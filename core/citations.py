"""Citation counts and metadata from NASA SciX (ADS) and INSPIRE-HEP."""

from contextlib import closing
import datetime
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from core import config
from core.corpus import connect, default_db_path, ensure_bootstrapped
from core.fragments import bare_arxiv_id

_SCIX_CHUNK = 40
_INSPIRE_CHUNK = 25
_INSPIRE_API_URL = 'https://inspirehep.net/api/literature'


class CitationError(RuntimeError):
    """Raised when a citation provider returns an unrecoverable error."""


class CitationRateLimit(CitationError):
    """Raised when a citation provider throttles requests (HTTP 429 or low quota)."""

    def __init__(self, message: str = 'Citation rate limit reached', reset_ts: float | None = None):
        super().__init__(message)
        self.reset_ts = reset_ts


def get_scix_token() -> str:
    """Return configured NASA SciX / ADS API token, or empty string if absent."""
    if config.SCIX_API_TOKEN and config.SCIX_API_TOKEN.strip():
        return config.SCIX_API_TOKEN.strip()
    for var in ('SCIX_API_TOKEN', 'ADS_API_KEY', 'ADS_API_TOKEN', 'ADS_DEV_KEY', 'ADS_TOKEN'):
        val = os.getenv(var, '').strip()
        if val:
            return val
    for path in ('~/.scix/token', '~/.ads/dev_key', '~/.ads/token'):
        expanded = os.path.expanduser(path)
        if os.path.isfile(expanded):
            try:
                with open(expanded, encoding='utf-8') as f:
                    val = f.read().strip()
                if val:
                    return val
            except OSError:
                continue
    return ''


def scix_abs_url(pid: str, bibcode: str = '') -> str:
    """Construct a NASA SciX abstract page URL from a bibcode or arXiv ID."""
    if bibcode:
        return f'https://scixplorer.org/abs/{urllib.parse.quote(bibcode, safe="")}/abstract'
    clean = bare_arxiv_id(pid) or (pid or '').strip()
    return f'https://scixplorer.org/abs/arXiv:{clean}/abstract'


def _http_get_json(
    url: str,
    headers: dict[str, str] | None = None,
    provider_label: str = 'SciX',
) -> tuple[dict, dict[str, str]]:
    req_headers = {'User-Agent': 'arxiv-report/1.0'}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode('utf-8')
            return json.loads(raw), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            reset_raw = exc.headers.get('X-RateLimit-Reset') if exc.headers else None
            reset_ts = (
                float(reset_raw) if reset_raw and reset_raw.replace('.', '', 1).isdigit() else None
            )
            raise CitationRateLimit(
                f'{provider_label} rate limit reached', reset_ts=reset_ts
            ) from exc
        if exc.code in (401, 403):
            raise CitationError(
                f'{provider_label} rejected credentials (HTTP {exc.code}).'
            ) from exc
        raise CitationError(f'{provider_label} HTTP {exc.code}: {exc.reason}') from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CitationError(f'{provider_label} request failed: {exc}') from exc


