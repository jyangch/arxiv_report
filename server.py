"""FastAPI web UI for the arxiv_report daily report generator."""

import asyncio
from contextlib import closing, suppress
import datetime
import glob
import hmac
import html as _html
import ipaddress
import json
import os
import re
import threading
import time
import uuid

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from core import citations, corpus, obsidian, paper_state, rollups, sanity
from core.config import (
    ALLOWED_NETS,
    CLAUDE_BACKEND,
    FALLBACK_ORDER,
    GEMINI_BACKEND,
    OBSIDIAN_ARXIV_FOLDER,
    OBSIDIAN_VAULT_PATH,
    OPENAI_BACKEND,
    PREFERRED_PROVIDER,
    UI_TOKEN,
)
from core.fetcher import (
    ARXIV_COOLDOWN_PATH,
    ARXIV_COOLDOWN_SECONDS,
    ARXIV_TZ,
    describe_empty_window,
    fetch_arxiv_papers,
)
from core.providers import generate_report
from core.render import REPORTS_DIR, STARRED_REPORT, save_html
from core.topics import TOPICS

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_REPORT_FILENAME_RE = re.compile(r'arXiv_astro_ph_HE_daily_report_(\d{4}-\d{2}-\d{2})\.html$')


def _parse_date(s: str) -> datetime.date | None:
    """Strict YYYY-MM-DD parser. Returns None on any deviation.

    ``datetime.date.fromisoformat`` accepts `2026-05-22T12:00`-style
    suffixes -- we reject those with an explicit regex pre-check so
    that URLs like ``/r/2026-05-22T12:00`` cannot smuggle anything
    extra past the filename concatenation.
    """
    if not _DATE_RE.match(s):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


def _list_all_report_dates() -> list[str]:
    """Return all existing report dates formatted as ``YYYY-MM-DD``, newest first."""
    out: list[str] = []
    for path in glob.glob(os.path.join(REPORTS_DIR, 'arXiv_astro_ph_HE_daily_report_*.html')):
        m = _REPORT_FILENAME_RE.search(os.path.basename(path))
        if not m:
            continue
        d = _parse_date(m.group(1))
        if d:
            out.append(d.isoformat())
    out.sort(reverse=True)
    return out


def _list_recent_dates(limit: int = 30) -> list[datetime.date]:
    """Return dates of existing reports, newest first, capped at ``limit``.

    Filenames not matching ``arXiv_astro_ph_HE_daily_report_YYYY-MM-DD.html``
    are silently ignored.
    """
    return [datetime.date.fromisoformat(s) for s in _list_all_report_dates()[:limit]]


def _report_path(date: datetime.date) -> str:
    """Filesystem path for the report HTML on this date."""
    return os.path.join(REPORTS_DIR, f'arXiv_astro_ph_HE_daily_report_{date.isoformat()}.html')


def _file_version(path: str) -> str:
    """Cache-busting token for generated HTML iframe URLs."""
    try:
        return str(int(os.path.getmtime(path) * 1000))
    except OSError:
        return str(int(time.time() * 1000))


_NO_STORE_HEADERS = {
    'Cache-Control': 'no-store, max-age=0',
    'Pragma': 'no-cache',
    'Expires': '0',
}
_PAPER_STATE_JS = os.path.join('static', 'paper_state.js')


def _raw_html_response(path: str, can_write: bool = True) -> HTMLResponse:
    """Serve generated HTML without browser caching stale iframe content.

    The paper-state sync script is injected at the top of ``<head>`` so it
    runs before the page's own scripts read ``localStorage``. Injecting at
    serve time covers reports generated before the sync existed.
    """
    with open(path, encoding='utf-8-sig') as f:
        html = f.read()
    guard = f'<script>window.__ARXIV_CAN_WRITE__ = {"true" if can_write else "false"};</script>'
    tag = (
        f'{guard}<script src="/static/paper_state.js?v={_file_version(_PAPER_STATE_JS)}"></script>'
    )
    html, n = re.subn(r'<head[^>]*>', lambda m: m.group(0) + tag, html, count=1)
    if n == 0:
        html = tag + html
    return HTMLResponse(html, headers=_NO_STORE_HEADERS)


