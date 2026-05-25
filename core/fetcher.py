"""arXiv ingestion: compute the daily submission window and pull astro-ph.HE papers."""

import concurrent.futures
import datetime
from datetime import timedelta
from html import unescape as _html_unescape
import json
import os
import re
import time
from urllib.error import HTTPError as _UrllibHTTPError
import urllib.request

import arxiv
import feedparser
import holidays
import pytz

ARXIV_QUERY = 'cat:astro-ph.he'
ARXIV_MAX_RESULTS = 500
ARXIV_TZ = pytz.timezone('US/Eastern')
ARXIV_RSS_URL = 'http://export.arxiv.org/rss/astro-ph.HE'
ARXIV_CACHE_DIR = './reports/.cache'
ARXIV_COOLDOWN_PATH = './reports/.cache/arxiv_api_cooldown'
ARXIV_COOLDOWN_SECONDS = 30 * 60  # after a 429, skip the api for 30 minutes

# Federal holidays arXiv actually defers announcements for. The `holidays`
# library's US set also includes Washington's Birthday, Memorial Day, Columbus
# Day, and Veterans Day, which arXiv does NOT observe -- so lookups are filtered
# to this allowlist. Names match the holidays library's US labels (with any
# `(observed)` suffix stripped). arXiv's ad-hoc year-end closures (e.g. Dec 29)
# are not derivable from the library and are intentionally not modelled.
_ARXIV_OBSERVED_HOLIDAYS = frozenset({
    "New Year's Day",
    'Martin Luther King Jr. Day',
    'Juneteenth National Independence Day',
    'Independence Day',
    'Labor Day',
    'Thanksgiving Day',
    'Christmas Day',
})

# Abs-page metadata extraction patterns (see arxiv.org/abs/<id> markup).
_ARXIV_ID_RE = re.compile(r'/abs/([^/?#]+?)(?:v\d+)?/?(?:[?#]|$)')
_ABS_COMMENTS_RE = re.compile(r'<td class="tablecell comments[^"]*">([^<]+)</td>', re.IGNORECASE)
_ABS_JREF_RE = re.compile(r'<td class="tablecell jref">([^<]+)</td>', re.IGNORECASE)
_ABS_DOI_RE = re.compile(
    r'<td class="tablecell doi">.*?data-doi="([^"]+)"', re.IGNORECASE | re.DOTALL
)


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


def _arxiv_holiday_name(day: datetime.date) -> str | None:
    """Return the arXiv-observed holiday name for ``day``, or ``None``.

    Wraps ``holidays.US`` but filters to the federal holidays arXiv actually
    closes for (``_ARXIV_OBSERVED_HOLIDAYS``); other US holidays such as
    Memorial Day return ``None`` because arXiv still announces on them.

    Args:
        day: Calendar date to classify.

    Returns:
        The holiday name (possibly with an ``(observed)`` suffix) when arXiv
        defers announcements that day, else ``None``.
    """
    name = holidays.US(years=day.year).get(day)
    if name and name.removesuffix(' (observed)') in _ARXIV_OBSERVED_HOLIDAYS:
        return name
    return None


def describe_empty_window(as_of: datetime.datetime | None = None) -> str:
    """Explain why the announcement date for ``as_of`` yielded no papers.

    Classifies the announcement day (``as_of`` in ET) as a weekend, a known
    arXiv holiday, or an unexpected weekday miss. Papers are queried by
    submission window, so a real holiday still has submissions to announce;
    an empty weekday result almost always means a transient fetch issue.

    Args:
        as_of: Reference timestamp; defaults to ``now`` in the Eastern timezone.

    Returns:
        A one-line, human-readable reason suitable for UI display.
    """
    now_et = as_of.astimezone(ARXIV_TZ) if as_of else datetime.datetime.now(ARXIV_TZ)
    day = now_et.date()
    if day.weekday() >= 5:
        return f'{day:%A} {day:%Y-%m-%d} is a weekend; arXiv does not announce on weekends.'
    holiday = _arxiv_holiday_name(day)
    if holiday:
        return f'{day:%Y-%m-%d} is {holiday}; arXiv defers announcements on this holiday.'
    return (
        f'No papers came back for {day:%Y-%m-%d}. That is unexpected for a weekday -- '
        'arXiv was likely rate-limiting the fetch. Try generating again in a few minutes.'
    )


