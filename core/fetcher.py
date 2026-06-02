"""arXiv ingestion: compute the daily submission window and pull astro-ph.HE papers."""

import concurrent.futures
import datetime
from datetime import timedelta
from html import unescape as _html_unescape
import json
import os
import re
import time
from urllib.error import HTTPError as _UrllibHTTPError, URLError as _UrllibURLError
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
# Original-submission (v1) timestamp from the abs-page submission history, e.g.
# "[v1] Thu, 21 May 2026 18:00:32 UTC". arXiv always renders these in UTC.
_ABS_SUBMITTED_RE = re.compile(
    r'\[v1\].*?(\w{3}, \d{1,2} \w{3} \d{4} \d{2}:\d{2}:\d{2})\s+UTC', re.DOTALL
)


def get_arxiv_sync_window(
    as_of: datetime.datetime | None = None,
) -> tuple[datetime.datetime, datetime.datetime]:
    """Compute the (start, end) ET submission window of the listing dated ``as_of``.

    arXiv announces Sunday-Thursday at 20:00 ET; the submission deadline is
    14:00 ET. Each calendar listing covers a fixed submission window:

    ====== =================================== ====================
    Listing Submission window (ET)             Announced
    ====== =================================== ====================
    Mon     Thu 14:00 -> Fri 14:00             Sun 20:00
    Tue     Fri 14:00 -> Mon 14:00 (weekend)   Mon 20:00
    Wed     Mon 14:00 -> Tue 14:00             Tue 20:00
    Thu     Tue 14:00 -> Wed 14:00             Wed 20:00
    Fri     Wed 14:00 -> Thu 14:00             Thu 20:00
    ====== =================================== ====================

    The three-day weekend bundle is announced on (and dated) **Tuesday**, so
    the reach-back belongs on Tuesday, not Monday. On Sat/Sun there is no
    listing and ``start == end``.

    Args:
        as_of: Reference timestamp; defaults to ``now`` in the Eastern timezone.

    Returns:
        A pair of timezone-aware ET datetimes ``(start, end)``.
    """
    now_et = as_of.astimezone(ARXIV_TZ) if as_of else datetime.datetime.now(ARXIV_TZ)
    weekday = now_et.weekday()
    # (end_offset, length): end is ``end_offset`` days before ``as_of`` at
    # 14:00 ET; start is ``length`` days before end.
    if weekday == 0:  # Monday: Sunday-announced listing = Thu -> Fri
        end_offset, length = 3, 1
    elif weekday == 1:  # Tuesday: Monday-announced weekend bundle = Fri -> Mon
        end_offset, length = 1, 3
    elif weekday in (5, 6):  # weekend: no listing
        end_offset, length = 1, 0
    else:  # Wed/Thu/Fri: prior day's 14:00 -> 14:00
        end_offset, length = 1, 1

    end_time = now_et.replace(hour=14, minute=0, second=0, microsecond=0) - timedelta(
        days=end_offset
    )
    start_time = end_time - timedelta(days=length)
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


def _parse_abs_submitted(html: str) -> datetime.datetime | None:
    """Return the v1 (original) submission time from abs-page HTML, or None.

    arXiv renders submission-history timestamps in UTC; the returned datetime
    is timezone-aware UTC. ``None`` means the timestamp could not be located.
    """
    m = _ABS_SUBMITTED_RE.search(html)
    if not m:
        return None
    try:
        naive = datetime.datetime.strptime(m.group(1), '%a, %d %b %Y %H:%M:%S')
    except ValueError:
        return None
    return pytz.UTC.localize(naive)