def _arxiv_cooldown_remaining() -> int:
    """Seconds until the arXiv rate-limit cooldown expires; 0 if not active.

    The cooldown file is written by ``core.fetcher`` whenever the arXiv API
    returns HTTP 429, regardless of whether the requesting window was recent
    or historical. While the cooldown is active, the UI disables Generate
    instead of letting the user hammer arXiv into a longer block.
    """
    try:
        with open(ARXIV_COOLDOWN_PATH) as f:
            until = float(f.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return 0
    return max(0, int(until - time.time()))


def _humanize_fetch_error(exc: Exception) -> str:
    """Trim ugly arXiv API exception text to something humans want to read."""
    msg = str(exc)
    if 'cooldown active' in msg.lower():
        return msg  # already formatted by core.fetcher
    if '429' in msg:
        mins = (ARXIV_COOLDOWN_SECONDS + 59) // 60
        return f'arXiv API is rate-limiting requests. Cooldown set; retry in up to {mins} min.'
    return msg


_tasks: dict[str, dict] = {}
# task_id -> {
#   'status':      'running' | 'done' | 'error',
#   'date':        'YYYY-MM-DD',
#   'messages':    list[str],
#   'report_path': str | None,
#   'provider':    str | None,
#   'error':       str | None,
#   'empty_reason': str | None,  # set when a 'done' task found no papers
# }


def _worker(task_id: str, as_of: datetime.datetime, date_str: str) -> None:
    """Run the full fetch -> LLM -> save flow, updating ``_tasks[task_id]``.

    Each stage appends a one-line status message to ``task['messages']``
    so the SSE stream can surface progress incrementally. Exceptions in
    any stage land in ``task['error']`` and set ``status='error'``.
    """
    task = _tasks[task_id]
    task['messages'].append(f'Task: {task_id[:8]} · Date: {date_str}')
    task['messages'].append(
        'Provider preference: '
        f'{PREFERRED_PROVIDER}; fallback: {", ".join(FALLBACK_ORDER) or "none"}; '
        f'Gemini backend: {GEMINI_BACKEND}; Claude backend: {CLAUDE_BACKEND}; '
        f'OpenAI backend: {OPENAI_BACKEND}'
    )
    task['messages'].append(f'Fetch anchor: {as_of.isoformat()}')
    task['messages'].append('Fetching arXiv papers...')
    try:
        papers = fetch_arxiv_papers(as_of=as_of)
    except Exception as exc:
        clean = _humanize_fetch_error(exc)
        task['error'] = clean
        task['status'] = 'error'
        task['messages'].append(f'Fetch failed: {clean}')
        return

    task['messages'].append(f'Found {len(papers)} papers')
    if not papers:
        reason = describe_empty_window(as_of=as_of)
        task['empty_reason'] = reason
        task['status'] = 'done'
        task['messages'].append(f'No papers: {reason}')
        return

    task['messages'].append('Calling LLM (may take several minutes)...')
    try:
        report, provider = generate_report(papers, on_progress=task['messages'].append)
    except Exception as exc:
        task['error'] = str(exc)
        task['status'] = 'error'
        task['messages'].append(f'LLM failed: {exc}')
        return

    task['provider'] = provider
    task['messages'].append(f'Provider: {provider}')
    task['report_path'] = save_html(papers, report, provider, as_of=as_of)
    task['messages'].append(f'Output: {task["report_path"]}')
    task['status'] = 'done'
    task['messages'].append('Done.')


app = FastAPI(title='arXiv astro-ph.HE Daily Report')
templates = Jinja2Templates(directory='templates')


def _is_local_owner_host(host: str) -> bool:
    if host in ('127.0.0.1', '::1', 'localhost', 'testclient'):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _can_write(request: Request) -> bool:
    """Return True if this request holds owner write authority."""
    token = UI_TOKEN
    if token:
        supplied = request.cookies.get('ui_token', '') or request.headers.get('X-UI-Token', '')
        return bool(supplied) and hmac.compare_digest(supplied, token)
    host = request.client.host if request.client else ''
    return _is_local_owner_host(host)


def _require_write(request: Request) -> None:
    if not _can_write(request):
        raise HTTPException(
            status_code=403,
            detail='Read-only mode for LAN visitors. Unlock owner session first.',
        )


@app.middleware('http')
async def _enforce_allowed_nets(request: Request, call_next):
    nets = ALLOWED_NETS
    if nets:
        host = request.client.host if request.client else ''
        check_ip = '127.0.0.1' if host in ('testclient', 'localhost') else host
        try:
            addr = ipaddress.ip_address(check_ip)
            parsed_nets = [ipaddress.ip_network(n, strict=False) for n in nets]
            if not any(addr in net for net in parsed_nets):
                return PlainTextResponse('Forbidden', status_code=403)
        except ValueError:
            return PlainTextResponse('Forbidden', status_code=403)
    return await call_next(request)


def _build_nav_context(
    selected_date: datetime.date | None,
    active_nav: str,
    rollup_kind: str = '',
    rollup_key: str = '',
) -> tuple[dict, str]:
    """Build navigation metadata and JSON payload for the topbar and calendar popover."""
    file_dates = _list_all_report_dates()
    file_set = set(file_dates)

    db_counts: dict[str, dict[str, int]] = {}
    db_file = corpus.default_db_path()
    if os.path.isfile(db_file):
        with suppress(Exception), closing(corpus.connect(db_file)) as conn:
            rows = conn.execute(
                'SELECT report_date, COUNT(*) AS total, SUM(has_report) AS digested '
                'FROM papers WHERE report_date != "" GROUP BY report_date'
            ).fetchall()
            for r in rows:
                d_str = str(r['report_date'] or '')
                if d_str:
                    db_counts[d_str] = {
                        'total': int(r['total'] or 0),
                        'digested': int(r['digested'] or 0),
                    }

    days_map: dict[str, dict] = {}
    weeks_map: dict[str, dict] = {}
    months_map: dict[str, dict] = {}
    years_map: dict[str, dict] = {}

    for d_str in sorted(file_set | set(db_counts), reverse=True):
        info = db_counts.get(d_str, {})
        cnt = int(info.get('digested') or info.get('total') or 0)
        tot = int(info.get('total') or cnt or 0)
        has_file = d_str in file_set
        w_key = ''
        with suppress(ValueError):
            w_key = rollups.iso_week_key(d_str)
        m_key = d_str[:7] if len(d_str) >= 7 else ''
        y_key = d_str[:4] if len(d_str) >= 4 else ''

        if has_file:
            days_map[d_str] = {
                'count': cnt,
                'total': tot,
                'has_file': True,
                'week': w_key,
                'month': m_key,
            }
        if w_key:
            wb = weeks_map.setdefault(
                w_key, {'count': 0, 'days': 0, 'span': rollups.format_period_label('week', w_key)}
            )
            wb['count'] += tot
            if has_file:
                wb['days'] += 1
        if m_key:
            mb = months_map.setdefault(
                m_key, {'count': 0, 'days': 0, 'label': rollups.format_period_label('month', m_key)}
            )
            mb['count'] += tot
            if has_file:
                mb['days'] += 1
        if y_key:
            yb = years_map.setdefault(y_key, {'count': 0, 'days': 0})
            yb['count'] += tot
            if has_file:
                yb['days'] += 1

    day_order = sorted(days_map.keys(), reverse=True)
    week_order = sorted(weeks_map.keys(), reverse=True)
    month_order = sorted(months_map.keys(), reverse=True)
    year_order = sorted(years_map.keys(), reverse=True)

    latest_day = day_order[0] if day_order else ''
    latest_week = week_order[0] if week_order else ''
    latest_month = month_order[0] if month_order else ''
    latest_year = year_order[0] if year_order else ''

    if active_nav == 'summary' and rollup_kind in ('week', 'month', 'year'):
        nav_mode = rollup_kind
    elif active_nav == 'report':
        nav_mode = 'day'
    else:
        nav_mode = 'tool'

    current_key = ''
    current_label = ''
    current_count = 0
    prev_href = ''
    next_href = ''
    latest_href = ''
    standalone_href = ''

    if nav_mode == 'day':
        current_key = selected_date.isoformat() if selected_date else latest_day
        if current_key:
            try:
                dt = datetime.date.fromisoformat(current_key)
                current_label = f'{current_key} · {dt:%a}'
            except ValueError:
                current_label = current_key
            dinfo = days_map.get(current_key)
            if dinfo:
                current_count = int(dinfo.get('count') or dinfo.get('total') or 0)
            if current_key in day_order:
                idx = day_order.index(current_key)
                if idx + 1 < len(day_order):
                    prev_href = f'/r/{day_order[idx + 1]}'
                if idx > 0:
                    next_href = f'/r/{day_order[idx - 1]}'
            if latest_day and current_key != latest_day:
                latest_href = f'/r/{latest_day}'
            if selected_date and os.path.exists(_report_path(selected_date)):
                standalone_href = f'/r/{current_key}/raw'
        else:
            current_label = 'No reports yet'
    elif nav_mode in ('week', 'month', 'year'):
        order_map = {'week': week_order, 'month': month_order, 'year': year_order}
        latest_map = {'week': latest_week, 'month': latest_month, 'year': latest_year}
        counts_map = {'week': weeks_map, 'month': months_map, 'year': years_map}
        order = order_map[nav_mode]
        current_key = rollup_key or latest_map[nav_mode]
        current_label = rollups.format_period_label(nav_mode, current_key) or nav_mode.title()
        current_count = int(counts_map[nav_mode].get(current_key, {}).get('count') or 0)
        if current_key in order:
            idx = order.index(current_key)
            if idx + 1 < len(order):
                prev_href = f'/summary/{nav_mode}/{order[idx + 1]}'
            if idx > 0:
                next_href = f'/summary/{nav_mode}/{order[idx - 1]}'
        if latest_map[nav_mode] and current_key != latest_map[nav_mode]:
            latest_href = f'/summary/{nav_mode}/{latest_map[nav_mode]}'
    else:
        tool_titles = {
            'search': 'Search Corpus',
            'similar': 'Similar Papers',
            'recommend': 'Recommended Papers',
            'starred': 'Starred Papers',
            'unlock': 'Owner Access',
        }
        current_label = tool_titles.get(active_nav, 'Archive')
        if active_nav == 'starred':
            standalone_href = '/starred/raw'

    nav = {
        'mode': nav_mode,
        'active_nav': active_nav,
        'current_key': current_key,
        'current_label': current_label,
        'current_count': current_count,
        'prev_href': prev_href,
        'next_href': next_href,
        'latest_href': latest_href,
        'standalone_href': standalone_href,
        'latest_day': latest_day,
        'latest_week': latest_week,
        'latest_month': latest_month,
        'latest_year': latest_year,
        'days': days_map,
        'weeks': weeks_map,
        'months': months_map,
        'years': years_map,
        'day_order': day_order,
        'week_order': week_order,
        'month_order': month_order,
        'year_order': year_order,
    }
    nav_json = json.dumps(
        {
            'mode': nav_mode,
            'currentKey': current_key,
            'latestDay': latest_day,
            'latestWeek': latest_week,
            'latestMonth': latest_month,
            'latestYear': latest_year,
            'days': days_map,
            'weeks': weeks_map,
            'months': months_map,
            'years': years_map,
            'dayOrder': day_order,
            'weekOrder': week_order,
            'monthOrder': month_order,
            'yearOrder': year_order,
        },
        ensure_ascii=False,
    ).replace('</', '<\\/')
    return nav, nav_json


def _render_home(
    request: Request,
    selected_date: datetime.date | None,
    main_content: str,
    active_nav: str = 'report',
    page_title: str | None = None,
    rollup_kind: str = '',
    rollup_key: str = '',
) -> HTMLResponse:
    """Render ``home.html`` with the given main-area content."""
    can_write = _can_write(request)
    token_configured = bool(UI_TOKEN)
    nav, nav_json = _build_nav_context(
        selected_date=selected_date,
        active_nav=active_nav,
        rollup_kind=rollup_kind,
        rollup_key=rollup_key,
    )
    return templates.TemplateResponse(
        request=request,
        name='home.html',
        context={
            'selected_date': selected_date,
            'main_content': main_content,
            'active_nav': active_nav,
            'page_title': page_title,
            'quick_q': request.query_params.get('q', ''),
            'can_write': can_write,
            'token_configured': token_configured,
            'paper_state_v': _file_version(_PAPER_STATE_JS),
            'ui_shell_v': _file_version(os.path.join('static', 'ui_shell.js')),
            'nav': nav,
            'nav_json': nav_json,
            'controls_html': _render_controls(
                selected_date,
                can_write=can_write,
                token_configured=token_configured,
            ),
        },
        headers=_NO_STORE_HEADERS,
    )


def _render_partial(name: str, **ctx) -> str:
    """Render a partial template to a string (no Response wrapping)."""
    return templates.get_template(name).render(**ctx)


def _render_controls(
    selected_date: datetime.date | None,
    *,
    can_write: bool = True,
    token_configured: bool = False,
) -> str:
    """Render the topbar Generate-form fragment, accounting for cooldown and write mode."""
    today_et = datetime.datetime.now(ARXIV_TZ).date().isoformat()
    return _render_partial(
        'partials/controls.html',
        selected_date=selected_date,
        today_et=today_et,
        cooldown_remaining=_arxiv_cooldown_remaining(),
        can_write=can_write,
        token_configured=token_configured,
    )


def _terminal_fragment(task: dict) -> str:
    """The HTML fragment shipped in the SSE 'done' event."""
    if task['status'] == 'error':
        return _render_partial(
            'partials/error_panel.html', message=task['error'] or 'Unknown error'
        )
    if task['report_path'] is None:
        return _render_partial(
            'partials/empty_panel.html',
            date=task['date'],
            reason=task.get('empty_reason'),
        )
    return _render_partial(
        'partials/report_frame.html',
        date=task['date'],
        raw_version=_file_version(task['report_path']),
    )


def _sse_pack_fragment(event: str, html: str) -> str:
    """Encode a possibly-multiline HTML fragment as a single SSE event.

    Each newline becomes a separate ``data:`` line; the browser
    reassembles them with newline separators back into the same HTML.
    """
    lines = html.replace('\r\n', '\n').split('\n')
    data_block = '\n'.join(f'data: {line}' for line in lines)
    return f'event: {event}\n{data_block}\n\n'


app.mount('/static', StaticFiles(directory='static'), name='static')


@app.get('/', response_model=None)
def index(request: Request) -> HTMLResponse | RedirectResponse:
    """Redirect to the latest report, or render a placeholder if none exist."""
    dates = _list_recent_dates(limit=1)
    if dates:
        return RedirectResponse(url=f'/r/{dates[0].isoformat()}')
    main = _render_partial('partials/placeholder.html', date='any date')
    return _render_home(request, None, main)


@app.get('/r/{date}', response_class=HTMLResponse)
def report_page(date: str, request: Request) -> HTMLResponse:
    """Main page for a specific date. Renders even when the file is missing."""
    parsed = _parse_date(date)
    if parsed is None:
        raise HTTPException(status_code=400, detail='Invalid date')
    path = _report_path(parsed)
    if os.path.exists(path):
        main = _render_partial(
            'partials/report_frame.html',
            date=parsed.isoformat(),
            raw_version=_file_version(path),
        )
    else:
        main = _render_partial('partials/placeholder.html', date=parsed.isoformat())
    return _render_home(request, parsed, main)


@app.get('/r/{date}/raw')
def report_raw(date: str, request: Request) -> HTMLResponse:
    """Serve the raw report HTML for the iframe ``src``."""
    parsed = _parse_date(date)
    if parsed is None:
        raise HTTPException(status_code=400, detail='Invalid date')
    path = _report_path(parsed)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail='Report not found')
    return _raw_html_response(path, can_write=_can_write(request))