def query_scix_batch(pids: list[str], token: str) -> dict[str, dict]:
    """Query NASA SciX / ADS API for up to ``_SCIX_CHUNK`` bare arXiv IDs.

    Args:
        pids: Bare arXiv identifiers (e.g. ``2606.28115``).
        token: NASA SciX or ADS bearer token.

    Returns:
        Mapping ``{pid: citation_record}`` for matched records.
    """
    if not pids:
        return {}
    chunk = pids[:_SCIX_CHUNK]
    wanted = {bare_arxiv_id(p) or p for p in chunk}
    q = 'identifier:(' + ' OR '.join(f'"arXiv:{p}"' for p in wanted) + ')'
    params = urllib.parse.urlencode(
        {
            'q': q,
            'fl': 'bibcode,identifier,citation_count,read_count,pubdate,property,pub',
            'rows': 2 * len(wanted),
        }
    )
    data, headers = _http_get_json(
        f'{config.SCIX_API_URL}?{params}',
        headers={'Authorization': f'Bearer {token}'},
        provider_label='SciX',
    )

    out: dict[str, dict] = {}
    for doc in data.get('response', {}).get('docs', []):
        hit = None
        for ident in doc.get('identifier') or []:
            b = bare_arxiv_id(str(ident))
            if b and b in wanted:
                hit = b
                break
        if not hit:
            continue
        rec = {
            'bibcode': str(doc.get('bibcode') or ''),
            'citation_count': int(doc.get('citation_count') or 0),
            'read_count': int(doc.get('read_count') or 0),
            'pubdate': str(doc.get('pubdate') or ''),
            'refereed': 'REFEREED' in (doc.get('property') or []),
            'pub': str(doc.get('pub') or ''),
            'source': 'scix',
        }
        prev = out.get(hit)
        if prev is None or (rec['citation_count'], rec['refereed']) > (
            prev['citation_count'],
            prev['refereed'],
        ):
            out[hit] = rec

    remaining = headers.get('X-RateLimit-Remaining') or headers.get('x-ratelimit-remaining')
    if (
        remaining is not None
        and str(remaining).strip().isdigit()
        and int(str(remaining).strip()) < 5
    ):
        reset_raw = headers.get('X-RateLimit-Reset') or headers.get('x-ratelimit-reset')
        reset_ts = (
            float(reset_raw) if reset_raw and str(reset_raw).replace('.', '', 1).isdigit() else None
        )
        raise CitationRateLimit('SciX rate limit nearly exhausted', reset_ts=reset_ts)

    return out


def query_inspire_batch(pids: list[str]) -> dict[str, dict]:
    """Query INSPIRE-HEP REST API for up to ``_INSPIRE_CHUNK`` arXiv IDs without a token."""
    if not pids:
        return {}
    chunk = pids[:_INSPIRE_CHUNK]
    wanted = {bare_arxiv_id(p) or p for p in chunk}
    q = ' or '.join(f'eprint:{p}' for p in wanted)
    params = urllib.parse.urlencode(
        {
            'q': q,
            'size': 50,
            'fields': 'citation_count,arxiv_eprints,publication_info,refereed',
        }
    )
    data, _headers = _http_get_json(
        f'{_INSPIRE_API_URL}?{params}',
        provider_label='INSPIRE',
    )

    out: dict[str, dict] = {}
    for hit in data.get('hits', {}).get('hits', []):
        meta = hit.get('metadata') or {}
        pid_match = None
        for ep in meta.get('arxiv_eprints') or []:
            b = bare_arxiv_id(str(ep.get('value') or ''))
            if b and b in wanted:
                pid_match = b
                break
        if not pid_match:
            continue
        pub_infos = meta.get('publication_info') or []
        pub = ''
        if pub_infos and isinstance(pub_infos[0], dict):
            pub = str(pub_infos[0].get('journal_title') or '')
        rec = {
            'bibcode': '',
            'citation_count': int(meta.get('citation_count') or 0),
            'read_count': 0,
            'pubdate': '',
            'refereed': bool(meta.get('refereed')),
            'pub': pub,
            'source': 'inspire',
        }
        prev = out.get(pid_match)
        if prev is None or rec['citation_count'] > prev['citation_count']:
            out[pid_match] = rec
    return out


