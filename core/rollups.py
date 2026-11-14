"""Week, month, and year corpus aggregation and statistics rollups."""

from collections import Counter
from contextlib import closing, suppress
import datetime
import json
from urllib.parse import quote_plus

from core.citations import get_citation_map
from core.corpus import connect, ensure_bootstrapped
from core.sanity import _parse_json_list, _row_to_card
from core.topics import TOPICS


def iso_week_key(date_str: str) -> str:
    """Convert ``YYYY-MM-DD`` into ISO week identifier ``YYYY-Www``."""
    d = datetime.date.fromisoformat(date_str)
    iso_y, iso_w, _ = d.isocalendar()
    return f'{iso_y}-W{iso_w:02d}'


def week_date_span(week_key: str) -> tuple[datetime.date, datetime.date]:
    """Return ``(monday, sunday)`` dates for an ISO week key ``YYYY-Www``."""
    year_s, week_s = week_key.split('-W')
    monday = datetime.date.fromisocalendar(int(year_s), int(week_s), 1)
    return monday, monday + datetime.timedelta(days=6)


def format_period_label(kind: str, key: str) -> str:
    """Return a concise human-readable label for a week, month, or year key."""
    if not key:
        return ''
    if kind == 'week':
        try:
            mon, sun = week_date_span(key)
            return f'{key} ({mon:%b %d} - {sun:%b %d, %Y})'
        except ValueError:
            return key
    if kind == 'month':
        try:
            y_s, m_s = key.split('-')
            d = datetime.date(int(y_s), int(m_s), 1)
            return f'{key} · {d:%B %Y}'
        except ValueError:
            return key
    return key


def list_periods(db_path: str | None = None) -> dict:
    """List distinct ISO weeks, months, and years present in the paper corpus."""
    ensure_bootstrapped(db_path=db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            'SELECT DISTINCT report_date FROM papers '
            'WHERE report_date != "" ORDER BY report_date DESC'
        ).fetchall()
    dates = [r['report_date'] for r in rows]
    weeks_set: set[str] = set()
    months_set: set[str] = set()
    years_set: set[str] = set()
    for d in dates:
        with suppress(ValueError):
            weeks_set.add(iso_week_key(d))
        if len(d) >= 7:
            months_set.add(d[:7])
        if len(d) >= 4:
            years_set.add(d[:4])

    weeks = sorted(weeks_set, reverse=True)
    months = sorted(months_set, reverse=True)
    years = sorted(years_set, reverse=True)
    return {
        'dates': dates,
        'weeks': weeks,
        'months': months,
        'years': years,
        'latest_date': dates[0] if dates else '',
        'latest_week': weeks[0] if weeks else '',
        'latest_month': months[0] if months else '',
        'latest_year': years[0] if years else '',
    }


def _fetch_period_rows(kind: str, key: str, db_path: str | None) -> list[dict]:
    if not key:
        return []
    with closing(connect(db_path)) as conn:
        if kind == 'week':
            mon, sun = week_date_span(key)
            rows = conn.execute(
                'SELECT * FROM papers WHERE report_date >= ? AND report_date <= ? '
                'ORDER BY report_date DESC, has_report DESC, number ASC, pid DESC',
                (mon.isoformat(), sun.isoformat()),
            ).fetchall()
        elif kind in ('month', 'year'):
            rows = conn.execute(
                'SELECT * FROM papers WHERE report_date LIKE ? '
                'ORDER BY report_date DESC, has_report DESC, number ASC, pid DESC',
                (f'{key}-%',),
            ).fetchall()
        else:
            raise ValueError(f'Unsupported rollup kind: {kind!r}')
    return [dict(r) for r in rows]