@app.get('/starred', response_class=HTMLResponse)
def starred_page(request: Request) -> HTMLResponse:
    """Render the main page for the starred / Obsidian collection."""
    path = os.path.join(REPORTS_DIR, STARRED_REPORT)
    main = _render_partial('partials/starred_frame.html', raw_version=_file_version(path))
    return _render_home(request, None, main, active_nav='starred', page_title='Starred paper list')


@app.get('/starred/raw')
def starred_raw(request: Request) -> HTMLResponse:
    """Serve the standalone starred / Obsidian collection page."""
    path = os.path.join(REPORTS_DIR, STARRED_REPORT)
    if not os.path.exists(path):
        from core.render import _write_starred_html

        _write_starred_html(provider='local')
    return _raw_html_response(path, can_write=_can_write(request))


@app.get('/api/session')
def session_status(request: Request) -> JSONResponse:
    """Report current visitor write capability and token lock configuration."""
    return JSONResponse(
        {'can_write': _can_write(request), 'token_configured': bool(UI_TOKEN)},
        headers=_NO_STORE_HEADERS,
    )


@app.get('/unlock', response_model=None)
def unlock_page(request: Request, token: str = ''):
    """Render owner unlock form or accept ``?token=...`` shortcut."""
    if token and UI_TOKEN and hmac.compare_digest(token, UI_TOKEN):
        resp = RedirectResponse(url='/', status_code=303)
        resp.set_cookie('ui_token', token, httponly=True, samesite='lax', max_age=90 * 86400)
        return resp
    main = _render_partial(
        'partials/unlock_panel.html',
        token_configured=bool(UI_TOKEN),
        can_write=_can_write(request),
        error='',
    )
    return _render_home(request, None, main, active_nav='unlock', page_title='Unlock Owner Mode')