def store_citations(
    records: dict[str, dict],
    missing_pids: list[str],
    source: str,
    db_path: str | None = None,
) -> None:
    """Persist fetched citation records and track missing IDs in SQLite."""
    now_ts = time.time()
    with closing(connect(db_path)) as conn:
        for pid, rec in records.items():
            clean_pid = bare_arxiv_id(pid) or pid
            if not clean_pid:
                continue
            conn.execute(
                """
                INSERT INTO citations (
                    pid, bibcode, citation_count, read_count, refereed,
                    pub, pubdate, source, found, fetched_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(pid) DO UPDATE SET
                    bibcode = CASE WHEN excluded.bibcode != '' THEN excluded.bibcode ELSE citations.bibcode END,
                    citation_count = excluded.citation_count,
                    read_count = excluded.read_count,
                    refereed = excluded.refereed,
                    pub = CASE WHEN excluded.pub != '' THEN excluded.pub ELSE citations.pub END,
                    pubdate = CASE WHEN excluded.pubdate != '' THEN excluded.pubdate ELSE citations.pubdate END,
                    source = excluded.source,
                    found = 1,
                    fetched_ts = excluded.fetched_ts
                """,
                (
                    clean_pid,
                    str(rec.get('bibcode') or ''),
                    int(rec.get('citation_count') or 0),
                    int(rec.get('read_count') or 0),
                    1 if rec.get('refereed') else 0,
                    str(rec.get('pub') or ''),
                    str(rec.get('pubdate') or ''),
                    str(rec.get('source') or source),
                    now_ts,
                ),
            )
        for pid in missing_pids:
            clean_pid = bare_arxiv_id(pid) or pid
            if not clean_pid:
                continue
            conn.execute(
                """
                INSERT INTO citations (pid, source, found, fetched_ts)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(pid) DO UPDATE SET
                    source = CASE WHEN citations.found = 1 THEN citations.source ELSE excluded.source END,
                    fetched_ts = excluded.fetched_ts
                """,
                (clean_pid, source, now_ts),
            )
        conn.commit()


def _resolve_since_date(conn, days: int, since: str) -> str:
    if since:
        return since.strip()
    if days <= 0:
        return ''
    row = conn.execute('SELECT MAX(NULLIF(report_date, "")) AS max_d FROM papers').fetchone()
    anchor_str = row['max_d'] if row and row['max_d'] else ''
    try:
        anchor = datetime.date.fromisoformat(anchor_str) if anchor_str else datetime.date.today()
    except ValueError:
        anchor = datetime.date.today()
    return (anchor - datetime.timedelta(days=days)).isoformat()


def stale_pids(
    *,
    stale_days: float = 7.0,
    days: int = 0,
    since: str = '',
    until: str = '',
    upgrade_to_scix: bool = True,
    db_path: str | None = None,
) -> list[str]:
    """Return paper IDs needing initial citation fetch, refresh, or SciX upgrade."""
    ensure_bootstrapped(db_path=db_path)
    has_scix_tok = bool(get_scix_token())
    with closing(connect(db_path)) as conn:
        eff_since = _resolve_since_date(conn, days, since)
        eff_until = until.strip()

        conditions: list[str] = []
        params: list = []

        if stale_days <= 0:
            conditions.append('1=1')
        else:
            cutoff_ts = time.time() - stale_days * 86400.0
            stale_clauses = ['c.pid IS NULL', 'c.fetched_ts < ?']
            params.append(cutoff_ts)
            if upgrade_to_scix and has_scix_tok:
                stale_clauses.append("c.source != 'scix'")
            conditions.append('(' + ' OR '.join(stale_clauses) + ')')

        if eff_since:
            conditions.append('p.report_date >= ?')
            params.append(eff_since)
        if eff_until:
            conditions.append('p.report_date <= ?')
            params.append(eff_until)

        sql = f"""
            SELECT p.pid
            FROM papers p
            LEFT JOIN citations c ON c.pid = p.pid
            WHERE {' AND '.join(conditions)}
            ORDER BY c.pid IS NOT NULL ASC, p.has_report DESC, p.report_date DESC, c.fetched_ts ASC
        """
        rows = conn.execute(sql, params).fetchall()
    return [r['pid'] for r in rows]


