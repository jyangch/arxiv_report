"""arXiv ingestion: compute the daily submission window and pull astro-ph.HE papers."""

import datetime
from datetime import timedelta

import arxiv
import pytz

ARXIV_QUERY = 'cat:astro-ph.he'
ARXIV_MAX_RESULTS = 500
ARXIV_TZ = pytz.timezone('US/Eastern')


def get_arxiv_sync_window(as_of: datetime.datetime | None = None):
    """Return the (start, end) ET datetime window matching arXiv's daily release cadence.

    arXiv announces submissions at 14:00 ET each weekday. We define a window ending at
    14:00 ET of the most recent announcement day and starting just after the previous
    one (with a 3-day stretch on Mondays to cover the weekend gap). When ``as_of`` is
    provided, the window is computed relative to that timestamp rather than now.
    """
    now_et = as_of.astimezone(ARXIV_TZ) if as_of else datetime.datetime.now(ARXIV_TZ)
    weekday = now_et.weekday()
    if weekday == 0:
        days_back = 3
    elif weekday in (5, 6):
        days_back = 0
    else:
        days_back = 1

    end_time = now_et.replace(hour=14, minute=0, second=0, microsecond=0) - timedelta(days=1)
    start_time = end_time - timedelta(days=days_back)
    return start_time, end_time


def _fmt_arxiv_date(dt: datetime.datetime) -> str:
    """Format an aware datetime as UTC ``YYYYMMDDHHMM`` for arXiv submittedDate query."""
    return dt.astimezone(pytz.UTC).strftime('%Y%m%d%H%M')


def fetch_arxiv_papers(as_of: datetime.datetime | None = None):
    """Fetch and normalize papers in the sync window for ``as_of`` (defaults to now)."""
    start_t, end_t = get_arxiv_sync_window(as_of=as_of)
    print(f'🔍 Fetching arXiv announcements (Submission window: {start_t} -> {end_t} ET)')

    if start_t >= end_t:
        print('✅ Empty window (weekend); nothing to fetch.')
        return []

    query = (
        f'{ARXIV_QUERY} AND submittedDate:[{_fmt_arxiv_date(start_t)} TO {_fmt_arxiv_date(end_t)}]'
    )
    arxiv_client = arxiv.Client()
    search = arxiv.Search(
        query=query,
        max_results=ARXIV_MAX_RESULTS,
        sort_by=arxiv.SortCriterion.SubmittedDate,
    )

    papers = []
    for r in arxiv_client.results(search):
        pub_et = r.published.astimezone(ARXIV_TZ)
        if start_t <= pub_et < end_t:
            papers.append(
                {
                    'title': r.title,
                    'authors': ', '.join(a.name for a in r.authors),
                    'summary': r.summary,
                    'url': r.entry_id,
                    'pdf_url': r.pdf_url,
                    'categories': r.categories,
                    'comment': r.comment or '',
                    'journal_ref': r.journal_ref or '',
                    'doi': r.doi or '',
                }
            )

    print(f'✅ Found {len(papers)} papers submitted within the sync window.')
    return papers