def build_rollup(
    kind: str = 'month',
    key: str = '',
    db_path: str | None = None,
) -> dict:
    """Compute KPIs, topic breakdown, timeline, objects, citations, and highlights.

    Args:
        kind: One of ``'week'``, ``'month'``, or ``'year'``.
        key: Period identifier (e.g. ``'2026-W46'``, ``'2026-11'``, ``'2026'``);
            defaults to the latest available period for ``kind``.
        db_path: Optional SQLite corpus path override.

    Returns:
        Dictionary ready for HTML template or JSON serialization.
    """
    if kind not in ('week', 'month', 'year'):
        raise ValueError(f'Unsupported rollup kind: {kind!r}')

    periods = list_periods(db_path=db_path)
    keys_for_kind = periods[f'{kind}s']
    resolved_key = key.strip() if key else ''
    if not resolved_key:
        resolved_key = periods[f'latest_{kind}']

    prev_key = ''
    next_key = ''
    if resolved_key in keys_for_kind:
        idx = keys_for_kind.index(resolved_key)
        if idx + 1 < len(keys_for_kind):
            prev_key = keys_for_kind[idx + 1]
        if idx > 0:
            next_key = keys_for_kind[idx - 1]

    rows = _fetch_period_rows(kind, resolved_key, db_path)
    cite_map = get_citation_map([r['pid'] for r in rows], db_path=db_path)

    total_papers = len(rows)
    digest_papers = sum(1 for r in rows if r.get('has_report'))
    report_dates = {r['report_date'] for r in rows if r.get('has_report') and r.get('report_date')}
    if not report_dates:
        report_dates = {r['report_date'] for r in rows if r.get('report_date')}
    highlight_rows = [r for r in rows if r.get('is_highlight')]
    accepted_or_published = sum(
        1 for r in rows if (r.get('pub_status') or '') in ('accepted', 'published')
    )
    cited_rows = [
        (r, cite_map[r['pid']])
        for r in rows
        if r['pid'] in cite_map and int(cite_map[r['pid']].get('citation_count') or 0) > 0
    ]
    total_citations = sum(int(ci.get('citation_count') or 0) for _, ci in cited_rows)

    topic_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    object_counts: Counter[str] = Counter()
    matrix_counts: dict[str, Counter[str]] = {}
    by_day: dict[str, dict] = {}
    by_week: dict[str, dict] = {}
    by_month: dict[str, dict] = {}

    for r in rows:
        d_str = r.get('report_date') or ''
        col_key = ''
        if d_str:
            if kind == 'week':
                col_key = d_str
            elif kind == 'month':
                with suppress(ValueError):
                    col_key = iso_week_key(d_str)
            else:
                col_key = d_str[:7]

        for slug in _parse_json_list(r.get('topic_slugs_json')):
            topic_counts[slug] += 1
            if col_key:
                matrix_counts.setdefault(slug, Counter())[col_key] += 1
        mtag = (r.get('method_tag') or '').strip()
        if mtag:
            method_counts[mtag] += 1
        for obj in _parse_json_list(r.get('objects_json')):
            object_counts[obj] += 1

        if d_str:
            day_bucket = by_day.setdefault(d_str, {'total': 0, 'digested': 0, 'highlights': 0})
            day_bucket['total'] += 1
            if r.get('has_report'):
                day_bucket['digested'] += 1
            if r.get('is_highlight'):
                day_bucket['highlights'] += 1

            with suppress(ValueError):
                w_str = iso_week_key(d_str)
                w_bucket = by_week.setdefault(w_str, {'total': 0, 'digested': 0})
                w_bucket['total'] += 1
                if r.get('has_report'):
                    w_bucket['digested'] += 1

            m_str = d_str[:7]
            m_bucket = by_month.setdefault(
                m_str, {'total': 0, 'digested': 0, 'highlights': 0, 'days': set()}
            )
            m_bucket['total'] += 1
            if r.get('has_report'):
                m_bucket['digested'] += 1
                m_bucket['days'].add(d_str)
            if r.get('is_highlight'):
                m_bucket['highlights'] += 1

    max_topic = max(topic_counts.values(), default=0) or 1
    denom_digest = digest_papers or total_papers or 1
    topic_breakdown: list[dict] = []
    for spec in TOPICS:
        cnt = int(topic_counts.get(spec.slug, 0))
        if cnt <= 0:
            continue
        topic_breakdown.append(
            {
                'slug': spec.slug,
                'short': spec.short,
                'label': spec.label,
                'label_en': spec.label_en,
                'count': cnt,
                'pct_of_max': round(100.0 * cnt / max_topic, 1),
                'pct_of_digest': round(100.0 * cnt / denom_digest, 1),
                'search_href': f'/search?topic={spec.slug}',
            }
        )
    topic_breakdown.sort(key=lambda item: item['count'], reverse=True)

    method_total = sum(method_counts.values()) or 1
    method_breakdown = [
        {
            'method': m,
            'count': int(method_counts[m]),
            'pct': round(100.0 * method_counts[m] / method_total, 1),
            'search_href': f'/search?method={m}',
        }
        for m in ('Modeling', 'Observation', 'Simulation', 'Theory')
        if method_counts.get(m, 0) > 0
    ]

    timeline: list[dict] = []
    if kind in ('week', 'month'):
        max_bar = max((v['total'] for v in by_day.values()), default=0) or 1
        for d_str in sorted(by_day):
            info = by_day[d_str]
            try:
                dt = datetime.date.fromisoformat(d_str)
                lbl = f'{dt:%m-%d %a}'
            except ValueError:
                lbl = d_str
            timeline.append(
                {
                    'date': d_str,
                    'label': lbl,
                    'total': info['total'],
                    'digested': info['digested'],
                    'highlights': info['highlights'],
                    'pct_of_max': max(8.0, round(100.0 * info['total'] / max_bar, 1)),
                    'href': f'/r/{d_str}'
                    if info['digested'] > 0
                    else f'/search?q={quote_plus(d_str)}',
                }
            )
    else:
        max_bar = max((v['total'] for v in by_month.values()), default=0) or 1
        for m_str in sorted(by_month):
            info = by_month[m_str]
            timeline.append(
                {
                    'month': m_str,
                    'label': m_str,
                    'total': info['total'],
                    'digested': info['digested'],
                    'highlights': info['highlights'],
                    'days': len(info['days']),
                    'pct_of_max': max(8.0, round(100.0 * info['total'] / max_bar, 1)),
                    'href': f'/summary/month/{m_str}',
                }
            )

    top_objects = [
        {'name': obj, 'count': cnt, 'href': f'/search?q={quote_plus(obj)}'}
        for obj, cnt in object_counts.most_common(18)
    ]

    cited_rows.sort(
        key=lambda pair: (
            int(pair[1].get('citation_count') or 0),
            int(pair[1].get('read_count') or 0),
            pair[0].get('report_date') or '',
        ),
        reverse=True,
    )
    top_cited = [_row_to_card(r, cite_info=ci) for r, ci in cited_rows[:15]]

    highlight_rows.sort(
        key=lambda r: (r.get('report_date') or '', -int(r.get('number') or 0)),
        reverse=True,
    )
    highlights = [_row_to_card(r, cite_info=cite_map.get(r['pid'])) for r in highlight_rows[:50]]

    matrix_columns: list[dict] = []
    if kind == 'week':
        for d_str in sorted(by_day):
            info = by_day[d_str]
            try:
                dt = datetime.date.fromisoformat(d_str)
                col_lbl = f'{dt:%m-%d %a}'
            except ValueError:
                col_lbl = d_str
            matrix_columns.append(
                {
                    'key': d_str,
                    'label': col_lbl,
                    'href': f'/r/{d_str}'
                    if info['digested'] > 0
                    else f'/search?q={quote_plus(d_str)}',
                }
            )
    elif kind == 'month':
        for w_str in sorted(by_week):
            short_w = w_str.split('-')[1] if '-' in w_str else w_str
            matrix_columns.append(
                {
                    'key': w_str,
                    'label': short_w,
                    'href': f'/summary/week/{w_str}',
                }
            )
    else:
        for m_str in sorted(by_month):
            try:
                dt = datetime.date.fromisoformat(f'{m_str}-01')
                col_lbl = f'{dt:%b}'
            except ValueError:
                col_lbl = m_str
            matrix_columns.append(
                {
                    'key': m_str,
                    'label': col_lbl,
                    'href': f'/summary/month/{m_str}',
                }
            )

    max_cell = max((cnt for c in matrix_counts.values() for cnt in c.values()), default=0) or 1
    matrix_rows: list[dict] = []
    for t in topic_breakdown:
        per_col = matrix_counts.get(t['slug'], {})
        cells: list[dict] = []
        for col in matrix_columns:
            c = int(per_col.get(col['key'], 0))
            lvl = 0 if c == 0 else 1 + min(3, int(3 * c / max_cell))
            cells.append({'key': col['key'], 'count': c, 'level': lvl})
        matrix_rows.append(
            {
                'slug': t['slug'],
                'short': t['short'],
                'label': t['label'],
                'label_en': t['label_en'],
                'search_href': t['search_href'],
                'total': t['count'],
                'cells': cells,
            }
        )

    return {
        'kind': kind,
        'key': resolved_key,
        'label': format_period_label(kind, resolved_key),
        'available_keys': keys_for_kind,
        'prev_key': prev_key,
        'next_key': next_key,
        'periods': {
            'weeks': periods['weeks'],
            'months': periods['months'],
            'years': periods['years'],
        },
        'kpis': {
            'total_papers': total_papers,
            'digest_papers': digest_papers,
            'report_days': len(report_dates),
            'highlight_count': len(highlight_rows),
            'cited_papers': len(cited_rows),
            'total_citations': total_citations,
            'accepted_or_published': accepted_or_published,
        },
        'topic_breakdown': topic_breakdown,
        'topic_matrix': {
            'columns': matrix_columns,
            'rows': matrix_rows,
        },
        'method_breakdown': method_breakdown,
        'timeline': timeline,
        'top_objects': top_objects,
        'top_cited': top_cited,
        'highlights': highlights,
        'highlights_total': len(highlight_rows),
        'meta_json': json.dumps(
            {'kind': kind, 'key': resolved_key},
            ensure_ascii=False,
        ),
    }
