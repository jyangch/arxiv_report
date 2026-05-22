"""FastAPI web UI for the arxiv_report daily report generator."""

import datetime
import glob
import os
import re

from fastapi import FastAPI
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


app = FastAPI(title='arXiv astro-ph.HE Daily Report')
app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')
