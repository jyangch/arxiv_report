"""Persistent SQLite corpus storing bilingual arXiv metadata and digests."""

from contextlib import closing
import datetime
import glob
import json
import os
import re
import sqlite3
import time

from core import config
from core.fragments import (
    bare_arxiv_id,
    display_arxiv_id,
    extract_fragment_from_report_html,
    load_fragment,
    migrate_fragment_topics,
    parse_fragment,
    save_fragment,
)
from core.objects import extract_objects
from core.pub_status import classify_pub_status, normalize_status_label

_LISTING_CACHE_RE = re.compile(r'^arxiv_listing_(\d{8})\.json$')
_WINDOW_CACHE_RE = re.compile(r'^arxiv_\d{8}T\d{4}_(\d{8})T\d{4}\.json$')
_REPORT_FILE_RE = re.compile(r'arXiv_astro_ph_HE_daily_report_(\d{4}-\d{2}-\d{2})\.html$')
_FRAG_FILE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})\.html$')
_VERSION_RE = re.compile(r'v(\d+)$', re.IGNORECASE)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS papers (
    pid TEXT PRIMARY KEY,
    arxiv_id TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL DEFAULT '',
    title_zh TEXT NOT NULL DEFAULT '',
    authors TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    question_zh TEXT NOT NULL DEFAULT '',
    question_en TEXT NOT NULL DEFAULT '',
    method_zh TEXT NOT NULL DEFAULT '',
    method_en TEXT NOT NULL DEFAULT '',
    method_tag TEXT NOT NULL DEFAULT '',
    result_zh TEXT NOT NULL DEFAULT '',
    result_en TEXT NOT NULL DEFAULT '',
    caveat_zh TEXT NOT NULL DEFAULT '',
    caveat_en TEXT NOT NULL DEFAULT '',
    topics_json TEXT NOT NULL DEFAULT '[]',
    topic_slugs_json TEXT NOT NULL DEFAULT '[]',
    objects_json TEXT NOT NULL DEFAULT '[]',
    categories TEXT NOT NULL DEFAULT '',
    comment TEXT NOT NULL DEFAULT '',
    journal_ref TEXT NOT NULL DEFAULT '',
    doi TEXT NOT NULL DEFAULT '',
    pub_status TEXT NOT NULL DEFAULT 'submitted',
    status_label TEXT NOT NULL DEFAULT '',
    is_highlight INTEGER NOT NULL DEFAULT 0,
    highlight_zh TEXT NOT NULL DEFAULT '',
    highlight_en TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    pdf_url TEXT NOT NULL DEFAULT '',
    report_date TEXT NOT NULL DEFAULT '',
    number INTEGER NOT NULL DEFAULT 0,
    has_report INTEGER NOT NULL DEFAULT 0,
    updated_ts REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_papers_date ON papers(report_date);
CREATE INDEX IF NOT EXISTS idx_papers_method ON papers(method_tag);
CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(pub_status);
CREATE INDEX IF NOT EXISTS idx_papers_has_report ON papers(has_report);
CREATE TABLE IF NOT EXISTS citations (
    pid TEXT PRIMARY KEY,
    bibcode TEXT NOT NULL DEFAULT '',
    citation_count INTEGER NOT NULL DEFAULT 0,
    read_count INTEGER NOT NULL DEFAULT 0,
    refereed INTEGER NOT NULL DEFAULT 0,
    pub TEXT NOT NULL DEFAULT '',
    pubdate TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    found INTEGER NOT NULL DEFAULT 1,
    fetched_ts REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_citations_count ON citations(citation_count DESC);
"""


def default_db_path() -> str:
    """Return the resolved SQLite corpus path under ``ARXIV_DATA_DIR``."""
    return os.path.join(config.ARXIV_DATA_DIR, 'papers.db')


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection and ensure corpus schema exists.

    Args:
        db_path: Optional override path for the SQLite file.

    Returns:
        Configured ``sqlite3.Connection`` with ``sqlite3.Row`` row factory.
    """
    resolved = db_path or default_db_path()
    os.makedirs(os.path.dirname(resolved) or '.', exist_ok=True)
    conn = sqlite3.connect(resolved, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.executescript(_SCHEMA_SQL)
    existing_cols = {row['name'] for row in conn.execute('PRAGMA table_info(papers)').fetchall()}
    for col in ('question_en', 'method_en', 'result_en', 'caveat_en', 'highlight_en'):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    return conn


def infer_listing_date_from_cache_filename(filename: str) -> str | None:
    """Infer the arXiv announcement date ``YYYY-MM-DD`` from a cache filename.

    Supports both ``arxiv_listing_YYYYMMDD.json`` and legacy
    ``arxiv_YYYYMMDDTHHMM_YYYYMMDDTHHMM.json`` filenames.

    Args:
        filename: Cache basename (or full path).

    Returns:
        Announcement date formatted as ``YYYY-MM-DD``, or ``None``.
    """
    base = os.path.basename(filename)
    m_list = _LISTING_CACHE_RE.match(base)
    if m_list:
        d = datetime.datetime.strptime(m_list.group(1), '%Y%m%d').date()
        return d.isoformat()
    m_win = _WINDOW_CACHE_RE.match(base)
    if m_win:
        end_d = datetime.datetime.strptime(m_win.group(1), '%Y%m%d').date()
        delta = 3 if end_d.weekday() == 4 else 1
        return (end_d + datetime.timedelta(days=delta)).isoformat()
    return None


def _parse_version(disp_id: str) -> int:
    m = _VERSION_RE.search(disp_id or '')
    return int(m.group(1)) if m else 1


def _upsert_raw_with_conn(
    conn: sqlite3.Connection,
    papers: list[dict],
    listing_date: str,
) -> int:
    now_ts = time.time()
    count = 0
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        url = str(paper.get('url') or '').strip()
        pid = bare_arxiv_id(url) or bare_arxiv_id(str(paper.get('pdf_url') or ''))
        if not pid:
            continue
        disp_id = display_arxiv_id(url) or pid
        version = _parse_version(disp_id)
        title = str(paper.get('title') or '').strip()
        authors = str(paper.get('authors') or '').strip()
        summary = str(paper.get('summary') or '').strip()
        raw_cats = paper.get('categories') or []
        categories = (
            ' '.join(str(c).strip() for c in raw_cats if str(c).strip())
            if isinstance(raw_cats, list)
            else str(raw_cats).strip()
        )
        comment = str(paper.get('comment') or '').strip()
        journal_ref = str(paper.get('journal_ref') or '').strip()
        doi = str(paper.get('doi') or '').strip()
        raw_status, status_label = classify_pub_status(comment, journal_ref, doi)
        pub_status = 'submitted' if raw_status == 'preprint' else raw_status
        pdf_url = str(paper.get('pdf_url') or '').strip() or f'https://arxiv.org/pdf/{disp_id}'

        existing = conn.execute(
            'SELECT pid, objects_json, title_zh, question_zh, result_zh, has_report, report_date '
            'FROM papers WHERE pid = ?',
            (pid,),
        ).fetchone()

        obj_sources = [title, summary]
        if existing:
            obj_sources.extend(
                [existing['title_zh'], existing['question_zh'], existing['result_zh']]
            )
        objects = extract_objects('\n'.join(s for s in obj_sources if s))
        objects_json = json.dumps(objects, ensure_ascii=False)

        if existing:
            target_date = (
                existing['report_date']
                if existing['has_report'] or not listing_date
                else listing_date
            )
            conn.execute(
                """
                UPDATE papers SET
                    arxiv_id = ?, version = ?,
                    title = CASE WHEN ? != '' THEN ? ELSE title END,
                    authors = CASE WHEN ? != '' THEN ? ELSE authors END,
                    summary = CASE WHEN ? != '' THEN ? ELSE summary END,
                    categories = CASE WHEN ? != '' THEN ? ELSE categories END,
                    comment = CASE WHEN ? != '' THEN ? ELSE comment END,
                    journal_ref = CASE WHEN ? != '' THEN ? ELSE journal_ref END,
                    doi = CASE WHEN ? != '' THEN ? ELSE doi END,
                    pub_status = ?, status_label = ?,
                    url = CASE WHEN ? != '' THEN ? ELSE url END,
                    pdf_url = CASE WHEN ? != '' THEN ? ELSE pdf_url END,
                    objects_json = ?,
                    report_date = ?,
                    updated_ts = ?
                WHERE pid = ?
                """,
                (
                    disp_id,
                    version,
                    title,
                    title,
                    authors,
                    authors,
                    summary,
                    summary,
                    categories,
                    categories,
                    comment,
                    comment,
                    journal_ref,
                    journal_ref,
                    doi,
                    doi,
                    pub_status,
                    status_label,
                    url,
                    url,
                    pdf_url,
                    pdf_url,
                    objects_json,
                    target_date,
                    now_ts,
                    pid,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO papers (
                    pid, arxiv_id, version, title, authors, summary,
                    objects_json, categories, comment, journal_ref, doi,
                    pub_status, status_label, url, pdf_url, report_date,
                    has_report, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    pid,
                    disp_id,
                    version,
                    title,
                    authors,
                    summary,
                    objects_json,
                    categories,
                    comment,
                    journal_ref,
                    doi,
                    pub_status,
                    status_label,
                    url or f'https://arxiv.org/abs/{disp_id}',
                    pdf_url,
                    listing_date,
                    now_ts,
                ),
            )
        count += 1
    return count


def _apply_digest_with_conn(
    conn: sqlite3.Connection,
    report_date: str,
    fragment_html: str,
) -> int:
    parsed = parse_fragment(fragment_html)
    now_ts = time.time()
    count = 0
    for item in parsed['items'].values():
        pid = item.get('pid') or ''
        if not pid:
            continue
        existing = conn.execute(
            'SELECT pid, title, summary, authors, pub_status, status_label, url '
            'FROM papers WHERE pid = ?',
            (pid,),
        ).fetchone()

        title_en = (existing['title'] if existing and existing['title'] else '') or item['title_en']
        summary = existing['summary'] if existing else ''
        authors = (existing['authors'] if existing and existing['authors'] else '') or item[
            'authors'
        ]
        url = item['url'] or (existing['url'] if existing else '') or f'https://arxiv.org/abs/{pid}'
        disp_id = item['arxiv_id'] or pid
        version = _parse_version(disp_id)

        pub_status = item['status']
        status_label = normalize_status_label(item['status_label'])
        if existing and not status_label and existing['status_label']:
            pub_status = existing['pub_status']
            status_label = normalize_status_label(existing['status_label'])

        question_en = item.get('question_en', '')
        method_en = item.get('method_en', '')
        result_en = item.get('result_en', '')
        caveat_en = item.get('caveat_en', '')
        highlight_en = item.get('highlight_en', '')

        objects = extract_objects(
            '\n'.join(
                s
                for s in (
                    title_en,
                    summary,
                    item['title_zh'],
                    item['question_zh'],
                    question_en,
                    item['result_zh'],
                    result_en,
                )
                if s
            )
        )

        topics_json = json.dumps(item['topics'], ensure_ascii=False)
        slugs_json = json.dumps(item['topic_slugs'], ensure_ascii=False)
        objects_json = json.dumps(objects, ensure_ascii=False)

        if existing:
            conn.execute(
                """
                UPDATE papers SET
                    arxiv_id = CASE WHEN ? != '' THEN ? ELSE arxiv_id END,
                    version = CASE WHEN ? > version THEN ? ELSE version END,
                    title = CASE WHEN title = '' THEN ? ELSE title END,
                    title_zh = ?,
                    authors = CASE WHEN authors = '' THEN ? ELSE authors END,
                    question_zh = ?,
                    question_en = ?,
                    method_zh = ?,
                    method_en = ?,
                    method_tag = ?,
                    result_zh = ?,
                    result_en = ?,
                    caveat_zh = ?,
                    caveat_en = ?,
                    topics_json = ?,
                    topic_slugs_json = ?,
                    objects_json = ?,
                    pub_status = CASE WHEN ? != '' THEN ? ELSE pub_status END,
                    status_label = CASE WHEN ? != '' THEN ? ELSE status_label END,
                    is_highlight = ?,
                    highlight_zh = ?,
                    highlight_en = ?,
                    url = CASE WHEN ? != '' THEN ? ELSE url END,
                    report_date = ?,
                    number = ?,
                    has_report = 1,
                    updated_ts = ?
                WHERE pid = ?
                """,
                (
                    disp_id,
                    disp_id,
                    version,
                    version,
                    title_en,
                    item['title_zh'],
                    authors,
                    item['question_zh'],
                    question_en,
                    item['method_zh'],
                    method_en,
                    item['method_tag'],
                    item['result_zh'],
                    result_en,
                    item['caveat_zh'],
                    caveat_en,
                    topics_json,
                    slugs_json,
                    objects_json,
                    status_label,
                    pub_status,
                    status_label,
                    status_label,
                    item['is_highlight'],
                    item['highlight_zh'],
                    highlight_en,
                    url,
                    url,
                    report_date,
                    item['number'],
                    now_ts,
                    pid,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO papers (
                    pid, arxiv_id, version, title, title_zh, authors,
                    question_zh, question_en, method_zh, method_en, method_tag,
                    result_zh, result_en, caveat_zh, caveat_en,
                    topics_json, topic_slugs_json, objects_json,
                    pub_status, status_label, is_highlight, highlight_zh, highlight_en,
                    url, pdf_url, report_date, number, has_report, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    pid,
                    disp_id,
                    version,
                    title_en,
                    item['title_zh'],
                    authors,
                    item['question_zh'],
                    question_en,
                    item['method_zh'],
                    method_en,
                    item['method_tag'],
                    item['result_zh'],
                    result_en,
                    item['caveat_zh'],
                    caveat_en,
                    topics_json,
                    slugs_json,
                    objects_json,
                    pub_status,
                    status_label,
                    item['is_highlight'],
                    item['highlight_zh'],
                    highlight_en,
                    url,
                    f'https://arxiv.org/pdf/{disp_id}',
                    report_date,
                    item['number'],
                    now_ts,
                ),
            )
        count += 1
    return count


def upsert_raw_papers(
    papers: list[dict],
    listing_date: str,
    db_path: str | None = None,
) -> int:
    """Insert or refresh fetched English arXiv paper metadata."""
    with closing(connect(db_path)) as conn:
        count = _upsert_raw_with_conn(conn, papers, listing_date)
        conn.commit()
    return count


def apply_report_digest(
    report_date: str,
    fragment_html: str,
    db_path: str | None = None,
) -> int:
    """Parse a report HTML fragment and store its Chinese digest fields."""
    with closing(connect(db_path)) as conn:
        count = _apply_digest_with_conn(conn, report_date, fragment_html)
        conn.commit()
    return count


def sync_daily_report(
    report_date: str,
    papers: list[dict],
    fragment_html: str,
    db_path: str | None = None,
) -> None:
    """Sync both raw fetched papers and LLM Chinese digest for one report date."""
    with closing(connect(db_path)) as conn:
        _upsert_raw_with_conn(conn, papers, report_date)
        _apply_digest_with_conn(conn, report_date, fragment_html)
        conn.commit()


def bootstrap_corpus(
    reports_dir: str = './reports',
    cache_dir: str | None = None,
    db_path: str | None = None,
    extract_fragments: bool = True,
) -> dict[str, int]:
    """Import all JSON caches and daily report HTML/fragments into SQLite.

    Args:
        reports_dir: Root directory containing daily HTML reports and fragments.
        cache_dir: Directory containing arXiv JSON caches (defaults to
            ``<reports_dir>/.cache``).
        db_path: Override SQLite database path.
        extract_fragments: If True, write extracted ``fragments/<date>.html``
            files when bootstrapping from full report HTMLs.

    Returns:
        Summary count dict with keys ``cache_files``, ``reports``,
        ``fragments_extracted``, ``total_papers``, and ``digest_papers``.
    """
    resolved_cache = cache_dir or os.path.join(reports_dir, '.cache')
    cache_files = 0
    reports = 0
    fragments_extracted = 0

    with closing(connect(db_path)) as conn:
        if os.path.isdir(resolved_cache):
            for path in sorted(glob.glob(os.path.join(resolved_cache, '*.json'))):
                listing_date = infer_listing_date_from_cache_filename(path)
                if not listing_date:
                    continue
                try:
                    with open(path, encoding='utf-8') as f:
                        data = json.load(f)
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(data, list) and data:
                    _upsert_raw_with_conn(conn, data, listing_date)
                    cache_files += 1

        seen_dates: set[str] = set()
        report_pattern = os.path.join(reports_dir, 'arXiv_astro_ph_HE_daily_report_*.html')
        for path in sorted(glob.glob(report_pattern)):
            m = _REPORT_FILE_RE.search(os.path.basename(path))
            if not m:
                continue
            date_str = m.group(1)
            seen_dates.add(date_str)
            loaded = load_fragment(date_str, reports_dir)
            if loaded is not None:
                frag_html, _ = loaded
            else:
                try:
                    with open(path, encoding='utf-8-sig') as f:
                        full_html = f.read()
                    frag_html, provider = extract_fragment_from_report_html(full_html)
                except (OSError, ValueError):
                    continue
                if extract_fragments:
                    save_fragment(date_str, frag_html, provider, reports_dir)
                    fragments_extracted += 1
            _apply_digest_with_conn(conn, date_str, frag_html)
            reports += 1

        frag_dir = os.path.join(reports_dir, 'fragments')
        if os.path.isdir(frag_dir):
            for path in sorted(glob.glob(os.path.join(frag_dir, '*.html'))):
                m_f = _FRAG_FILE_RE.match(os.path.basename(path))
                if not m_f or m_f.group(1) in seen_dates:
                    continue
                loaded = load_fragment(m_f.group(1), reports_dir)
                if loaded is not None:
                    _apply_digest_with_conn(conn, m_f.group(1), loaded[0])
                    reports += 1

        conn.commit()
        totals = conn.execute(
            'SELECT COUNT(*) AS total, SUM(has_report) AS digested FROM papers'
        ).fetchone()
        total_papers = int(totals['total'] or 0)
        digest_papers = int(totals['digested'] or 0)

    return {
        'cache_files': cache_files,
        'reports': reports,
        'fragments_extracted': fragments_extracted,
        'total_papers': total_papers,
        'digest_papers': digest_papers,
    }


def ensure_bootstrapped(
    db_path: str | None = None,
    reports_dir: str = './reports',
) -> None:
    """Populate an empty corpus from existing caches and reports if available."""
    with closing(connect(db_path)) as conn:
        row = conn.execute('SELECT COUNT(*) AS cnt FROM papers').fetchone()
        if int(row['cnt'] or 0) > 0:
            return
    if os.path.isdir(reports_dir):
        bootstrap_corpus(reports_dir=reports_dir, db_path=db_path)


def corpus_stats(db_path: str | None = None) -> dict:
    """Compute summary statistics over the indexed paper corpus."""
    ensure_bootstrapped(db_path=db_path)
    with closing(connect(db_path)) as conn:
        agg = conn.execute(
            """
            SELECT
                COUNT(*) AS total_papers,
                COALESCE(SUM(has_report), 0) AS digest_papers,
                MIN(NULLIF(report_date, '')) AS min_date,
                MAX(NULLIF(report_date, '')) AS max_date,
                COUNT(DISTINCT CASE WHEN has_report = 1 THEN report_date END) AS report_days
            FROM papers
            """
        ).fetchone()
        method_rows = conn.execute(
            'SELECT method_tag, COUNT(*) AS c FROM papers WHERE method_tag != "" GROUP BY method_tag ORDER BY c DESC'
        ).fetchall()
        slug_rows = conn.execute(
            'SELECT topic_slugs_json FROM papers WHERE topic_slugs_json != "[]"'
        ).fetchall()

    topic_counts: dict[str, int] = {}
    for row in slug_rows:
        try:
            slugs = json.loads(row['topic_slugs_json'])
        except json.JSONDecodeError:
            continue
        for slug in slugs:
            topic_counts[slug] = topic_counts.get(slug, 0) + 1

    return {
        'total_papers': int(agg['total_papers'] or 0),
        'digest_papers': int(agg['digest_papers'] or 0),
        'min_date': agg['min_date'] or '',
        'max_date': agg['max_date'] or '',
        'report_days': int(agg['report_days'] or 0),
        'topic_counts': dict(sorted(topic_counts.items(), key=lambda kv: kv[1], reverse=True)),
        'method_counts': {r['method_tag']: int(r['c']) for r in method_rows},
    }


def migrate_all_fragments(
    reports_dir: str = './reports',
    db_path: str | None = None,
) -> dict[str, int]:
    """Reclassify all stored fragments into the canonical 16-topic taxonomy.

    Updates ``fragments/<date>.html`` in place without calling an LLM, refreshes
    ``topics_json`` and ``topic_slugs_json`` in SQLite, and returns summary
    counts.
    """
    ensure_bootstrapped(db_path=db_path, reports_dir=reports_dir)

    fragments_migrated = 0
    fragments_changed = 0
    papers_updated = 0

    with closing(connect(db_path)) as conn:
        meta_rows = conn.execute('SELECT pid, title, summary, categories FROM papers').fetchall()
        paper_meta_by_pid = {r['pid']: dict(r) for r in meta_rows}

        report_pattern = os.path.join(reports_dir, 'arXiv_astro_ph_HE_daily_report_*.html')
        for path in sorted(glob.glob(report_pattern)):
            m = _REPORT_FILE_RE.search(os.path.basename(path))
            if not m:
                continue
            date_str = m.group(1)
            if load_fragment(date_str, reports_dir) is not None:
                continue
            try:
                with open(path, encoding='utf-8-sig') as f:
                    full_html = f.read()
                frag_html, provider = extract_fragment_from_report_html(full_html)
                save_fragment(date_str, frag_html, provider, reports_dir)
            except (OSError, ValueError):
                continue

        frag_dir = os.path.join(reports_dir, 'fragments')
        if os.path.isdir(frag_dir):
            for path in sorted(glob.glob(os.path.join(frag_dir, '*.html'))):
                m_f = _FRAG_FILE_RE.match(os.path.basename(path))
                if not m_f:
                    continue
                date_str = m_f.group(1)
                loaded = load_fragment(date_str, reports_dir)
                if loaded is None:
                    continue
                old_html, provider = loaded
                migrated_html = migrate_fragment_topics(
                    old_html,
                    paper_meta_by_pid=paper_meta_by_pid,
                )
                save_fragment(date_str, migrated_html, provider, reports_dir)
                if migrated_html != old_html:
                    fragments_changed += 1
                papers_updated += _apply_digest_with_conn(conn, date_str, migrated_html)
                fragments_migrated += 1

        conn.commit()

    return {
        'fragments_migrated': fragments_migrated,
        'fragments_changed': fragments_changed,
        'papers_updated': papers_updated,
    }