@app.post('/unlock', response_model=None)
def unlock_submit(request: Request, token: str = Form(...)):
    """Validate owner token form submission and set auth cookie."""
    if not UI_TOKEN or not hmac.compare_digest(token.strip(), UI_TOKEN):
        main = _render_partial(
            'partials/unlock_panel.html',
            token_configured=bool(UI_TOKEN),
            can_write=False,
            error='Invalid token (or UI_TOKEN is not configured on the server).'
            if not UI_TOKEN
            else 'Invalid owner token.',
        )
        return _render_home(
            request, None, main, active_nav='unlock', page_title='Unlock Owner Mode'
        )
    resp = RedirectResponse(url='/', status_code=303)
    resp.set_cookie('ui_token', token.strip(), httponly=True, samesite='lax', max_age=90 * 86400)
    return resp


@app.post('/lock')
def lock_session() -> RedirectResponse:
    """Clear the owner unlock cookie."""
    resp = RedirectResponse(url='/', status_code=303)
    resp.delete_cookie('ui_token')
    return resp


@app.get('/search', response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = '',
    days: int = 0,
    topic: str = '',
    method: str = '',
    status: str = '',
    sort: str = 'relevance',
) -> HTMLResponse:
    """Cross-day bilingual corpus search page."""
    result = sanity.search_papers(
        q,
        days=days,
        topic=topic,
        method=method,
        status=status,
        sort=sort,
        limit=100,
    )
    main = _render_partial(
        'partials/search_panel.html',
        result=result,
        days=days,
        topic=topic,
        method=method,
        status=status,
        sort=sort,
        topics=TOPICS,
    )
    return _render_home(request, None, main, active_nav='search', page_title='Search Corpus')