def _fmt_arxiv_date(dt: datetime.datetime) -> str:
    """Format an aware datetime as UTC ``YYYYMMDDHHMM`` for arXiv submittedDate query."""
    return dt.astimezone(pytz.UTC).strftime('%Y%m%d%H%M')


def _cache_path(start_t: datetime.datetime, end_t: datetime.datetime) -> str:
    """Build the per-window cache file path."""
    key = f'{start_t.strftime("%Y%m%dT%H%M")}_{end_t.strftime("%Y%m%dT%H%M")}'
    return os.path.join(ARXIV_CACHE_DIR, f'arxiv_{key}.json')


def _load_cache(start_t: datetime.datetime, end_t: datetime.datetime) -> list[dict] | None:
    """Return cached papers for this window, or None if absent/unreadable."""
    p = _cache_path(start_t, end_t)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(start_t: datetime.datetime, end_t: datetime.datetime, papers: list[dict]) -> None:
    """Persist papers for this window so same-day re-runs hit the cache."""
    os.makedirs(ARXIV_CACHE_DIR, exist_ok=True)
    with open(_cache_path(start_t, end_t), 'w', encoding='utf-8') as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)


def _extract_arxiv_id(url: str) -> str | None:
    """Extract the bare arXiv id (no version suffix) from an abs URL or entry id."""
    m = _ARXIV_ID_RE.search(url or '')
    return m.group(1) if m else None