def refresh_citations(
    *,
    pids: list[str] | None = None,
    provider: str = 'auto',
    stale_days: float = 7.0,
    days: int = 0,
    since: str = '',
    until: str = '',
    max_requests: int = 40,
    sleep_s: float = 0.3,
    db_path: str | None = None,
) -> dict:
    """Fetch citation counts via SciX (when token exists) or INSPIRE-HEP fallback.

    Partial batches are committed to SQLite immediately so rate-limited runs
    never lose previously retrieved counts.
    """
    tok = get_scix_token()
    if provider == 'auto':
        used_provider = 'scix' if tok else 'inspire'
    elif provider == 'scix':
        if not tok:
            raise CitationError('No SciX/ADS token configured')
        used_provider = 'scix'
    elif provider == 'inspire':
        used_provider = 'inspire'
    else:
        raise ValueError(f'Unsupported citation provider: {provider!r}')

    if pids is not None:
        all_targets = sorted({bare_arxiv_id(p) for p in pids if bare_arxiv_id(p)})
    else:
        all_targets = stale_pids(
            stale_days=stale_days,
            days=days,
            since=since,
            until=until,
            upgrade_to_scix=(used_provider == 'scix'),
            db_path=db_path,
        )

    chunk_sz = _SCIX_CHUNK if used_provider == 'scix' else _INSPIRE_CHUNK
    budget = max(0, max_requests) * chunk_sz
    target = all_targets[:budget]

    fetched = 0
    found = 0
    req_count = 0
    rate_limited = False

    for i in range(0, len(target), chunk_sz):
        chunk = target[i : i + chunk_sz]
        req_count += 1
        try:
            if used_provider == 'scix':
                records = query_scix_batch(chunk, tok)
            else:
                records = query_inspire_batch(chunk)
        except CitationRateLimit:
            rate_limited = True
            break

        missing = [p for p in chunk if p not in records]
        store_citations(records, missing, used_provider, db_path=db_path)
        fetched += len(chunk)
        found += len(records)

        if sleep_s > 0 and i + chunk_sz < len(target):
            time.sleep(sleep_s)

    return {
        'provider': used_provider,
        'requested': len(target),
        'fetched': fetched,
        'found': found,
        'requests': req_count,
        'rate_limited': rate_limited,
        'remaining': max(0, len(all_targets) - fetched),
    }


def get_citation_map(
    pids: list[str] | None = None,
    db_path: str | None = None,
) -> dict[str, dict]:
    """Return ``{pid: citation_info}`` for found citation records without auto-bootstrap."""
    if db_path is None and not os.path.exists(default_db_path()):
        return {}
    if db_path is not None and not os.path.exists(db_path):
        return {}

    out: dict[str, dict] = {}
    with closing(connect(db_path)) as conn:
        if pids is None:
            rows = conn.execute(
                'SELECT pid, bibcode, citation_count, read_count, refereed, pub, source, fetched_ts '
                'FROM citations WHERE found = 1'
            ).fetchall()
            _collect_citation_rows(out, rows)
        else:
            clean = sorted({bare_arxiv_id(p) or p for p in pids if p})
            for i in range(0, len(clean), 500):
                batch = clean[i : i + 500]
                ph = ','.join('?' for _ in batch)
                rows = conn.execute(
                    f'SELECT pid, bibcode, citation_count, read_count, refereed, pub, source, fetched_ts '
                    f'FROM citations WHERE found = 1 AND pid IN ({ph})',
                    batch,
                ).fetchall()
                _collect_citation_rows(out, rows)
    return out


def _collect_citation_rows(out: dict[str, dict], rows) -> None:
    for r in rows:
        pid = r['pid']
        bibcode = r['bibcode'] or ''
        fetched_ts = float(r['fetched_ts'] or 0)
        fetched_str = datetime.date.fromtimestamp(fetched_ts).isoformat() if fetched_ts > 0 else ''
        out[pid] = {
            'citation_count': int(r['citation_count'] or 0),
            'read_count': int(r['read_count'] or 0),
            'bibcode': bibcode,
            'refereed': bool(r['refereed']),
            'pub': r['pub'] or '',
            'source': r['source'] or '',
            'scix_url': scix_abs_url(pid, bibcode),
            'fetched': fetched_str,
        }