@app.get('/api/search')
def search_api(
    q: str = '',
    days: int = 0,
    topic: str = '',
    method: str = '',
    status: str = '',
    sort: str = 'relevance',
    limit: int = 100,
) -> JSONResponse:
    """JSON endpoint for bilingual corpus search."""
    res = sanity.search_papers(
        q,
        days=days,
        topic=topic,
        method=method,
        status=status,
        sort=sort,
        limit=limit,
    )
    return JSONResponse(res, headers=_NO_STORE_HEADERS)


@app.get('/similar/{arxiv_id:path}', response_class=HTMLResponse)
def similar_page(request: Request, arxiv_id: str, days: int = 0) -> HTMLResponse:
    """Display papers most similar to ``arxiv_id`` using TF-IDF cosine similarity."""
    res = sanity.similar_papers(arxiv_id, days=days, limit=40)
    main = _render_partial(
        'partials/similar_panel.html',
        arxiv_id=arxiv_id,
        days=days,
        result=res,
    )
    return _render_home(
        request, None, main, active_nav='similar', page_title=f'Similar to {arxiv_id}'
    )


class RecommendRequest(BaseModel):
    """Request payload for client-side positive-ID recommendations."""

    pids: list[str] = []
    days: int = 30
    exclude_positives: bool = True
    limit: int = 60


