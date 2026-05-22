"""FastAPI web UI for the arxiv_report daily report generator."""

import datetime
import glob
import os
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from arxiv_report.render import REPORTS_DIR

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
        },
    )


def _render_partial(name: str, **ctx) -> str:
    """Render a partial template to a string (no Response wrapping)."""
    return templates.get_template(name).render(**ctx)


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


@app.get('/recent', response_class=HTMLResponse)
def recent(active: str = '') -> HTMLResponse:
    """Sidebar fragment: list of recent report dates, descending."""
    dates = _list_recent_dates(limit=30)
    html = _render_partial('partials/recent_list.html', dates=dates, active=active)
    return HTMLResponse(html)
