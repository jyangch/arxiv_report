"""FastAPI web UI for the arxiv_report daily report generator."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from arxiv_report.render import REPORTS_DIR  # noqa: F401

app = FastAPI(title='arXiv astro-ph.HE Daily Report')
app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')