@app.get('/recommend', response_class=HTMLResponse)
def recommend_page(
    request: Request,
    source: str = 'all',
    days: int = 30,
    exclude: int = 1,
) -> HTMLResponse:
    """Personalized LinearSVC recommendation feed."""
    can_write = _can_write(request)
    visitor_mode = (not can_write) or source == 'browser'
    if visitor_mode:
        pos_info = {
            'source': 'browser',
            'starred_count': 0,
            'obsidian_count': 0,
            'combined_count': 0,
        }
        rec = None
    else:
        pos_info = sanity.collect_owner_positives(source=source)
        rec = sanity.recommend_papers(
            pos_info['selected'],
            source=pos_info['source'],
            days=days,
            exclude_positives=bool(exclude),
            limit=60,
        )
    main = _render_partial(
        'partials/recommend_panel.html',
        visitor_mode=visitor_mode,
        can_write=can_write,
        source=pos_info['source'],
        days=days,
        exclude=bool(exclude),
        pos_info=pos_info,
        rec=rec,
    )
    return _render_home(
        request, None, main, active_nav='recommend', page_title='Recommended Papers'
    )


@app.post('/api/recommend')
def recommend_api(req: RecommendRequest) -> JSONResponse:
    """Return personalized recommendations as JSON from explicit arXiv IDs."""
    res = sanity.recommend_papers(
        req.pids,
        days=req.days,
        exclude_positives=req.exclude_positives,
        limit=req.limit,
    )
    return JSONResponse(res, headers=_NO_STORE_HEADERS)