def _fetch_abs_metadata(
    arxiv_id: str, timeout: float = 20.0, max_retries: int = 2
) -> tuple[str, str, str, datetime.datetime | None]:
    """Scrape (comment, journal_ref, doi, v1_submitted) from arxiv.org/abs/<id>.

    Used to backfill fields missing in the RSS fallback and to recover each
    paper's submission time (RSS exposes only the announcement date). The
    arxiv.org/abs host is separate from export.arxiv.org/api and rarely 429s.

    Retries on transient failures (read timeout, connection error, 5xx) up to
    ``max_retries`` times with a 1-second backoff; 4xx HTTP responses are
    raised immediately because they do not improve on retry.
    """
    url = f'https://arxiv.org/abs/{arxiv_id}'
    req = urllib.request.Request(url, headers={'User-Agent': 'arxiv-report/1.0'})

    html = ''
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                html = resp.read().decode('utf-8', errors='replace')
            break
        except _UrllibHTTPError as e:
            if 400 <= getattr(e, 'code', 0) < 500 or attempt >= max_retries:
                raise
        except (TimeoutError, _UrllibURLError, OSError):
            if attempt >= max_retries:
                raise
        time.sleep(1)

    def first(regex: re.Pattern) -> str:
        m = regex.search(html)
        return _html_unescape(m.group(1)).strip() if m else ''

    return (
        first(_ABS_COMMENTS_RE),
        first(_ABS_JREF_RE),
        first(_ABS_DOI_RE),
        _parse_abs_submitted(html),
    )


def _enrich_papers(papers: list[dict], max_workers: int = 5) -> None:
    """Fill missing ``comment / journal_ref / doi`` on each paper via abs pages.

    Mutates ``papers`` in place. Individual failures are logged and skipped.
    """

    def enrich_one(paper: dict) -> None:
        arxiv_id = _extract_arxiv_id(paper.get('url', ''))
        if not arxiv_id:
            return
        try:
            c, j, d, submitted = _fetch_abs_metadata(arxiv_id)
        except Exception as e:
            print(f'  ⚠️  abs-page fetch failed for {arxiv_id}: {e}')
            return
        paper['comment'] = c
        paper['journal_ref'] = j
        paper['doi'] = d
        paper['_submitted_dt'] = submitted

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


def _fetch_via_rss(
    start_t: datetime.datetime, end_t: datetime.datetime
) -> list[dict]:
    """Fetch astro-ph.HE RSS papers and filter to the ``[start_t, end_t)`` window.

    The RSS endpoint is far less rate-limited than the query API but exposes
    only the most recent announcement, so it is a fallback for ``as_of ==
    today`` when the API is 429-ing.

    Only ``announce_type == 'new'`` items are kept (replacements and
    cross-listings excluded). Each kept item's v1 submission time is recovered
    from its abs page and compared against the window: a feed that has not yet
    refreshed to the requested listing filters to ``[]`` rather than returning
    the previous announcement's papers. Items whose submission time cannot be
    scraped are kept (fail-open) so a parsing hiccup never silently drops a
    paper.
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
        papers = _filter_to_window(papers, start_t, end_t)
        print(f'🪟 {len(papers)} of the announcement fall within the sync window.')

    if papers and not any(p.get('_submitted_dt') is not None for p in papers):
        # Every kept paper passed the filter via fail-open (abs-page enrichment
        # failed for all of them), so we cannot trust that any of them actually
        # fall in the window. Treat as empty so the caller skips caching and
        # the report file, letting the next scheduled retry re-fetch.
        print(
            '⚠️  All RSS papers came through fail-open (abs-page enrichment '
            'failed for every one); discarding result so the next retry can re-fetch.'
        )
        return []

    for paper in papers:
        paper.pop('_submitted_dt', None)
    return papers


def _filter_to_window(
    papers: list[dict], start_t: datetime.datetime, end_t: datetime.datetime
) -> list[dict]:
    """Keep papers whose v1 submission time lies in ``[start_t, end_t)``.

    Inspects the transient ``_submitted_dt`` stashed by enrichment but leaves
    it on the kept dicts so the caller can distinguish papers with a real
    timestamp from fail-open passes (where enrichment failed). A paper with
    no recovered timestamp is kept (fail-open).
    """
    kept = []
    for paper in papers:
        submitted = paper.get('_submitted_dt')
        if submitted is None or start_t <= submitted < end_t:
            kept.append(paper)
    return kept


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
        papers = _fetch_via_rss(start_t, end_t)
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
            papers = _fetch_via_rss(start_t, end_t)

    papers.sort(key=lambda p: _extract_arxiv_id(p.get('url', '')) or '', reverse=True)
    # Never cache an empty result: a degraded RSS fallback during a 429 can
    # return [] and would otherwise mask the real window data on later runs.
    if papers:
        _save_cache(start_t, end_t, papers)
    print(f'✅ Found {len(papers)} papers within the sync window.')
    return papers
