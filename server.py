"""FastAPI web UI for the arxiv_report daily report generator."""

import asyncio
import datetime
import glob
import html as _html
import os
import re
import threading
import time
import uuid

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.fetcher import (
    ARXIV_COOLDOWN_PATH,
    ARXIV_COOLDOWN_SECONDS,
    ARXIV_TZ,
    fetch_arxiv_papers,
)
from core.providers import generate_report
from core.render import REPORTS_DIR, save_html

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


def _list_recent_dates(limit: int = 30) -> list[datetime.date]:
    """Return dates of existing reports, newest first, capped at ``limit``.

    Filenames not matching ``arXiv_astro_ph_HE_daily_report_YYYY-MM-DD.html``
    are silently ignored.
    """
    out: list[datetime.date] = []
    for path in glob.glob(os.path.join(REPORTS_DIR, 'arXiv_astro_ph_HE_daily_report_*.html')):
        m = _REPORT_FILENAME_RE.search(os.path.basename(path))
        if not m:
            continue
        d = _parse_date(m.group(1))
        if d:
            out.append(d)
    out.sort(reverse=True)
    return out[:limit]


def _report_path(date: datetime.date) -> str:
    """Filesystem path for the report HTML on this date."""
    return os.path.join(REPORTS_DIR, f'arXiv_astro_ph_HE_daily_report_{date.isoformat()}.html')


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
# }


def _worker(task_id: str, as_of: datetime.datetime, date_str: str) -> None:
    """Run the full fetch -> LLM -> save flow, updating ``_tasks[task_id]``.

    Each stage appends a one-line status message to ``task['messages']``
    so the SSE stream can surface progress incrementally. Exceptions in
    any stage land in ``task['error']`` and set ``status='error'``.
    """
    task = _tasks[task_id]
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
        task['status'] = 'done'
        task['messages'].append('No papers for this date (weekend / holiday / out of range).')
        return

    task['messages'].append('Calling LLM (may take several minutes)...')
    try:
        report, provider = generate_report(papers)
    except Exception as exc:
        task['error'] = str(exc)
        task['status'] = 'error'
        task['messages'].append(f'LLM failed: {exc}')
        return

    task['provider'] = provider
    task['messages'].append(f'Provider: {provider}')
    task['report_path'] = save_html(papers, report, provider, as_of=as_of)
    task['status'] = 'done'
    task['messages'].append('Done.')


app = FastAPI(title='arXiv astro-ph.HE Daily Report')
templates = Jinja2Templates(directory='templates')


def _render_home(
    request: Request, selected_date: datetime.date | None, main_content: str
) -> HTMLResponse:
    """Render ``home.html`` with the given main-area content."""
    return templates.TemplateResponse(
        request=request,
        name='home.html',
        context={
            'selected_date': selected_date,
            'main_content': main_content,
            'controls_html': _render_controls(selected_date),
        },
    )


def _render_partial(name: str, **ctx) -> str:
    """Render a partial template to a string (no Response wrapping)."""
    return templates.get_template(name).render(**ctx)


def _render_controls(selected_date: datetime.date | None) -> str:
    """Render the sidebar Generate-form fragment, accounting for cooldown."""
    return _render_partial(
        'partials/controls.html',
        selected_date=selected_date,
        cooldown_remaining=_arxiv_cooldown_remaining(),
    )


def _terminal_fragment(task: dict) -> str:
    """The HTML fragment shipped in the SSE 'done' event."""
    if task['status'] == 'error':
        return _render_partial(
            'partials/error_panel.html', message=task['error'] or 'Unknown error'
        )
    if task['report_path'] is None:
        return _render_partial('partials/empty_panel.html', date=task['date'])
    return _render_partial('partials/report_frame.html', date=task['date'])


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
        main = _render_partial('partials/report_frame.html', date=parsed.isoformat())
    else:
        main = _render_partial('partials/placeholder.html', date=parsed.isoformat())
    return _render_home(request, parsed, main)


@app.get('/r/{date}/raw')
def report_raw(date: str) -> FileResponse:
    """Serve the raw report HTML for the iframe ``src``."""
    parsed = _parse_date(date)
    if parsed is None:
        raise HTTPException(status_code=400, detail='Invalid date')
    path = _report_path(parsed)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail='Report not found')
    return FileResponse(path, media_type='text/html')


@app.post('/generate', response_class=HTMLResponse)
def generate(date: str = Form(...)) -> HTMLResponse:
    """Spawn a background generation task and return the running panel."""
    parsed = _parse_date(date)
    if parsed is None:
        raise HTTPException(status_code=400, detail='Invalid date')

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


@app.get('/recent', response_class=HTMLResponse)
def recent(active: str = '') -> HTMLResponse:
    """Sidebar fragment: list of recent report dates, descending."""
    dates = _list_recent_dates(limit=30)
    html = _render_partial('partials/recent_list.html', dates=dates, active=active)
    return HTMLResponse(html)


@app.get('/controls', response_class=HTMLResponse)
def controls(active: str = '') -> HTMLResponse:
    """Sidebar Generate-form fragment; polled to reflect cooldown state."""
    selected = _parse_date(active)
    return HTMLResponse(_render_controls(selected))