def _fetch_abs_metadata(arxiv_id: str, timeout: float = 10.0) -> tuple[str, str, str]:
    """Scrape (comment, journal_ref, doi) from arxiv.org/abs/<id>.

    Used to backfill fields missing in the RSS fallback. arxiv.org/abs is on
    different infrastructure than export.arxiv.org/api and rarely 429s.
    """
    url = f'https://arxiv.org/abs/{arxiv_id}'
    req = urllib.request.Request(url, headers={'User-Agent': 'arxiv-report/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode('utf-8', errors='replace')

    def first(regex: re.Pattern) -> str:
        m = regex.search(html)
        return _html_unescape(m.group(1)).strip() if m else ''

    return first(_ABS_COMMENTS_RE), first(_ABS_JREF_RE), first(_ABS_DOI_RE)


def _enrich_papers(papers: list[dict], max_workers: int = 5) -> None:
    """Fill missing ``comment / journal_ref / doi`` on each paper via abs pages.

    Mutates ``papers`` in place. Individual failures are logged and skipped.
    """

    def enrich_one(paper: dict) -> None:
        arxiv_id = _extract_arxiv_id(paper.get('url', ''))
        if not arxiv_id:
            return
        try:
            c, j, d = _fetch_abs_metadata(arxiv_id)
        except Exception as e:
            print(f'  ⚠️  abs-page fetch failed for {arxiv_id}: {e}')
            return
        paper['comment'] = c
        paper['journal_ref'] = j
        paper['doi'] = d

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(enrich_one, papers))


def _is_in_cooldown() -> bool:
    """True if a recent 429 marked the api as off-limits for now."""
    try:
        return time.time() < float(open(ARXIV_COOLDOWN_PATH).read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return False


def _mark_cooldown() -> None:
    """Record that the api is off-limits until now + ARXIV_COOLDOWN_SECONDS."""
    os.makedirs(ARXIV_CACHE_DIR, exist_ok=True)
    until = time.time() + ARXIV_COOLDOWN_SECONDS
    with open(ARXIV_COOLDOWN_PATH, 'w') as f:
        f.write(str(until))


def _fetch_via_api(start_t: datetime.datetime, end_t: datetime.datetime) -> list[dict]:
    """Fetch papers via arXiv's Atom query API, filtering by submission time.

    Fail-fast: a single 429 propagates immediately so the caller can fall back
    to RSS within seconds rather than spending minutes on retries that almost
    never clear the rolling rate-limit window anyway.
    """
    query = (
        f'{ARXIV_QUERY} AND submittedDate:[{_fmt_arxiv_date(start_t)} TO {_fmt_arxiv_date(end_t)}]'
    )
    arxiv_client = arxiv.Client(
        page_size=50,
        delay_seconds=3.0,
        num_retries=1,
    )
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
    return papers


def _fetch_via_rss() -> list[dict]:
    """Fetch papers from arXiv's astro-ph.HE RSS feed (latest announcement only).

    The RSS endpoint is far less rate-limited than the query API but only
    exposes the most recent announcement, so this is suitable as a fallback
    when the caller wants ``as_of == today`` and the API is 429-ing.

    Only ``announce_type == 'new'`` items are returned — replacements and
    cross-listings are excluded to mirror the api path's ``submittedDate``
    semantics.
    """
    print(f'⚠️  Falling back to RSS feed: {ARXIV_RSS_URL}')
    feed = feedparser.parse(ARXIV_RSS_URL)
    if feed.bozo and not feed.entries:
        raise RuntimeError(f'RSS parse failed: {feed.bozo_exception}')

    papers = []
    for entry in feed.entries:
        announce_type = (entry.get('arxiv_announce_type') or '').lower()
        if announce_type and announce_type != 'new':
            continue

        link = entry.get('link', '') or entry.get('id', '')
        pdf_url = ''
        for lnk in entry.get('links', []) or []:
            if lnk.get('type') == 'application/pdf':
                pdf_url = lnk.get('href', '')
                break
        if not pdf_url and '/abs/' in link:
            pdf_url = link.replace('/abs/', '/pdf/')

        # New RSS format puts authors as a comma-separated string in
        # ``dc:creator``; feedparser surfaces it as ``entry.author`` or
        # populates ``entry.authors`` with one entry containing the joined name.
        if entry.get('authors'):
            authors = ', '.join(a.get('name', '') for a in entry.authors if a.get('name'))
        else:
            authors = entry.get('author', '')

        papers.append(
            {
                'title': entry.get('title', '').replace('\n', ' ').strip(),
                'authors': authors,
                'summary': entry.get('summary', ''),
                'url': link,
                'pdf_url': pdf_url,
                'categories': [t['term'] for t in entry.get('tags', []) if t.get('term')],
                'comment': '',
                'journal_ref': '',
                'doi': '',
            }
        )

    if papers:
        print(f'🌐 Enriching {len(papers)} papers from abs pages…')
        _enrich_papers(papers)
    return papers


def fetch_arxiv_papers(as_of: datetime.datetime | None = None) -> list[dict]:
    """Fetch arXiv astro-ph.HE papers within the sync window.

    Tries the Atom query API first (supports any historical date). If arXiv
    returns HTTP 429 after retries *and* the requested window is the most
    recent announcement (``end_t`` within ~26h of now), falls back to the RSS
    feed, which is less rate-limited but only carries the latest announcement.

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

    cached = _load_cache(start_t, end_t)
    if cached is not None:
        print(f'📁 Cache hit ({len(cached)} papers); skipping arXiv request.')
        return cached

    age_hours = (datetime.datetime.now(ARXIV_TZ) - end_t).total_seconds() / 3600
    window_is_recent = age_hours <= 26

    if window_is_recent and _is_in_cooldown():
        print('⏭️  Skipping api (recent 429 cooldown active); going straight to RSS.')
        papers = _fetch_via_rss()
    elif _is_in_cooldown() and not window_is_recent:
        # Historical windows have no RSS fallback, but we still respect the
        # cooldown so repeat clicks don't hammer arXiv while it's throttling us.
        raise RuntimeError(
            f'arXiv API cooldown active (set after a recent HTTP 429); '
            f'retry in up to {ARXIV_COOLDOWN_SECONDS // 60} min.'
        )
    else:
        try:
            papers = _fetch_via_api(start_t, end_t)
        except (arxiv.HTTPError, _UrllibHTTPError) as e:
            status = getattr(e, 'status', None) or getattr(e, 'code', None)
            if status != 429:
                raise
            _mark_cooldown()
            print(f'⚠️  api returned 429; cooldown set for {ARXIV_COOLDOWN_SECONDS // 60} min.')
            if not window_is_recent:
                # No RSS fallback available for historical windows -- propagate.
                raise
            papers = _fetch_via_rss()

    papers.sort(key=lambda p: _extract_arxiv_id(p.get('url', '')) or '', reverse=True)
    # Never cache an empty result: a degraded RSS fallback during a 429 can
    # return [] and would otherwise mask the real window data on later runs.
    if papers:
        _save_cache(start_t, end_t, papers)
    print(f'✅ Found {len(papers)} papers within the sync window.')
    return papers
