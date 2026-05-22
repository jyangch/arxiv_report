"""FastAPI web UI for the arxiv_report daily report generator."""

import datetime
import re

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from arxiv_report.render import REPORTS_DIR  # noqa: F401

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _parse_date(s: str) -> datetime.date | None:
    """Strict YYYY-MM-DD parser. Returns None on any deviation.

    ``datetime.date.fromisoformat`` accepts `2026-05-22T12:00`-style
    suffixes — we reject those with an explicit regex pre-check so
    that URLs like ``/r/2026-05-22T12:00`` cannot smuggle anything
    extra past the filename concatenation.
    """
    if not _DATE_RE.match(s):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


app = FastAPI(title='arXiv astro-ph.HE Daily Report')
app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')