def citation_stats(db_path: str | None = None) -> dict:
    """Compute corpus-wide citation coverage statistics."""
    ensure_bootstrapped(db_path=db_path)
    with closing(connect(db_path)) as conn:
        total_papers = int(conn.execute('SELECT COUNT(*) FROM papers').fetchone()[0] or 0)
        agg = conn.execute(
            """
            SELECT
                COUNT(*) AS tracked,
                COALESCE(SUM(CASE WHEN found = 1 THEN 1 ELSE 0 END), 0) AS found_cnt,
                COALESCE(SUM(CASE WHEN found = 1 AND citation_count > 0 THEN 1 ELSE 0 END), 0) AS cited_cnt,
                COALESCE(MAX(CASE WHEN found = 1 THEN citation_count ELSE 0 END), 0) AS max_cites,
                COALESCE(SUM(CASE WHEN source = 'scix' THEN 1 ELSE 0 END), 0) AS scix_cnt,
                COALESCE(SUM(CASE WHEN source = 'inspire' THEN 1 ELSE 0 END), 0) AS inspire_cnt,
                COALESCE(MAX(fetched_ts), 0) AS latest_ts
            FROM citations
            """
        ).fetchone()
    latest_ts = float(agg['latest_ts'] or 0)
    return {
        'token_configured': bool(get_scix_token()),
        'total_papers': total_papers,
        'tracked_papers': int(agg['tracked'] or 0),
        'found_papers': int(agg['found_cnt'] or 0),
        'cited_papers': int(agg['cited_cnt'] or 0),
        'max_citations': int(agg['max_cites'] or 0),
        'scix_records': int(agg['scix_cnt'] or 0),
        'inspire_records': int(agg['inspire_cnt'] or 0),
        'latest_fetched': (
            datetime.date.fromtimestamp(latest_ts).isoformat() if latest_ts > 0 else ''
        ),
    }


def top_cited_papers(
    limit: int = 25,
    days: int = 0,
    since: str = '',
    until: str = '',
    db_path: str | None = None,
) -> list[dict]:
    """Return top-cited corpus papers enriched into card dictionaries."""
    from core.sanity import _row_to_card

    ensure_bootstrapped(db_path=db_path)
    with closing(connect(db_path)) as conn:
        eff_since = _resolve_since_date(conn, days, since)
        eff_until = until.strip()
        where = ['c.found = 1', 'c.citation_count > 0']
        params: list = []
        if eff_since:
            where.append('p.report_date >= ?')
            params.append(eff_since)
        if eff_until:
            where.append('p.report_date <= ?')
            params.append(eff_until)
        params.append(max(1, limit))

        rows = conn.execute(
            f"""
            SELECT p.*,
                   c.bibcode AS c_bibcode,
                   c.citation_count AS c_count,
                   c.read_count AS c_reads,
                   c.refereed AS c_refereed,
                   c.pub AS c_pub,
                   c.source AS c_source,
                   c.fetched_ts AS c_fetched_ts
            FROM citations c
            JOIN papers p ON p.pid = c.pid
            WHERE {' AND '.join(where)}
            ORDER BY c.citation_count DESC, c.read_count DESC, p.report_date DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    cards: list[dict] = []
    for r in rows:
        rd = dict(r)
        pid = rd['pid']
        bib = rd['c_bibcode'] or ''
        cite_info = {
            'citation_count': int(rd['c_count'] or 0),
            'read_count': int(rd['c_reads'] or 0),
            'bibcode': bib,
            'refereed': bool(rd['c_refereed']),
            'pub': rd['c_pub'] or '',
            'source': rd['c_source'] or '',
            'scix_url': scix_abs_url(pid, bib),
        }
        cards.append(_row_to_card(rd, cite_info=cite_info))
    return cards