@app.post('/api/recommend/cards', response_class=HTMLResponse)
def recommend_cards_html(req: RecommendRequest) -> HTMLResponse:
    """Render personalized recommendation cards HTML for LAN visitor localStorage."""
    res = sanity.recommend_papers(
        req.pids,
        days=req.days,
        exclude_positives=req.exclude_positives,
        limit=req.limit,
    )
    html = _render_partial('partials/recommend_results.html', rec=res, visitor_mode=True)
    return HTMLResponse(html, headers=_NO_STORE_HEADERS)


@app.get('/summary', response_class=HTMLResponse)
@app.get('/summary/{kind}/{key}', response_class=HTMLResponse)
def summary_page(request: Request, kind: str = 'month', key: str = '') -> HTMLResponse:
    """Render week, month, or year rollup statistics and top papers."""
    if kind not in ('week', 'month', 'year'):
        raise HTTPException(status_code=400, detail='Invalid rollup period kind')
    data = rollups.build_rollup(kind=kind, key=key)
    main = _render_partial('partials/summary_panel.html', rollup=data)
    title = f'{kind.title()} Rollup · {data["key"]}' if data['key'] else 'Period Rollups'
    return _render_home(
        request,
        None,
        main,
        active_nav='summary',
        page_title=title,
        rollup_kind=kind,
        rollup_key=data['key'],
    )


@app.get('/api/summary')
def summary_api(kind: str = 'month', key: str = '') -> JSONResponse:
    """Return JSON week, month, or year rollup aggregation."""
    if kind not in ('week', 'month', 'year'):
        raise HTTPException(status_code=400, detail='Invalid rollup period kind')
    data = rollups.build_rollup(kind=kind, key=key)
    return JSONResponse(data, headers=_NO_STORE_HEADERS)


class CitationRefreshRequest(BaseModel):
    """Parameters for owner-initiated citation refresh requests."""

    days: int = 30
    max_requests: int = 10
    provider: str = 'auto'


@app.post('/api/citations/refresh')
async def citations_refresh_api(request: Request) -> JSONResponse:
    """Refresh NASA SciX / INSPIRE-HEP citation counts for owner sessions."""
    _require_write(request)
    days = 30
    max_requests = 10
    provider = 'auto'
    try:
        raw_body = await request.body()
        if raw_body:
            payload = CitationRefreshRequest.model_validate_json(raw_body)
            days = payload.days
            max_requests = payload.max_requests
            provider = payload.provider
    except Exception:
        pass
    if 'days' in request.query_params:
        days = int(request.query_params['days'])
    if 'max_requests' in request.query_params:
        max_requests = int(request.query_params['max_requests'])
    if 'provider' in request.query_params:
        provider = request.query_params['provider']

    try:
        res = citations.refresh_citations(
            provider=provider,
            days=days,
            max_requests=max_requests,
        )
    except citations.CitationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(res, headers=_NO_STORE_HEADERS)


@app.get('/api/paper-state')
def paper_state_get(request: Request) -> JSONResponse:
    """Return every stored starred / Obsidian key with its paper snapshot."""
    if not _can_write(request):
        return JSONResponse({}, headers=_NO_STORE_HEADERS)
    return JSONResponse(paper_state.load_state(paper_state.STATE_PATH), headers=_NO_STORE_HEADERS)


class PaperStatePatch(BaseModel):
    """Represent one batch of ``localStorage`` writes mirrored from the browser."""

    set: dict[str, str] = {}
    remove: list[str] = []


@app.post('/api/paper-state')
def paper_state_post(patch: PaperStatePatch, request: Request) -> JSONResponse:
    """Apply mirrored starred / Obsidian writes and report the stored key count."""
    _require_write(request)
    state = paper_state.apply_changes(patch.set, patch.remove, paper_state.STATE_PATH)
    return JSONResponse({'count': len(state)}, headers=_NO_STORE_HEADERS)


