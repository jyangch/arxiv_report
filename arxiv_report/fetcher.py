"""arXiv ingestion: compute the daily submission window and pull astro-ph.HE papers."""

import datetime
from datetime import timedelta

import arxiv
import pytz

ARXIV_QUERY = 'cat:astro-ph.he'
ARXIV_MAX_RESULTS = 500
ARXIV_TZ = pytz.timezone('US/Eastern')


def get_arxiv_sync_window(
    as_of: datetime.datetime | None = None,
) -> tuple[datetime.datetime, datetime.datetime]:
    """Compute the (start, end) ET window matching arXiv's daily release cadence.

    arXiv announces submissions at 14:00 ET each weekday. The window ends at
    14:00 ET on the day before ``as_of`` and starts 1 (or 3 on Mondays) days
    earlier to cover the weekend gap. On weekends ``start == end``.

    Args:
        as_of: Reference timestamp; defaults to ``now`` in the Eastern timezone.

    Returns:
        A pair of timezone-aware ET datetimes ``(start, end)``.
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


def fetch_arxiv_papers(as_of: datetime.datetime | None = None) -> list[dict]:
    """Fetch arXiv astro-ph.HE papers within the sync window.

    Issues a ``submittedDate`` range query so any historical date works, then
    re-filters in Python to guard against timezone-interpretation drift between
    client and server.

    Args:
        as_of: Reference timestamp; defaults to ``now`` in the Eastern timezone.

    Returns:
        A list of paper dicts keyed by ``title``, ``authors``, ``summary``,
        ``url``, ``pdf_url``, ``categories``, ``comment``, ``journal_ref``,
        ``doi``. Empty when the window is empty (weekend / no submissions).
    """
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