class ObsidianSaveRequest(BaseModel):
    """Represent paper metadata submitted to create or append an Obsidian note."""

    arxiv_id: str
    url: str = ''
    title_en: str = ''
    title_zh: str = ''
    authors: str = ''
    question: str = ''
    method: str = ''
    method_tag: str = ''
    result: str = ''
    caveat: str = ''
    paper_status: str = 'submitted'
    report_date: str = ''
    note: str = ''


@app.post('/api/obsidian/save')
def obsidian_save(req: ObsidianSaveRequest, request: Request) -> JSONResponse:
    """Write or safely append a paper note in the local Obsidian vault folder."""
    _require_write(request)
    try:
        result = obsidian.save_obsidian_note(
            req.model_dump(),
            vault_path=OBSIDIAN_VAULT_PATH,
            folder_name=OBSIDIAN_ARXIV_FOLDER,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f'Obsidian write failed: {exc}') from exc
    return JSONResponse(
        {
            'status': result['status'],
            'filename': result['filename'],
            'markdown': result['markdown'],
        },
        headers=_NO_STORE_HEADERS,
    )


@app.get('/api/obsidian/saved')
def obsidian_saved(request: Request) -> JSONResponse:
    """Return sorted arXiv IDs already saved in the local Obsidian folder."""
    if not _can_write(request):
        return JSONResponse({'arxiv_ids': []}, headers=_NO_STORE_HEADERS)
    ids = obsidian.list_saved_arxiv_ids(OBSIDIAN_VAULT_PATH, OBSIDIAN_ARXIV_FOLDER)
    return JSONResponse({'arxiv_ids': ids}, headers=_NO_STORE_HEADERS)


@app.post('/generate', response_class=HTMLResponse)
def generate(request: Request, date: str = Form(...), force: str = Form('')) -> HTMLResponse:
    """Spawn a background generation task and return the running panel."""
    _require_write(request)
    parsed = _parse_date(date)
    if parsed is None:
        raise HTTPException(status_code=400, detail='Invalid date')

    existing_path = _report_path(parsed)
    if os.path.exists(existing_path) and force.strip().lower() not in ('1', 'true', 'yes'):
        return HTMLResponse(
            _render_partial('partials/generate_exists_panel.html', date=parsed.isoformat())
        )

    cooldown_s = _arxiv_cooldown_remaining()
    if cooldown_s > 0:
        mins = (cooldown_s + 59) // 60
        msg = f'arXiv API is rate-limiting requests. Cooldown active; retry in ~{mins} min.'
        return HTMLResponse(_render_partial('partials/error_panel.html', message=msg))

    as_of = ARXIV_TZ.localize(datetime.datetime.combine(parsed, datetime.time(hour=12)))
    task_id = str(uuid.uuid4())
    _tasks[task_id] = {
        'status': 'running',
        'date': parsed.isoformat(),
        'messages': [],
        'report_path': None,
        'provider': None,
        'error': None,
    }
    threading.Thread(target=_worker, args=(task_id, as_of, parsed.isoformat()), daemon=True).start()
    html = _render_partial('partials/status_panel.html', task_id=task_id)
    return HTMLResponse(html)


@app.get('/generate/stream/{task_id}')
async def generate_stream(task_id: str, request: Request) -> StreamingResponse:
    """SSE: stream worker log lines, then a single 'done' event with the result."""

    async def gen():
        sent = 0
        while True:
            if await request.is_disconnected():
                break
            task = _tasks.get(task_id)
            if task is None:
                yield _sse_pack_fragment(
                    'done',
                    '<div class="alert alert-error">Task not found.</div>',
                )
                return
            while sent < len(task['messages']):
                safe = _html.escape(task['messages'][sent])
                sent += 1
                yield f'data: <div class="log-line">{safe}</div>\n\n'
            if task['status'] in ('done', 'error'):
                yield _sse_pack_fragment('done', _terminal_fragment(task))
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        gen(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.get('/controls', response_class=HTMLResponse)
def controls(request: Request, active: str = '') -> HTMLResponse:
    """Topbar Generate-form fragment; polled to reflect cooldown state."""
    selected = _parse_date(active)
    return HTMLResponse(
        _render_controls(
            selected,
            can_write=_can_write(request),
            token_configured=bool(UI_TOKEN),
        )
    )
