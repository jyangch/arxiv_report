"""Privacy-safe static HTML + client-search site exporter."""

from contextlib import closing
import glob
from html import escape as _html_escape
import json
import os
import re
from urllib.parse import quote

from core import config, rollups
from core.citations import get_citation_map
from core.corpus import connect, default_db_path, ensure_bootstrapped
from core.fragments import (
    extract_fragment_from_report_html,
    iter_paper_item_spans,
    load_fragment,
    parse_fragment,
)
from core.pub_status import normalize_status_label
from core.render import (
    STARRED_REPORT,
    _write_starred_html,
    render_report_html,
)
from core.sanity import _parse_json_list
from core.topics import TOPICS, topic_chip_css

_REPORT_FILE_RE = re.compile(r'arXiv_astro_ph_HE_daily_report_(\d{4}-\d{2}-\d{2})\.html$')
_HEAD_TAG_RE = re.compile(r'<head[^>]*>', re.IGNORECASE)
_STATIC_FLAGS_SCRIPT = (
    '<script>window.__ARXIV_CAN_WRITE__ = false; window.__ARXIV_STATIC_SITE__ = true;</script>'
)


def _write_atomic(path: str, text: str, encoding: str = 'utf-8') -> None:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp_path = f'{path}.tmp'
    with open(tmp_path, 'w', encoding=encoding) as f:
        f.write(text)
    os.replace(tmp_path, path)


def _inject_static_flags(html_text: str) -> str:
    patched, count = _HEAD_TAG_RE.subn(
        lambda m: m.group(0) + _STATIC_FLAGS_SCRIPT, html_text, count=1
    )
    if count == 0:
        return _STATIC_FLAGS_SCRIPT + html_text
    return patched


def _resolve_db_path(reports_dir: str, db_path: str | None) -> str | None:
    if db_path:
        return db_path
    if os.path.normpath(reports_dir) != 'reports':
        return os.path.join(reports_dir, '.data', 'papers.db')
    return default_db_path()


def _export_daily_reports(
    reports_dir: str,
    out_dir: str,
    resolved_db: str | None,
) -> list[str]:
    dates_set: set[str] = set()
    frag_dir = os.path.join(reports_dir, 'fragments')
    if os.path.isdir(frag_dir):
        for p in glob.glob(os.path.join(frag_dir, '*.html')):
            base = os.path.basename(p)
            if base.endswith('.meta.json'):
                continue
            stem = os.path.splitext(base)[0]
            if re.match(r'^\d{4}-\d{2}-\d{2}$', stem):
                dates_set.add(stem)

    for p in glob.glob(os.path.join(reports_dir, 'arXiv_astro_ph_HE_daily_report_*.html')):
        m = _REPORT_FILE_RE.search(os.path.basename(p))
        if m:
            dates_set.add(m.group(1))

    exported_files: set[str] = set()
    exported_dates: list[str] = []
    for date_str in sorted(dates_set, reverse=True):
        loaded = load_fragment(date_str, reports_dir=reports_dir)
        if loaded is not None:
            frag_html, provider = loaded
        else:
            src_report = os.path.join(
                reports_dir, f'arXiv_astro_ph_HE_daily_report_{date_str}.html'
            )
            if not os.path.exists(src_report):
                continue
            with open(src_report, encoding='utf-8-sig') as f:
                raw_report = f.read()
            frag_html, provider = extract_fragment_from_report_html(raw_report)

        parsed = parse_fragment(frag_html)
        n_papers = len(iter_paper_item_spans(frag_html)) or len(parsed.get('items', {}))
        full_html = render_report_html(
            date_str=date_str,
            paper_count=n_papers,
            provider=provider,
            body_content=frag_html,
            db_path=resolved_db,
        )
        full_html = _inject_static_flags(full_html)
        fname = f'arXiv_astro_ph_HE_daily_report_{date_str}.html'
        _write_atomic(os.path.join(out_dir, fname), full_html, encoding='utf-8-sig')
        exported_files.add(fname)
        exported_dates.append(date_str)

    for stale in glob.glob(os.path.join(out_dir, 'arXiv_astro_ph_HE_daily_report_*.html')):
        if os.path.basename(stale) not in exported_files:
            os.remove(stale)

    return exported_dates


def _export_starred_page(out_dir: str) -> None:
    import core.render as render_mod

    orig_dir = render_mod.REPORTS_DIR
    try:
        render_mod.REPORTS_DIR = out_dir
        starred_path = _write_starred_html('local')
    finally:
        render_mod.REPORTS_DIR = orig_dir

    with open(starred_path, encoding='utf-8-sig') as f:
        raw_html = f.read()
    _write_atomic(starred_path, _inject_static_flags(raw_html), encoding='utf-8-sig')


def _render_static_rollup_cards(items: list[dict]) -> str:
    if not items:
        return '<p class="muted">No entries.</p>'
    blocks: list[str] = []
    for p in items:
        pid = p['pid']
        title_zh = _html_escape(p.get('title_zh') or '')
        title_en = _html_escape(p.get('title') or '')
        authors = _html_escape(p.get('authors') or '')
        c_cnt = p.get('citation_count') or 0
        cite_html = ''
        if c_cnt > 0:
            cite_html = (
                f'<a class="scix-tag is-cited" href="{_html_escape(p["scix_url"])}" '
                f'target="_blank" rel="noopener noreferrer">SciX · {c_cnt}</a>'
            )
        day_href = (
            f'../index.html#day/{_html_escape(p["report_date"])}'
            + (f'/p{p["number"]}' if p.get('number') else '')
            if p.get('report_date')
            else _html_escape(p.get('url') or '#')
        )
        sim_href = f'../index.html#similar/{quote(pid, safe="")}'
        summary_raw = str(p.get('summary') or '').strip()
        summary = p.get('result_zh') or p.get('result_en') or summary_raw[:260]
        if p.get('result_zh'):
            summary_en = (p.get('result_en') or '').strip() or (
                summary_raw[:260] + ('...' if len(summary_raw) > 260 else '')
            )
        else:
            summary_en = ''
        has_title_pair = bool(title_zh and title_en)
        has_sum_pair = bool(summary and summary_en and summary_en != summary)
        chips = ''.join(
            f'<span class="card-topic-pill" data-topic="{_html_escape(tc["slug"])}">{_html_escape(tc["short"])}</span>'
            for tc in p.get('topic_chips') or []
        )
        objs = ''.join(
            f'<a class="obj-tag" href="../index.html#search/{quote(o, safe="")}" target="_top">'
            f'⌖ {_html_escape(o)}</a>'
            for o in (p.get('objects') or [])[:6]
        )
        title_zh_cls = 'r-lang-zh has-en-pair' if has_title_pair else 'r-lang-zh'
        title_en_cls = 'en-title r-lang-en has-zh-pair' if has_title_pair else 'en-title r-lang-en'
        sum_zh_cls = 'summary r-lang-zh has-en-pair' if has_sum_pair else 'summary'
        blocks.append(
            f"""
            <article class="card">
              <div class="card-head">
                <a class="meta-pill" href="{day_href}" target="_top">{_html_escape(p.get('report_date') or 'corpus')} · arXiv:{_html_escape(p.get('arxiv_id') or pid)}</a>
                <a class="meta-pill" href="{sim_href}" target="_top">☍ Similar</a>
                {cite_html}
              </div>
              {f'<h4 class="{title_zh_cls}">{title_zh}</h4>' if title_zh else ''}
              {f'<p class="{title_en_cls}">{title_en}</p>' if title_en else ''}
              {f'<p class="authors">{authors}</p>' if authors else ''}
              {f'<p class="{sum_zh_cls}">{_html_escape(summary)}</p>' if summary else ''}
              {f'<p class="summary r-lang-en has-zh-pair">{_html_escape(summary_en)}</p>' if has_sum_pair else ''}
              {f'<div class="paper-tags">{chips}{objs}</div>' if (chips or objs) else ''}
            </article>
            """
        )
    return '\n'.join(blocks)


def _render_static_rollup_html(data: dict, title: str) -> str:
    kpis = data['kpis']
    topic_rows = '\n'.join(
        f'<a class="topic-bar-row" href="../index.html#search/{quote("topic:" + t["slug"], safe="")}" target="_top">'
        f'<span class="card-topic-pill" data-topic="{_html_escape(t["slug"])}">{_html_escape(t["short"])}</span>'
        f'<span class="bar-track"><span class="bar-fill" data-topic="{_html_escape(t["slug"])}" style="width:{t["pct_of_max"]}%"></span></span>'
        f'<span class="bar-val">{t["count"]}</span></a>'
        for t in data['topic_breakdown']
    )

    if data['kind'] in ('week', 'month'):
        timeline_html = '\n'.join(
            f'<a class="t-col" href="../index.html#day/{_html_escape(s["date"])}" target="_top" '
            f'title="{_html_escape(s["label"])}: {s["total"]} papers">'
            f'<span class="t-num">{s["total"]}</span>'
            f'<span class="t-track"><span class="t-fill" style="height:{s["pct_of_max"]}%"></span></span>'
            f'<span class="t-lbl">{_html_escape(s["label"])}</span></a>'
            for s in data['timeline']
        )
    else:
        timeline_html = '\n'.join(
            f'<a class="t-col" href="../index.html#rollup/month/{_html_escape(s["month"])}" target="_top" '
            f'title="{_html_escape(s["label"])}: {s["total"]} papers">'
            f'<span class="t-num">{s["total"]}</span>'
            f'<span class="t-track"><span class="t-fill" style="height:{s["pct_of_max"]}%"></span></span>'
            f'<span class="t-lbl">{_html_escape(s["label"])}</span></a>'
            for s in data['timeline']
        )

    methods_html = ''.join(
        f'<a class="obj-tag" href="../index.html#search/{quote("method:" + m["method"], safe="")}" target="_top">'
        f'{_html_escape(m["method"])} · {m["count"]} ({m["pct"]}%)</a>'
        for m in (data.get('method_breakdown') or [])
    )
    methods_section = (
        f'<h3 style="margin-top:16px">Research Methods</h3><div class="tags-wrap">{methods_html}</div>'
        if methods_html
        else ''
    )
    objs_html = ''.join(
        f'<a class="obj-tag" href="../index.html#search/{quote(o["name"], safe="")}" target="_top">'
        f'⌖ {_html_escape(o["name"])} ({o["count"]})</a>'
        for o in data['top_objects']
    )
    prev_link = (
        f'<a class="nav-btn" href="../index.html#rollup/{data["kind"]}/{_html_escape(data["prev_key"])}" target="_top">← {_html_escape(data["prev_key"])}</a>'
        if data['prev_key']
        else ''
    )
    next_link = (
        f'<a class="nav-btn" href="../index.html#rollup/{data["kind"]}/{_html_escape(data["next_key"])}" target="_top">{_html_escape(data["next_key"])} →</a>'
        if data['next_key']
        else ''
    )
    top_cited_html = (
        _render_static_rollup_cards(data['top_cited'])
        if data['top_cited']
        else '<p class="muted">No citation records in this period yet.</p>'
    )
    highlights_html = _render_static_rollup_cards(data['highlights'])

    matrix = data.get('topic_matrix') or {}
    matrix_cols = matrix.get('columns') or []
    matrix_rows = matrix.get('rows') or []
    if matrix_cols and matrix_rows:
        kind_val = data.get('kind', 'month')

        def _col_hash(col_key: str) -> str:
            esc_key = _html_escape(col_key)
            if kind_val == 'week':
                return f'day/{esc_key}'
            if kind_val == 'month':
                return f'rollup/week/{esc_key}'
            return f'rollup/month/{esc_key}'

        th_cells = ''.join(
            f'<th scope="col"><a href="../index.html#{_col_hash(c["key"])}" '
            f'target="_top" title="{_html_escape(c["key"])}">{_html_escape(c["label"])}</a></th>'
            for c in matrix_cols
        )
        body_rows_parts = []
        for mr in matrix_rows:
            cells_html = ''.join(
                f'<td class="matrix-cell heat-{c["level"]}" title="{_html_escape(mr["short"])} · {_html_escape(c["key"])}: {c["count"]}">'
                + (
                    f'<a href="../index.html#{_col_hash(c["key"])}" target="_top">{c["count"]}</a>'
                    if c['count']
                    else '·'
                )
                + '</td>'
                for c in mr['cells']
            )
            body_rows_parts.append(
                f'<tr><th scope="row"><a class="card-topic-pill" data-topic="{_html_escape(mr["slug"])}" '
                f'href="../index.html#search/{quote("topic:" + mr["slug"], safe="")}" target="_top">'
                f'{_html_escape(mr["short"])}</a></th>{cells_html}'
                f'<td class="matrix-total">{mr["total"]}</td></tr>'
            )
        matrix_html = (
            '<section class="panel matrix-card">'
            '<h3>Topic × Period Activity Matrix</h3>'
            '<div class="matrix-scroll"><table class="matrix-table">'
            f'<thead><tr><th scope="col">Topic</th>{th_cells}<th scope="col">Total</th></tr></thead>'
            f'<tbody>{"".join(body_rows_parts)}</tbody>'
            '</table></div></section>'
        )
    else:
        matrix_html = ''

    raw_label = data['label'] or ''
    if ' (' in raw_label:
        lead, rest = raw_label.split(' (', 1)
        heading_html = (
            f'{_html_escape(lead)} '
            f'<span class="rollup-title-sub">({_html_escape(rest.replace(" - ", " – "))}</span>'
        )
    elif ' · ' in raw_label:
        lead, rest = raw_label.split(' · ', 1)
        heading_html = (
            f'{_html_escape(lead)} <span class="rollup-title-sub">· {_html_escape(rest)}</span>'
        )
    else:
        heading_html = _html_escape(raw_label or 'No data')

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{_html_escape(data['label'] or title)} · {_html_escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Newsreader:ital,opsz,wght@0,6..72,400..600;1,6..72,400&family=Outfit:wght@300..700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" />
<style>
:root {{
  color-scheme: light dark;
  --bg: #f8f8f6; --surface: #ffffff; --surface-alt: #f1f2ee;
  --text: #181a1f; --text-muted: #5a606c; --primary: #1858a8;
  --primary-soft: #e6effa; --border: #e4e5e0;
  --heat-1: #e6effa; --heat-2: #bfd6f6; --heat-3: #6ca3e6; --heat-4: #1858a8;
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --bg: #0d1014; --surface: #14181f; --surface-alt: #1b2029;
  --text: #e2e6ed; --text-muted: #8c96a5; --primary: #68a6f8;
  --primary-soft: rgba(104,166,248,0.14); --border: #252c37;
  --heat-1: rgba(104,166,248,0.14); --heat-2: rgba(104,166,248,0.28);
  --heat-3: rgba(104,166,248,0.50); --heat-4: #68a6f8;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #0d1014; --surface: #14181f; --surface-alt: #1b2029;
    --text: #e2e6ed; --text-muted: #8c96a5; --primary: #68a6f8;
    --primary-soft: rgba(104,166,248,0.14); --border: #252c37;
    --heat-1: rgba(104,166,248,0.14); --heat-2: rgba(104,166,248,0.28);
    --heat-3: rgba(104,166,248,0.50); --heat-4: #68a6f8;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text); font-family: "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Noto Sans CJK SC", sans-serif; font-size: 14.5px; line-height: 1.58; -webkit-font-smoothing: antialiased; }}
.wrap {{ max-width: 1000px; margin: 0 auto; padding: 20px 22px 60px; }}
.head {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-end; gap: 12px; margin-bottom: 18px; padding-bottom: 14px; border-bottom: 2px solid var(--border); }}
.kicker {{ margin: 0; font-family: "JetBrains Mono", monospace; font-size: 0.72rem; letter-spacing: 0.12em; text-transform: uppercase; color: var(--text-muted); font-weight: 700; }}
h1 {{ margin: 4px 0 0; font-family: "Outfit", "Avenir Next", "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, sans-serif; font-size: 1.5rem; font-weight: 580; letter-spacing: -0.022em; line-height: 1.15; font-variant-numeric: lining-nums proportional-nums; }}
.rollup-title-sub {{ font-weight: 400; font-size: 0.93em; letter-spacing: -0.014em; color: var(--text-muted); }}
.nav-row {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.nav-btn, .meta-pill {{ display: inline-flex; align-items: center; padding: 4px 11px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface); color: var(--text-muted); font-family: "JetBrains Mono", monospace; font-size: 0.78rem; font-weight: 600; text-decoration: none; font-variant-numeric: tabular-nums; }}
.nav-btn:hover, .meta-pill:hover {{ border-color: var(--primary); color: var(--primary); }}
.kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }}
.kpi {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 13px 15px; }}
.kpi p {{ margin: 0; }}
.kpi-val {{ font-family: "JetBrains Mono", monospace; font-variant-numeric: tabular-nums; font-size: 1.55rem; font-weight: 800; margin: 4px 0 !important; }}
.kpi-sub {{ font-size: 0.75rem; color: var(--text-muted); }}
.grid {{ display: grid; grid-template-columns: 1fr 1.15fr; gap: 14px; margin-bottom: 18px; }}
.panel, .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; }}
.card {{ margin-bottom: 12px; }}
.matrix-card {{ margin-bottom: 18px; }}
.matrix-scroll {{ overflow-x: auto; }}
.matrix-table {{ width: 100%; border-collapse: collapse; font-size: 0.76rem; }}
.matrix-table th, .matrix-table td {{ padding: 4px 6px; border: 1px solid var(--border); text-align: center; white-space: nowrap; }}
.matrix-table thead th {{ background: var(--surface-alt); color: var(--text-muted); font-family: "JetBrains Mono", monospace; font-size: 0.72rem; font-weight: 600; }}
.matrix-table thead th a {{ color: inherit; text-decoration: none; }}
.matrix-table thead th a:hover {{ color: var(--primary); }}
.matrix-table tbody th {{ text-align: left; background: var(--surface); position: sticky; left: 0; z-index: 1; }}
.matrix-cell {{ font-family: "JetBrains Mono", monospace; font-variant-numeric: tabular-nums; font-weight: 600; }}
.matrix-cell a {{ display: block; color: inherit; text-decoration: none; }}
.matrix-cell.heat-0 {{ color: var(--text-muted); opacity: 0.45; }}
.matrix-cell.heat-1 {{ background: var(--heat-1); }}
.matrix-cell.heat-2 {{ background: var(--heat-2); }}
.matrix-cell.heat-3 {{ background: var(--heat-3); color: #ffffff; }}
.matrix-cell.heat-4 {{ background: var(--heat-4); color: #ffffff; }}
:root[data-theme="dark"] .matrix-cell.heat-3, :root[data-theme="dark"] .matrix-cell.heat-4 {{ color: #0d1014; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) .matrix-cell.heat-3, :root:not([data-theme="light"]) .matrix-cell.heat-4 {{ color: #0d1014; }} }}
.matrix-total {{ font-family: "JetBrains Mono", monospace; font-weight: 700; color: var(--text); background: var(--surface-alt); }}
.panel h3 {{ margin: 0 0 12px; font-size: 0.96rem; }}
.topic-bar-row {{ display: grid; grid-template-columns: 80px 1fr 36px; align-items: center; gap: 8px; margin-bottom: 7px; text-decoration: none; color: inherit; }}
.topic-bar-row .card-topic-pill {{ justify-content: center; }}
.bar-track {{ height: 8px; border-radius: 999px; background: var(--surface-alt); overflow: hidden; }}
.bar-fill {{ display: block; height: 100%; background: var(--primary); border-radius: 999px; }}
.bar-fill[data-topic="grb"] {{ background: #d97706; }}
.bar-fill[data-topic="efxt"] {{ background: #ea580c; }}
.bar-fill[data-topic="frb"] {{ background: #db2777; }}
.bar-fill[data-topic="sne_kn"] {{ background: #dc2626; }}
.bar-fill[data-topic="tde_qpe"] {{ background: #e11d48; }}
.bar-fill[data-topic="ns_psr"] {{ background: #0d9488; }}
.bar-fill[data-topic="xrb_wd"] {{ background: #0284c7; }}
.bar-fill[data-topic="gw"] {{ background: #7c3aed; }}
.bar-fill[data-topic="bh_gr"] {{ background: #2563eb; }}
.bar-fill[data-topic="agn"] {{ background: #4f46e5; }}
.bar-fill[data-topic="snr_icm"] {{ background: #65a30d; }}
.bar-fill[data-topic="vhe"] {{ background: #059669; }}
.bar-fill[data-topic="cr_nu"] {{ background: #16a34a; }}
.bar-fill[data-topic="dm_alp"] {{ background: #9333ea; }}
.bar-fill[data-topic="plasma"] {{ background: #0891b2; }}
.bar-fill[data-topic="inst_ml"] {{ background: #64748b; }}
.bar-val {{ text-align: right; font-family: "JetBrains Mono", monospace; font-variant-numeric: tabular-nums; font-size: 0.76rem; color: var(--text-muted); font-weight: 600; }}
.t-bars {{ display: flex; align-items: flex-end; gap: 6px; overflow-x: auto; padding-bottom: 4px; }}
.t-col {{ flex: 1 0 38px; min-width: 38px; display: flex; flex-direction: column; align-items: center; gap: 4px; text-decoration: none; color: inherit; }}
.t-num {{ font-family: "JetBrains Mono", monospace; font-size: 0.68rem; color: var(--text-muted); }}
.t-track {{ width: 100%; height: 74px; border-radius: 6px; background: var(--surface-alt); display: flex; align-items: flex-end; padding: 2px; }}
.t-fill {{ width: 100%; background: var(--primary); border-radius: 4px; }}
.t-lbl {{ font-family: "JetBrains Mono", monospace; font-size: 0.66rem; color: var(--text-muted); white-space: nowrap; }}
.tags-wrap {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }}
.card-head {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }}
.card h4 {{ margin: 2px 0 4px; font-size: 1.14rem; font-weight: 800; letter-spacing: -0.01em; line-height: 1.4; }}
.en-title {{ margin: 0 0 6px; font-family: "Newsreader", Georgia, serif; font-size: 1.06rem; font-weight: 600; color: color-mix(in srgb, var(--text) 82%, var(--text-muted)); line-height: 1.4; }}
:root:not([data-lang="en"]) .r-lang-en.has-zh-pair {{ display: none; }}
:root[data-lang="en"] .r-lang-zh.has-en-pair {{ display: none; }}
:root[data-lang="en"] .en-title {{ margin: 2px 0 6px; font-size: 1.12rem; font-weight: 700; color: var(--text); }}
.authors {{ margin: 0 0 6px; color: var(--text-muted); font-size: 0.82rem; }}
.summary {{ margin: 0; font-size: 0.88rem; }}
.muted {{ color: var(--text-muted); font-size: 0.86rem; }}
{topic_chip_css()}
@media (max-width: 780px) {{
  .kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .grid {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <header class="head">
    <div>
      <p class="kicker">Period Rollup · {_html_escape(data['kind'].upper())}</p>
      <h1>{heading_html}</h1>
    </div>
    <div class="nav-row">{prev_link}{next_link}</div>
  </header>
  <section class="kpis">
    <div class="kpi"><p class="kpi-sub">Papers Indexed</p><p class="kpi-val">{kpis['total_papers']}</p><p class="kpi-sub">{kpis['accepted_or_published']} accepted / published</p></div>
    <div class="kpi"><p class="kpi-sub">Digested Papers</p><p class="kpi-val">{kpis['digest_papers']}</p><p class="kpi-sub">{kpis['report_days']} digest days</p></div>
    <div class="kpi"><p class="kpi-sub">★ Today's Highlights</p><p class="kpi-val">{kpis['highlight_count']}</p><p class="kpi-sub">Curated standout papers</p></div>
    <div class="kpi"><p class="kpi-sub">Citations Tracked</p><p class="kpi-val">{kpis['total_citations']}</p><p class="kpi-sub">{kpis['cited_papers']} cited papers</p></div>
  </section>
  {matrix_html}
  <section class="grid">
    <div class="panel"><h3>Topic Breakdown</h3>{topic_rows or '<p class="muted">No data</p>'}</div>
    <div class="panel"><h3>Publishing Timeline</h3><div class="t-bars">{timeline_html}</div>{methods_section}<h3 style="margin-top:16px">Top Astrophysical Objects</h3><div class="tags-wrap">{objs_html or '<p class="muted">No data</p>'}</div></div>
  </section>
  <section><h2>Top Cited Papers</h2>{top_cited_html}</section>
  <section><h2>★ Period Highlights ({data['highlights_total']})</h2>{highlights_html}</section>
</div>
<script>
(function() {{
  try {{
    var savedLang = localStorage.getItem('arxiv-report:lang');
    document.documentElement.setAttribute('data-lang', savedLang === 'en' ? 'en' : 'zh');
  }} catch (e) {{}}
  try {{
    var saved = localStorage.getItem('arxiv-report:theme');
    if (saved === 'light' || saved === 'dark') document.documentElement.setAttribute('data-theme', saved);
  }} catch (e) {{}}
  window.addEventListener('message', function(e) {{
    var msg = e && e.data;
    if (!msg) return;
    if (msg.type === 'arxiv-lang-change') {{
      document.documentElement.setAttribute('data-lang', msg.lang === 'en' ? 'en' : 'zh');
      return;
    }}
    if (msg.type !== 'arxiv-theme-change') return;
    if (msg.theme === 'light' || msg.theme === 'dark') document.documentElement.setAttribute('data-theme', msg.theme);
    else document.documentElement.removeAttribute('data-theme');
  }});
}})();
</script>
</body>
</html>"""


def _export_rollups(out_dir: str, db_path: str | None, title: str) -> dict[str, list[str]]:
    periods = rollups.list_periods(db_path=db_path)
    rollups_dir = os.path.join(out_dir, 'rollups')
    os.makedirs(rollups_dir, exist_ok=True)
    wanted: set[str] = set()
    for kind in ('week', 'month', 'year'):
        for key in periods[f'{kind}s']:
            data = rollups.build_rollup(kind=kind, key=key, db_path=db_path)
            fname = f'{kind}-{key}.html'
            _write_atomic(
                os.path.join(rollups_dir, fname),
                _render_static_rollup_html(data, title),
                encoding='utf-8',
            )
            wanted.add(fname)

    for stale in glob.glob(os.path.join(rollups_dir, '*.html')):
        if os.path.basename(stale) not in wanted:
            os.remove(stale)

    return {
        'weeks': periods['weeks'],
        'months': periods['months'],
        'years': periods['years'],
    }


def _export_search_index(out_dir: str, db_path: str | None) -> int:
    ensure_bootstrapped(db_path=db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            'SELECT pid, arxiv_id, title, title_zh, authors, summary, method_tag, '
            'pub_status, status_label, result_zh, result_en, topics_json, topic_slugs_json, '
            'objects_json, is_highlight, url, pdf_url, report_date, number, has_report '
            'FROM papers ORDER BY report_date DESC, has_report DESC, number ASC, pid DESC'
        ).fetchall()
    cite_map = get_citation_map([r['pid'] for r in rows], db_path=db_path)

    entries: list[dict] = []
    for r in rows:
        pid = r['pid']
        disp_id = r['arxiv_id'] or pid
        slugs = _parse_json_list(r['topic_slugs_json'])
        objs = _parse_json_list(r['objects_json'])
        snippet = (r['result_zh'] or '').strip()
        snippet_en = (r['result_en'] or '').strip()
        s_raw = str(r['summary'] or '').strip()
        if snippet and not snippet_en and s_raw:
            snippet_en = s_raw[:240] + ('...' if len(s_raw) > 240 else '')
        elif not snippet and snippet_en:
            snippet = snippet_en
            snippet_en = ''
        elif not snippet and s_raw:
            snippet = s_raw[:240] + ('...' if len(s_raw) > 240 else '')
        cinfo = cite_map.get(pid)
        entries.append(
            {
                'pid': pid,
                'arxiv_id': disp_id,
                'date': r['report_date'] or '',
                'number': int(r['number'] or 0),
                'has_report': bool(r['has_report']),
                'title': r['title'] or '',
                'title_zh': r['title_zh'] or '',
                'authors': r['authors'] or '',
                'method': r['method_tag'] or '',
                'status': r['pub_status'] or 'submitted',
                'status_label': normalize_status_label(r['status_label'] or ''),
                'topics': slugs,
                'objects': objs,
                'snippet': snippet,
                'snippet_en': snippet_en,
                'is_highlight': bool(r['is_highlight']),
                'citation_count': cinfo['citation_count'] if cinfo else 0,
                'scix_url': cinfo['scix_url']
                if cinfo
                else f'https://scixplorer.org/abs/arXiv:{pid}/abstract',
                'url': r['url'] or f'https://arxiv.org/abs/{disp_id}',
                'pdf_url': r['pdf_url'] or f'https://arxiv.org/pdf/{disp_id}',
            }
        )

    _write_atomic(
        os.path.join(out_dir, 'search_index.json'),
        json.dumps(entries, ensure_ascii=False),
        encoding='utf-8',
    )
    return len(entries)


def _render_static_index_html(
    *,
    site_title: str,
    dates: list[str],
    periods: dict[str, list[str]],
    paper_count: int,
) -> str:
    topic_meta = [{'slug': t.slug, 'short': t.short, 'label': t.label_en} for t in TOPICS]
    boot_data = json.dumps(
        {
            'dates': dates,
            'periods': periods,
            'topics': topic_meta,
            'starred': STARRED_REPORT,
            'paperCount': paper_count,
        },
        ensure_ascii=False,
    ).replace('</', '<\\/')

    topic_options = '\n'.join(
        f'<option value="{_html_escape(t.slug)}">{_html_escape(t.short)}</option>' for t in TOPICS
    )
    topic_map_css = '\n'.join(
        f'.topic-pill[data-topic="{t.slug}"] {{ background: {t.light_bg}; color: {t.light_fg}; }}'
        for t in TOPICS
    )
    dark_topic_css = '\n'.join(
        f':root[data-theme="dark"] .topic-pill[data-topic="{t.slug}"] {{ background: {t.dark_bg}; color: {t.dark_fg}; }}'
        for t in TOPICS
    )
    media_dark_topic_css = '\n'.join(
        f'  :root:not([data-theme="light"]) .topic-pill[data-topic="{t.slug}"] {{ background: {t.dark_bg}; color: {t.dark_fg}; }}'
        for t in TOPICS
    )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{_html_escape(site_title)}</title>
<script>
  (function() {{
    try {{
      var lang = localStorage.getItem('arxiv-report:lang');
      document.documentElement.dataset.lang = lang === 'en' ? 'en' : 'zh';
    }} catch (_e) {{
      document.documentElement.dataset.lang = 'zh';
    }}
    try {{
      var t = localStorage.getItem('arxiv-report:theme');
      if (t === 'light' || t === 'dark') {{
        document.documentElement.dataset.theme = t;
        return;
      }}
    }} catch (_e) {{}}
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {{
      document.documentElement.dataset.theme = 'dark';
    }} else {{
      document.documentElement.dataset.theme = 'light';
    }}
  }})();
</script>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Newsreader:ital,opsz,wght@0,6..72,400..600;1,6..72,400&family=Outfit:wght@300..700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" />
<style>
:root {{
  color-scheme: light dark;
  --bg: #f8f8f6; --surface: #ffffff; --surface-alt: #f1f2ee;
  --border: #e4e5e0; --border-strong: #d1d3cb;
  --text: #181a1f; --muted: #5a606c;
  --primary: #1858a8; --primary-soft: #e6effa;
  --highlight: #92400e; --highlight-pill: #fde68a; --highlight-border: #f3d27a;
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --bg: #0d1014; --surface: #14181f; --surface-alt: #1b2029;
  --border: #252c37; --border-strong: #323c4a;
  --text: #e2e6ed; --muted: #8c96a5;
  --primary: #68a6f8; --primary-soft: rgba(104, 166, 248, 0.14);
  --highlight: #fbbf24; --highlight-pill: rgba(251, 191, 36, 0.20); --highlight-border: rgba(251, 191, 36, 0.35);
}}
:root[data-theme="dark"] .brand-icon .logo-mark {{ fill: #818cf8; }}
:root[data-theme="dark"] .brand-icon .logo-sparkle {{ fill: #fbbf24; }}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #0d1014; --surface: #14181f; --surface-alt: #1b2029;
    --border: #252c37; --border-strong: #323c4a;
    --text: #e2e6ed; --muted: #8c96a5;
    --primary: #68a6f8; --primary-soft: rgba(104, 166, 248, 0.14);
    --highlight: #fbbf24; --highlight-pill: rgba(251, 191, 36, 0.20); --highlight-border: rgba(251, 191, 36, 0.35);
  }}
  :root:not([data-theme="light"]) .brand-icon .logo-mark {{ fill: #818cf8; }}
  :root:not([data-theme="light"]) .brand-icon .logo-sparkle {{ fill: #fbbf24; }}
}}
* {{ box-sizing: border-box; }}
html, body {{ height: 100%; margin: 0; background: var(--bg); color: var(--text); font-family: "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Noto Sans CJK SC", sans-serif; font-size: 14px; line-height: 1.5; -webkit-font-smoothing: antialiased; }}
body {{ display: flex; flex-direction: column; overflow: hidden; }}
.site-topbar {{ background: var(--surface); border-bottom: 1px solid var(--border); flex: 0 0 auto; z-index: 40; }}
.topbar-inner {{ max-width: 1360px; margin: 0 auto; padding: 8px 22px; display: flex; align-items: center; justify-content: space-between; gap: 14px; flex-wrap: wrap; }}
.topbar-left, .topbar-right {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
.brand-link {{ display: inline-flex; align-items: center; gap: 10px; text-decoration: none; color: inherit; }}
.brand-icon {{ width: 30px; height: 30px; flex-shrink: 0; display: block; }}
.brand-icon .logo-mark {{ fill: #3f4fe0; }}
.brand-icon .logo-sparkle {{ fill: #f59e0b; }}
.brand-text {{ display: inline-flex; flex-direction: column; width: max-content; line-height: 1.05; }}
.brand-kicker {{ display: flex; justify-content: space-between; width: 100%; font-family: "JetBrains Mono", monospace; font-size: 0.58rem; font-weight: 700; text-transform: uppercase; color: var(--muted); margin-bottom: 2px; }}
.brand-title {{ font-size: 1.02rem; font-weight: 800; letter-spacing: -0.02em; color: var(--text); white-space: nowrap; }}
.dim-tabs {{ display: inline-flex; align-items: center; gap: 3px; padding: 3px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface-alt); }}
.dim-tab {{ display: inline-flex; align-items: baseline; gap: 6px; padding: 5px 10px; border: 0; border-radius: 7px; background: transparent; color: var(--muted); font: inherit; font-size: 0.8rem; font-weight: 700; cursor: pointer; }}
.dim-tab:hover {{ color: var(--text); }}
.dim-tab.is-active {{ background: var(--surface); color: var(--primary); box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08); }}
.dim-sub {{ font-family: "JetBrains Mono", monospace; font-size: 0.7rem; font-weight: 500; color: var(--muted); font-variant-numeric: tabular-nums; }}
.topbar-search {{ display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px 4px 6px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-alt); min-width: 210px; }}
.topbar-search.is-active {{ border-color: var(--primary); background: var(--surface); }}
.topbar-search:focus-within {{ border-color: var(--primary); background: var(--surface); box-shadow: 0 0 0 3px var(--primary-soft); }}
.topbar-search .search-ico-btn {{ display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; padding: 0; border: 0; border-radius: 999px; background: transparent; color: var(--muted); cursor: pointer; flex-shrink: 0; }}
.topbar-search .search-ico-btn:hover, .topbar-search.is-active .search-ico-btn {{ color: var(--primary); background: var(--primary-soft); }}
.topbar-search input {{ flex: 1; min-width: 0; border: 0; outline: 0; background: transparent; color: var(--text); font: inherit; font-size: 0.82rem; }}
.search-kbd {{ font-family: "JetBrains Mono", monospace; font-size: 0.68rem; color: var(--muted); border: 1px solid var(--border); border-radius: 4px; padding: 0 5px; background: var(--surface); }}
.topbar-links {{ display: inline-flex; align-items: center; gap: 6px; }}
.topbar-pill {{ display: inline-flex; align-items: center; gap: 5px; padding: 5px 11px; border: 1px solid var(--border); border-radius: 999px; background: transparent; color: var(--text); font: inherit; font-size: 0.78rem; font-weight: 700; cursor: pointer; text-decoration: none; }}
.topbar-pill .pill-ico {{ width: 15px; height: 15px; flex: 0 0 auto; display: block; }}
.topbar-pill:hover, .topbar-pill.is-active {{ border-color: var(--primary); color: var(--primary); background: var(--primary-soft); }}
.theme-toggle-btn {{ display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-alt); color: var(--text); cursor: pointer; padding: 0; }}
.theme-toggle-btn:hover {{ border-color: var(--primary); color: var(--primary); }}
.theme-toggle-btn svg {{ width: 16px; height: 16px; }}
.theme-toggle-btn .ico-sun {{ display: none; }}
.theme-toggle-btn .ico-moon {{ display: block; }}
:root[data-theme="dark"] .theme-toggle-btn .ico-sun {{ display: block; }}
:root[data-theme="dark"] .theme-toggle-btn .ico-moon {{ display: none; }}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) .theme-toggle-btn .ico-sun {{ display: block; }}
  :root:not([data-theme="light"]) .theme-toggle-btn .ico-moon {{ display: none; }}
}}
.lang-toggle-btn {{ display: inline-flex; align-items: center; height: 32px; padding: 2px; gap: 2px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-alt); color: var(--muted); cursor: pointer; font: inherit; font-size: 0.74rem; font-weight: 600; line-height: 1; }}
.lang-toggle-btn:hover {{ border-color: var(--primary); }}
.lang-toggle-btn .lang-opt {{ display: inline-flex; align-items: center; justify-content: center; height: 26px; padding: 0 7px; border-radius: 999px; color: var(--muted); }}
:root:not([data-lang="en"]) .lang-toggle-btn .lang-opt[data-lang-opt="zh"],
:root[data-lang="en"] .lang-toggle-btn .lang-opt[data-lang-opt="en"] {{ background: var(--primary-soft); color: var(--primary); font-weight: 700; }}
.site-subbar {{ background: var(--surface-alt); border-bottom: 1px solid var(--border); flex: 0 0 auto; z-index: 35; }}
.subbar-inner {{ max-width: 1360px; margin: 0 auto; padding: 7px 22px; display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }}
.period-nav {{ display: flex; align-items: center; gap: 6px; }}
.nav-step {{ display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; padding: 0; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--text); font-size: 1rem; font-weight: 700; line-height: 1; cursor: pointer; }}
.nav-step svg, .cal-step svg {{ display: block; flex: 0 0 auto; }}
.nav-step:hover:not(:disabled) {{ border-color: var(--primary); color: var(--primary); }}
.nav-step:disabled {{ opacity: 0.38; cursor: default; }}
.cal-wrap {{ position: relative; }}
.cal-trigger {{ display: inline-flex; align-items: center; gap: 7px; padding: 4px 11px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--text); font-family: "JetBrains Mono", monospace; font-size: 0.78rem; font-weight: 600; cursor: pointer; }}
.cal-trigger:hover {{ border-color: var(--primary); color: var(--primary); }}
.cal-trigger svg {{ width: 15px; height: 15px; color: var(--primary); flex: 0 0 auto; }}
.cal-trigger svg.cal-caret-ico {{ width: 12px; height: 12px; color: var(--muted); }}
#cal-trigger-label {{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.nav-latest-btn {{ padding: 4px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--muted); font: inherit; font-size: 0.76rem; font-weight: 700; cursor: pointer; text-decoration: none; }}
.nav-latest-btn:hover {{ border-color: var(--primary); color: var(--primary); }}
.subbar-meta {{ font-family: "JetBrains Mono", monospace; font-size: 0.74rem; color: var(--muted); font-variant-numeric: tabular-nums; }}
.cal-popover {{ position: absolute; top: calc(100% + 8px); left: 0; width: 320px; padding: 12px; background: var(--surface); border: 1px solid var(--border-strong); border-radius: 14px; box-shadow: 0 18px 40px rgba(0, 0, 0, 0.16); z-index: 60; }}
.cal-tabs {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 3px; padding: 3px; border: 1px solid var(--border); border-radius: 9px; background: var(--surface-alt); margin-bottom: 10px; }}
.cal-tab {{ border: 0; border-radius: 6px; padding: 4px 0; background: transparent; color: var(--muted); font: inherit; font-size: 0.74rem; font-weight: 700; cursor: pointer; }}
.cal-tab.is-active {{ background: var(--surface); color: var(--primary); box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08); }}
.cal-head {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }}
.cal-head-title {{ font-weight: 700; font-size: 0.84rem; }}
.cal-step {{ display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; padding: 0; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-alt); color: var(--text); line-height: 1; cursor: pointer; font-weight: 700; }}
.cal-step:disabled {{ opacity: 0.35; cursor: default; }}
.cal-grid {{ display: grid; grid-template-columns: 32px repeat(7, 1fr); gap: 3px; text-align: center; align-items: center; }}
.cal-wd {{ font-family: "JetBrains Mono", monospace; font-size: 0.68rem; font-weight: 700; color: var(--muted); padding: 2px 0; }}
.cal-wk, .cal-day {{ height: 28px; display: inline-flex; align-items: center; justify-content: center; border-radius: 6px; border: 1px solid transparent; font-family: "JetBrains Mono", monospace; font-size: 0.72rem; font-variant-numeric: tabular-nums; }}
.cal-wk {{ color: var(--muted); background: transparent; font-size: 0.66rem; }}
.cal-wk.has-data {{ cursor: pointer; color: var(--primary); font-weight: 700; }}
.cal-wk.has-data:hover, .cal-wk.is-sel {{ background: var(--primary); color: #ffffff; }}
.cal-day {{ background: transparent; color: var(--muted); }}
.cal-day.is-out {{ opacity: 0.35; }}
.cal-day.is-empty {{ opacity: 0.42; }}
.cal-day.has-report {{ cursor: pointer; background: var(--primary-soft); color: var(--primary); font-weight: 700; }}
.cal-day.has-report:hover {{ border-color: var(--primary); }}
.cal-day.is-week-hi {{ box-shadow: inset 0 -2px 0 var(--primary); }}
.cal-day.is-sel {{ background: var(--primary); color: #ffffff; }}
.cal-months-grid, .cal-years-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }}
.cal-period-cell {{ display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; padding: 8px 6px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-alt); color: var(--text); cursor: pointer; font: inherit; }}
.cal-period-cell:hover:not(.is-disabled) {{ border-color: var(--primary); color: var(--primary); background: var(--primary-soft); }}
.cal-period-cell.is-sel {{ background: var(--primary); color: #ffffff; border-color: var(--primary); }}
.cal-period-cell.is-disabled {{ opacity: 0.38; cursor: default; }}
.cal-p-lbl {{ font-weight: 700; font-size: 0.8rem; }}
.cal-p-cnt {{ font-family: "JetBrains Mono", monospace; font-size: 0.68rem; opacity: 0.78; }}
main {{ position: relative; flex: 1 1 auto; min-height: 0; overflow: hidden; }}
iframe#viewer {{ width: 100%; height: 100%; border: 0; display: block; background: var(--bg); }}
#corpus-view {{ display: none; height: 100%; overflow-y: auto; padding: 26px 28px 64px; }}
#corpus-view > * {{ max-width: 1020px; margin-left: auto; margin-right: auto; }}
#corpus-view.is-visible {{ display: block; }}
.corpus-header {{ position: relative; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 14px; margin-bottom: 20px; padding: 0 0 16px; border-bottom: 1px solid var(--border); }}
.corpus-header::after {{ content: ''; position: absolute; left: 0; bottom: -1px; width: 44px; height: 2px; border-radius: 2px; background: var(--primary); }}
.page-heading {{ display: flex; align-items: center; gap: 12px; min-width: 0; flex-wrap: wrap; }}
.page-head-icon {{ display: inline-flex; align-items: center; justify-content: center; width: 34px; height: 34px; flex-shrink: 0; border-radius: 9px; border: 1px solid var(--border); background: var(--surface); color: var(--primary); box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04); }}
.page-head-icon svg {{ width: 17px; height: 17px; display: block; }}
.page-head-titles {{ display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 12px; min-width: 0; }}
.corpus-header h2 {{ margin: 0; font-family: "Outfit", "Avenir Next", "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, sans-serif; font-size: 1.5rem; font-weight: 580; letter-spacing: -0.022em; line-height: 1.15; color: var(--text); font-variant-numeric: lining-nums proportional-nums; }}
.page-head-sep {{ width: 1.5px; height: 18px; border-radius: 1px; background: var(--border-strong); align-self: center; flex-shrink: 0; }}
.page-head-sub {{ margin: 0; font-family: "Outfit", "Avenir Next", "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, sans-serif; font-size: 1.4rem; font-weight: 400; letter-spacing: -0.016em; line-height: 1.15; color: var(--muted); font-variant-numeric: lining-nums proportional-nums; }}
.corpus-stats-line {{ display: inline-flex; align-items: center; height: 28px; max-width: 420px; overflow: hidden; text-overflow: ellipsis; margin: 0; padding: 0 11px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface); font-family: "JetBrains Mono", monospace; font-size: 0.74rem; font-weight: 500; line-height: 1; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }}
.filter-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0 18px; }}
.filter-row input, .filter-row select {{ padding: 7px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--text); font: inherit; }}
.filter-row input {{ flex: 2 1 240px; }}
.s-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 15px 18px; margin-bottom: 12px; transition: border-color 0.15s; }}
.s-card:hover {{ border-color: var(--border-strong); }}
.s-ledger {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 8px; }}
.s-meta {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }}
.s-actions {{ display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0; }}
.card-action-btn {{ display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; padding: 0; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-alt); color: var(--muted); cursor: pointer; text-decoration: none; }}
.card-action-btn:hover {{ border-color: var(--primary); color: var(--primary); background: var(--primary-soft); }}
.card-action-btn.star-toggle-btn.is-active {{ background: var(--highlight-pill); color: var(--highlight); border-color: var(--highlight-border); }}
.card-action-btn svg {{ width: 15px; height: 15px; stroke: currentColor; fill: none; stroke-width: 1.9; stroke-linecap: round; stroke-linejoin: round; }}
.card-action-btn svg.is-filled {{ fill: currentColor; }}
.pill {{ display: inline-flex; align-items: center; padding: 2px 8px; border: 1px solid var(--border); border-radius: 999px; font-family: "JetBrains Mono", monospace; font-size: 0.72rem; font-weight: 600; color: var(--muted); text-decoration: none; cursor: pointer; background: transparent; }}
.pill:hover {{ border-color: var(--primary); color: var(--primary); }}
.pill.cited {{ background: #dbeafe; color: #1858a8; border-color: #bfdbfe; }}
.s-card h3 {{ margin: 2px 0 4px; font-size: 1.16rem; font-weight: 800; letter-spacing: -0.01em; line-height: 1.4; }}
.s-en {{ margin: 0 0 7px; font-family: "Newsreader", Georgia, serif; font-size: 1.08rem; font-weight: 600; color: color-mix(in srgb, var(--text) 82%, var(--muted)); line-height: 1.4; }}
.s-auth {{ margin: 0 0 6px; font-size: 0.8rem; color: var(--muted); }}
.s-snip {{ margin: 0; font-size: 0.87rem; }}
:root:not([data-lang="en"]) .s-lang-en.has-zh-pair {{ display: none; }}
:root[data-lang="en"] .s-lang-zh.has-en-pair {{ display: none; }}
:root[data-lang="en"] .s-en {{ margin: 2px 0 6px; font-size: 1.14rem; font-weight: 700; color: var(--text); }}
.s-tags {{ display: flex; flex-wrap: wrap; gap: 5px; margin-top: 9px; padding-top: 8px; border-top: 1px dashed var(--border); }}
.topic-pill {{ padding: 2px 8px; border-radius: 999px; font-size: 0.72rem; font-weight: 600; border: 0; cursor: pointer; }}
{topic_map_css}
:root[data-theme="dark"] .pill.cited {{ background: rgba(104, 166, 248, 0.16); color: #93c5fd; border-color: rgba(104, 166, 248, 0.35); }}
{dark_topic_css}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) .pill.cited {{ background: rgba(104, 166, 248, 0.16); color: #93c5fd; border-color: rgba(104, 166, 248, 0.35); }}
{media_dark_topic_css}
}}
@media (max-width: 820px) {{
  .topbar-inner, .subbar-inner {{ padding: 8px 12px; }}
  .period-nav {{ min-width: 0; max-width: 100%; }}
  .cal-wrap {{ min-width: 0; flex: 0 1 auto; }}
  .cal-trigger {{ max-width: calc(100vw - 108px); min-width: 0; }}
  .dim-sub {{ display: none; }}
  .topbar-search {{ min-width: 140px; flex: 1; }}
  #corpus-view {{ padding: 18px 14px 48px; }}
  .corpus-header h2 {{ font-size: 1.34rem; }}
  .page-head-sub {{ font-size: 1.12rem; }}
  .page-head-sep {{ display: none; }}
}}
</style>
</head>
<body>
<header class="site-topbar">
  <div class="topbar-inner">
    <div class="topbar-left">
      <a class="brand-link" href="#latest" id="brand-home">
        <svg class="brand-icon" viewBox="0 0 64 64" fill="none" aria-hidden="true">
          <svg x="1" y="0" width="44" height="59" viewBox="0 0 74.492 100.25">
            <g class="logo-mark">
              <path d="M586.72,255.616a3.377,3.377,0,0,1,.448.031,5.917,5.917,0,0,1,3.581,2.79c.454,1.116.314,2.023-1.315,4.141L563.168,293.6l-8.558-10.047,29.348-26.616a4.406,4.406,0,0,1,2.762-1.321m0-1.5a5.766,5.766,0,0,0-3.69,1.643l-.041.032-.038.035L553.6,282.442l-1.077.977.943,1.107,8.558,10.047,1.145,1.344,1.141-1.348,26.267-31.022.022-.027.022-.028c1.574-2.046,2.327-3.622,1.516-5.619a7.309,7.309,0,0,0-4.779-3.714,5.083,5.083,0,0,0-.64-.043Z" transform="translate(-526.086 -245.559)"/>
              <path d="M553.423,284.593l8.977,10.558L597.911,337.9c.873,1.093,1.419,2.186,1.047,3.418a4.092,4.092,0,0,1-2.721,2.837,3.557,3.557,0,0,1-1.045.159,4,4,0,0,1-2.687-1.124L548.01,300.808c-3.5-3.5-2.971-8.151.436-11.558l4.977-4.657m.124-2.17L552.4,283.5l-4.976,4.656c-4.192,4.191-4.372,9.816-.473,13.714l44.521,42.4a5.485,5.485,0,0,0,3.722,1.538,5.1,5.1,0,0,0,1.483-.224,5.59,5.59,0,0,0,3.719-3.838,5.176,5.176,0,0,0-1.31-4.788l-35.53-42.767-8.988-10.571-1.019-1.2Z" transform="translate(-526.086 -245.559)"/>
              <path d="M562.4,295.151l9.556,11.5,5.761-5.356a7.926,7.926,0,0,0,.041-11.743l-43.7-41.923s-1.671-2.029-3.437-2.071a4.49,4.49,0,0,0-4.23,2.718c-.688,1.651-.194,2.809,1.315,4.97l29.306,35.565Z" transform="translate(-526.086 -245.559)"/>
              <path d="M553.7,306.223l-17.116,21.024c-1.255,1.337-2.032,3.683-1.331,5.367a4.587,4.587,0,0,0,4.287,2.841,4.087,4.087,0,0,0,3.082-1.523l20.328-18.9Z" transform="translate(-526.086 -245.559)"/>
            </g>
          </svg>
          <g class="logo-sparkle">
            <path d="M50,6 C50,14 52,16 60,16 C52,16 50,18 50,26 C50,18 48,16 40,16 C48,16 50,14 50,6 Z" />
            <path d="M39,27 C39,30.5 40,31.5 43.5,31.5 C40,31.5 39,32.5 39,36 C39,32.5 38,31.5 34.5,31.5 C38,31.5 39,30.5 39,27 Z" />
          </g>
        </svg>
        <span class="brand-text">
          <span class="brand-kicker" aria-label="ASTRO-PH.HE"><span>A</span><span>S</span><span>T</span><span>R</span><span>O</span><span>-</span><span>P</span><span>H</span><span>.</span><span>H</span><span>E</span></span>
          <span class="brand-title">{_html_escape(site_title)}</span>
        </span>
      </a>
      <nav class="dim-tabs" aria-label="Time dimensions">
        <button type="button" class="dim-tab" data-dim="day"><span class="dim-name">Day</span><span class="dim-sub" id="dim-day-sub">{_html_escape(dates[0]) if dates else ''}</span></button>
        <button type="button" class="dim-tab" data-dim="week"><span class="dim-name">Week</span><span class="dim-sub" id="dim-week-sub">{_html_escape((periods.get('weeks') or [''])[0])}</span></button>
        <button type="button" class="dim-tab" data-dim="month"><span class="dim-name">Month</span><span class="dim-sub" id="dim-month-sub">{_html_escape((periods.get('months') or [''])[0])}</span></button>
        <button type="button" class="dim-tab" data-dim="year"><span class="dim-name">Year</span><span class="dim-sub" id="dim-year-sub">{_html_escape((periods.get('years') or [''])[0])}</span></button>
      </nav>
    </div>
    <div class="topbar-right">
      <form class="topbar-search" id="sb-search-form" role="search">
        <button type="submit" class="search-ico-btn" title="Open corpus search" aria-label="Open corpus search"><svg width="15" height="15" viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5" stroke="currentColor" stroke-width="1.7" /><path d="M12.8 12.8L17 17" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" /></svg></button>
        <input type="search" id="sb-q" placeholder="Search corpus, EP, GRB..." aria-label="Search corpus" autocomplete="off" />
        <kbd class="search-kbd" title="Press / to search">/</kbd>
      </form>
      <nav class="topbar-links" aria-label="Views">
        <button type="button" class="topbar-pill" data-view="starred"><svg class="pill-ico" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.2l2.6 5.4 5.9.9-4.3 4.2 1 5.9-5.2-2.8-5.2 2.8 1-5.9-4.3-4.2 5.9-.9z"/></svg><span>Starred</span></button>
      </nav>
      <button id="lang-toggle" type="button" class="lang-toggle-btn" title="Switch content language (中文 / EN)" aria-label="Switch content language">
        <span class="lang-opt" data-lang-opt="zh">中</span><span class="lang-opt" data-lang-opt="en">EN</span>
      </button>
      <button id="theme-toggle" type="button" class="theme-toggle-btn" title="Toggle theme" aria-label="Toggle color theme">
        <svg class="ico-sun" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="1.8" />
          <path d="M12 2.5v2.2M12 19.3v2.2M4.7 4.7l1.6 1.6M17.7 17.7l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.7 19.3l1.6-1.6M17.7 6.3l1.6-1.6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
        </svg>
        <svg class="ico-moon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M20.2 14.5A8.5 8.5 0 0 1 9.5 3.8a8.5 8.5 0 1 0 10.7 10.7z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
        </svg>
      </button>
    </div>
  </div>
</header>
<div class="site-subbar">
  <div class="subbar-inner">
    <div class="period-nav">
      <button type="button" class="nav-step" id="cal-prev-btn" title="Older period" aria-label="Older period"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 18 9 12 15 6"/></svg></button>
      <div id="cal-wrap" class="cal-wrap">
        <button id="cal-trigger-btn" type="button" class="cal-trigger" aria-expanded="false" aria-haspopup="dialog">
          <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <rect x="3" y="4.2" width="14" height="12.8" rx="2.2" stroke="currentColor" stroke-width="1.5" />
            <path d="M3 8.2h14M6.8 2.5v2.8M13.2 2.5v2.8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
          </svg>
          <span id="cal-trigger-label">{_html_escape(dates[0]) if dates else 'Calendar'}</span>
          <svg class="cal-caret-ico" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
        <div id="cal-popover" class="cal-popover" hidden role="dialog" aria-label="Calendar navigator"></div>
      </div>
      <button type="button" class="nav-step" id="cal-next-btn" title="Newer period" aria-label="Newer period"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 6 15 12 9 18"/></svg></button>
      <a class="nav-latest-btn" id="nav-latest-link" href="#day/{_html_escape(dates[0]) if dates else ''}">Latest</a>
    </div>
    <span class="subbar-meta">{len(dates)} report days · {paper_count} indexed papers</span>
  </div>
</div>
<main>
  <iframe id="viewer" title="Report Viewer"></iframe>
  <div id="corpus-view">
    <header class="corpus-header">
      <div class="page-heading">
        <span class="page-head-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5" stroke-linecap="round"/></svg>
        </span>
        <div class="page-head-titles">
          <h2 id="cv-title">Search Corpus</h2>
          <span class="page-head-sep" aria-hidden="true"></span>
          <p class="page-head-sub" id="cv-desc">Cross-day bilingual archive</p>
        </div>
      </div>
      <p id="cv-sub" class="corpus-stats-line"></p>
    </header>
    <div class="filter-row" id="cv-filters">
      <input type="search" id="cv-q" placeholder="Search keywords, arXiv IDs, or objects (e.g., EP240414a, magnetar)..." />
      <select id="cv-topic">
        <option value="">All topics</option>
        {topic_options}
      </select>
      <select id="cv-method">
        <option value="">All methods</option>
        <option value="Observation">Observation</option>
        <option value="Modeling">Modeling</option>
        <option value="Simulation">Simulation</option>
        <option value="Theory">Theory</option>
      </select>
      <select id="cv-sort">
        <option value="relevance">Relevance</option>
        <option value="date">Newest</option>
        <option value="citations">Most cited</option>
      </select>
    </div>
    <div id="cv-results"></div>
  </div>
</main>
<script>
window.__SITE_DATA__ = {boot_data};
(function() {{
  var THEME_KEY = 'arxiv-report:theme';
  var LANG_KEY = 'arxiv-report:lang';
  var DATA = window.__SITE_DATA__ || {{}};
  var viewer = document.getElementById('viewer');
  var corpusView = document.getElementById('corpus-view');
  var cvQ = document.getElementById('cv-q');
  var cvTopic = document.getElementById('cv-topic');
  var cvMethod = document.getElementById('cv-method');
  var cvSort = document.getElementById('cv-sort');
  var cvTitle = document.getElementById('cv-title');
  var cvDesc = document.getElementById('cv-desc');
  var cvSub = document.getElementById('cv-sub');
  var sbForm = document.getElementById('sb-search-form');
  var cvResults = document.getElementById('cv-results');
  var sbQ = document.getElementById('sb-q');
  var langBtn = document.getElementById('lang-toggle');
  var themeBtn = document.getElementById('theme-toggle');
  var lastSimSeed = null;
  var calWrap = document.getElementById('cal-wrap');
  var calTrig = document.getElementById('cal-trigger-btn');
  var calLbl = document.getElementById('cal-trigger-label');
  var calPop = document.getElementById('cal-popover');
  var calPrev = document.getElementById('cal-prev-btn');
  var calNext = document.getElementById('cal-next-btn');
  var indexCache = null;

  var curMode = 'day';
  var curKey = (DATA.dates || [])[0] || '';
  var pendingTarget = '';
  var dateSet = {{}};
  (DATA.dates || []).forEach(function(d) {{ dateSet[d] = true; }});
  var weekSet = {{}};
  ((DATA.periods && DATA.periods.weeks) || []).forEach(function(w) {{ weekSet[w] = true; }});
  var monthSet = {{}};
  ((DATA.periods && DATA.periods.months) || []).forEach(function(m) {{ monthSet[m] = true; }});

  function esc(s) {{
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }}

  function sysTheme() {{
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }}

  function curTheme() {{
    try {{
      var s = localStorage.getItem(THEME_KEY);
      if (s === 'light' || s === 'dark') return s;
    }} catch (_e) {{}}
    return (document.documentElement && document.documentElement.dataset && document.documentElement.dataset.theme) || sysTheme();
  }}

  function broadcastTheme(t) {{
    try {{
      if (viewer && viewer.contentWindow) {{
        viewer.contentWindow.postMessage({{type: 'arxiv-theme-change', theme: t}}, '*');
      }}
    }} catch (_e) {{}}
  }}

  function curLang() {{
    try {{
      var l = localStorage.getItem(LANG_KEY);
      if (l === 'en' || l === 'zh') return l;
    }} catch (_e) {{}}
    return (document.documentElement && document.documentElement.dataset && document.documentElement.dataset.lang === 'en') ? 'en' : 'zh';
  }}

  function broadcastLang(l) {{
    try {{
      if (viewer && viewer.contentWindow) {{
        viewer.contentWindow.postMessage({{type: 'arxiv-lang-change', lang: l}}, '*');
      }}
    }} catch (_e) {{}}
  }}

  function applyLang(l, save) {{
    var norm = l === 'en' ? 'en' : 'zh';
    if (document.documentElement && document.documentElement.dataset) document.documentElement.dataset.lang = norm;
    if (save) {{
      try {{ localStorage.setItem(LANG_KEY, norm); }} catch (_e) {{}}
    }}
    if (langBtn) {{
      langBtn.classList.toggle('is-en', norm === 'en');
      langBtn.setAttribute('aria-pressed', norm === 'en' ? 'true' : 'false');
    }}
    if (curMode === 'similar' && lastSimSeed && cvSub) {{
      cvSub.textContent = norm === 'en' ? (lastSimSeed.title || lastSimSeed.title_zh) : (lastSimSeed.title_zh || lastSimSeed.title);
    }}
    broadcastLang(norm);
  }}

  function applyTheme(t, save) {{
    if (document.documentElement) document.documentElement.dataset.theme = t;
    if (save) {{
      try {{ localStorage.setItem(THEME_KEY, t); }} catch (_e) {{}}
    }}
    if (themeBtn) {{
      themeBtn.classList.toggle('is-dark', t === 'dark');
      themeBtn.setAttribute('aria-pressed', t === 'dark' ? 'true' : 'false');
    }}
    broadcastTheme(t);
  }}

  function pad2(n) {{ return n < 10 ? '0' + n : String(n); }}
  function isoDateUTC(d) {{ return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate()); }}
  function isoWeekUTC(d) {{
    var tmp = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    var dayNum = tmp.getUTCDay() || 7;
    tmp.setUTCDate(tmp.getUTCDate() + 4 - dayNum);
    var yearStart = new Date(Date.UTC(tmp.getUTCFullYear(), 0, 1));
    var weekNo = Math.ceil(((tmp - yearStart) / 86400000 + 1) / 7);
    return tmp.getUTCFullYear() + '-W' + pad2(weekNo);
  }}
  function makeEl(tag, cls, txt) {{
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined && txt !== null) n.textContent = txt;
    return n;
  }}

  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var WDAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];
  var calTab = 'day';
  var viewYear = 2026;
  var viewMonth = 0;

  function syncCalendarAnchor() {{
    var anchor = (DATA.dates || [])[0] || new Date().toISOString().slice(0, 10);
    if (curMode === 'day' && /^\\d{{4}}-\\d{{2}}-\\d{{2}}$/.test(curKey)) anchor = curKey;
    else if (curMode === 'month' && /^\\d{{4}}-\\d{{2}}$/.test(curKey)) anchor = curKey + '-01';
    else if (curMode === 'year' && /^\\d{{4}}$/.test(curKey)) anchor = curKey + '-01-01';
    viewYear = parseInt(anchor.slice(0, 4), 10) || 2026;
    viewMonth = (parseInt(anchor.slice(5, 7), 10) || 1) - 1;
    if (curMode === 'week' || curMode === 'month' || curMode === 'year' || curMode === 'day') calTab = curMode;
  }}

  function renderPopover() {{
    if (!calPop) return;
    calPop.innerHTML = '';
    function makeStepBtn(dir, label) {{
      var b = makeEl('button', 'cal-step');
      b.type = 'button';
      b.setAttribute('aria-label', label);
      b.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="' + (dir < 0 ? '15 18 9 12 15 6' : '9 6 15 12 9 18') + '"/></svg>';
      return b;
    }}
    var tabs = makeEl('div', 'cal-tabs');
    [['day', 'Day'], ['week', 'Week'], ['month', 'Month'], ['year', 'Year']].forEach(function(pair) {{
      var b = makeEl('button', 'cal-tab' + (calTab === pair[0] ? ' is-active' : ''), pair[1]);
      b.type = 'button';
      b.addEventListener('click', function(e) {{
        e.stopPropagation();
        calTab = pair[0];
        renderPopover();
      }});
      tabs.appendChild(b);
    }});
    calPop.appendChild(tabs);

    var yrs = (DATA.periods && DATA.periods.years) || [];
    if (calTab === 'year') {{
      var yGrid = makeEl('div', 'cal-years-grid');
      yrs.forEach(function(yk) {{
        var yb = makeEl('button', 'cal-period-cell' + (curMode === 'year' && curKey === yk ? ' is-sel' : ''));
        yb.type = 'button';
        yb.appendChild(makeEl('span', 'cal-p-lbl', yk));
        yb.appendChild(makeEl('span', 'cal-p-cnt', 'Year'));
        yb.addEventListener('click', function() {{
          calPop.hidden = true;
          location.hash = '#rollup/year/' + yk;
        }});
        yGrid.appendChild(yb);
      }});
      calPop.appendChild(yGrid);
      return;
    }}

    var allDates = DATA.dates || [];
    var minDay = allDates.length ? allDates[allDates.length - 1] : '';
    var maxDay = allDates.length ? allDates[0] : '';

    if (calTab === 'month') {{
      var mHead = makeEl('div', 'cal-head');
      var prevY = makeStepBtn(-1, 'Previous year');
      var nextY = makeStepBtn(1, 'Next year');
      var minY = minDay ? parseInt(minDay.slice(0, 4), 10) : viewYear;
      var maxY = maxDay ? parseInt(maxDay.slice(0, 4), 10) : viewYear;
      prevY.disabled = viewYear <= minY;
      nextY.disabled = viewYear >= maxY;
      prevY.addEventListener('click', function(e) {{ e.stopPropagation(); viewYear -= 1; renderPopover(); }});
      nextY.addEventListener('click', function(e) {{ e.stopPropagation(); viewYear += 1; renderPopover(); }});
      mHead.appendChild(prevY);
      mHead.appendChild(makeEl('span', 'cal-head-title', String(viewYear)));
      mHead.appendChild(nextY);
      calPop.appendChild(mHead);

      var mGrid = makeEl('div', 'cal-months-grid');
      for (var mi = 0; mi < 12; mi += 1) {{
        var mk = viewYear + '-' + pad2(mi + 1);
        if (monthSet[mk]) {{
          var mb = makeEl('button', 'cal-period-cell' + (curMode === 'month' && curKey === mk ? ' is-sel' : ''));
          mb.type = 'button';
          mb.appendChild(makeEl('span', 'cal-p-lbl', MONTHS[mi]));
          mb.appendChild(makeEl('span', 'cal-p-cnt', mk));
          (function(targetM) {{
            mb.addEventListener('click', function() {{
              calPop.hidden = true;
              location.hash = '#rollup/month/' + targetM;
            }});
          }})(mk);
          mGrid.appendChild(mb);
        }} else {{
          var ms = makeEl('span', 'cal-period-cell is-disabled');
          ms.appendChild(makeEl('span', 'cal-p-lbl', MONTHS[mi]));
          ms.appendChild(makeEl('span', 'cal-p-cnt', '·'));
          mGrid.appendChild(ms);
        }}
      }}
      calPop.appendChild(mGrid);
      return;
    }}

    var head = makeEl('div', 'cal-head');
    var prevM = makeStepBtn(-1, 'Previous month');
    var nextM = makeStepBtn(1, 'Next month');
    var curYM = viewYear * 12 + viewMonth;
    var minYM = minDay ? parseInt(minDay.slice(0, 4), 10) * 12 + parseInt(minDay.slice(5, 7), 10) - 1 : 0;
    var maxYM = maxDay ? parseInt(maxDay.slice(0, 4), 10) * 12 + parseInt(maxDay.slice(5, 7), 10) - 1 : 999999;
    prevM.disabled = minDay && curYM <= minYM;
    nextM.disabled = maxDay && curYM >= maxYM;
    prevM.addEventListener('click', function(e) {{
      e.stopPropagation();
      if (viewMonth === 0) {{ viewMonth = 11; viewYear -= 1; }} else viewMonth -= 1;
      renderPopover();
    }});
    nextM.addEventListener('click', function(e) {{
      e.stopPropagation();
      if (viewMonth === 11) {{ viewMonth = 0; viewYear += 1; }} else viewMonth += 1;
      renderPopover();
    }});
    head.appendChild(prevM);
    head.appendChild(makeEl('span', 'cal-head-title', MONTHS[viewMonth] + ' ' + viewYear));
    head.appendChild(nextM);
    calPop.appendChild(head);

    var grid = makeEl('div', 'cal-grid');
    grid.appendChild(makeEl('span', 'cal-wd', 'Wk'));
    WDAYS.forEach(function(w) {{ grid.appendChild(makeEl('span', 'cal-wd', w)); }});
    var first = new Date(Date.UTC(viewYear, viewMonth, 1));
    var last = new Date(Date.UTC(viewYear, viewMonth + 1, 0));
    var offset = (first.getUTCDay() + 6) % 7;
    var cursor = new Date(first);
    cursor.setUTCDate(first.getUTCDate() - offset);
    for (var row = 0; row < 6; row += 1) {{
      var monday = new Date(cursor);
      monday.setUTCDate(cursor.getUTCDate() + row * 7);
      if (monday > last) break;
      var wkKey = isoWeekUTC(monday);
      if (weekSet[wkKey]) {{
        var wb = makeEl('button', 'cal-wk has-data' + (curMode === 'week' && curKey === wkKey ? ' is-sel' : ''), wkKey.split('-')[1]);
        wb.type = 'button';
        (function(tw) {{
          wb.addEventListener('click', function() {{ calPop.hidden = true; location.hash = '#rollup/week/' + tw; }});
        }})(wkKey);
        grid.appendChild(wb);
      }} else {{
        grid.appendChild(makeEl('span', 'cal-wk is-empty', wkKey.split('-')[1]));
      }}
      for (var col = 0; col < 7; col += 1) {{
        var cd = new Date(monday);
        cd.setUTCDate(monday.getUTCDate() + col);
        var dStr = isoDateUTC(cd);
        var isOut = cd.getUTCMonth() !== viewMonth;
        var inWk = curMode === 'week' && curKey === wkKey;
        var isSelD = curMode === 'day' && curKey === dStr;
        if (dateSet[dStr]) {{
          var db = makeEl('button', 'cal-day has-report' + (isOut ? ' is-out' : '') + (isSelD ? ' is-sel' : '') + (inWk ? ' is-week-hi' : ''), String(cd.getUTCDate()));
          db.type = 'button';
          (function(td, twk) {{
            db.addEventListener('click', function() {{
              calPop.hidden = true;
              location.hash = (calTab === 'week' && weekSet[twk]) ? '#rollup/week/' + twk : '#day/' + td;
            }});
          }})(dStr, wkKey);
          grid.appendChild(db);
        }} else {{
          grid.appendChild(makeEl('span', 'cal-day is-empty' + (isOut ? ' is-out' : '') + (inWk ? ' is-week-hi' : ''), String(cd.getUTCDate())));
        }}
      }}
    }}
    calPop.appendChild(grid);
  }}

  function stepPeriod(delta) {{
    var seq = [];
    var prefix = '';
    if (curMode === 'day') {{
      seq = DATA.dates || [];
      prefix = '#day/';
    }} else if (curMode === 'week' || curMode === 'month' || curMode === 'year') {{
      seq = (DATA.periods && DATA.periods[curMode + 's']) || [];
      prefix = '#rollup/' + curMode + '/';
    }}
    var idx = seq.indexOf(curKey);
    if (idx === -1) return;
    var nextIdx = idx + delta;
    if (nextIdx >= 0 && nextIdx < seq.length) location.hash = prefix + seq[nextIdx];
  }}

  function updateSubbarUi() {{
    document.querySelectorAll('.dim-tab').forEach(function(b) {{
      b.classList.toggle('is-active', b.dataset.dim === curMode);
    }});
    document.querySelectorAll('.topbar-pill[data-view]').forEach(function(b) {{
      b.classList.toggle('is-active', b.dataset.view === curMode);
    }});
    if (sbForm && sbForm.classList) {{
      sbForm.classList.toggle('is-active', curMode === 'search' || curMode === 'similar');
    }}
    if (calLbl) calLbl.textContent = curKey || 'Calendar';
    var seq = curMode === 'day' ? (DATA.dates || []) : ((DATA.periods && DATA.periods[curMode + 's']) || []);
    var idx = seq.indexOf(curKey);
    if (calPrev) calPrev.disabled = idx === -1 || idx >= seq.length - 1;
    if (calNext) calNext.disabled = idx <= 0;
  }}

  function loadIndex(cb) {{
    if (indexCache) return cb(indexCache);
    fetch('search_index.json').then(function(r) {{ return r.json(); }}).then(function(arr) {{
      indexCache = arr || [];
      cb(indexCache);
    }}).catch(function() {{ cb([]); }});
  }}

  function tokenize(s) {{
    return String(s || '').toLowerCase().trim().split(/\\s+/).filter(Boolean);
  }}

  function scoreSearch(item, tokens) {{
    if (!tokens.length) return 1;
    var pid = (item.pid || '').toLowerCase();
    var aid = (item.arxiv_id || '').toLowerCase();
    var tEn = (item.title || '').toLowerCase();
    var tZh = (item.title_zh || '').toLowerCase();
    var auth = (item.authors || '').toLowerCase();
    var objs = (item.objects || []).join(' ').toLowerCase();
    var snip = ((item.snippet || '') + ' ' + (item.snippet_en || '')).toLowerCase();
    var hits = 0;
    var score = 0;
    tokens.forEach(function(tok) {{
      var s = 0;
      if (tok === pid || tok === aid) s += 100;
      if (objs.indexOf(tok) !== -1) s += 40;
      if (tEn.indexOf(tok) !== -1 || tZh.indexOf(tok) !== -1) s += 20;
      if (auth.indexOf(tok) !== -1) s += 12;
      if (snip.indexOf(tok) !== -1) s += 6;
      if (s > 0) {{ hits += 1; score += s; }}
    }});
    return hits === tokens.length ? score : (hits > 0 ? score * 0.4 : 0);
  }}

  function isLocalStarred(item) {{
    try {{
      var aid = item.arxiv_id || item.pid || '';
      var key = 'arxiv-report:' + (item.date || 'corpus') + ':' + aid + ':starred';
      return localStorage.getItem(key) === '1';
    }} catch (_e) {{ return false; }}
  }}

  function cardHtml(item, badge) {{
    var dayHash = '#day/' + esc(item.date) + (item.number ? '/p' + item.number : '');
    var simHash = '#similar/' + encodeURIComponent(item.pid);
    var cite = item.citation_count > 0
      ? '<a class="pill cited" href="' + esc(item.scix_url) + '" target="_blank" rel="noopener noreferrer">SciX · ' + item.citation_count + '</a>'
      : '<a class="pill" href="' + esc(item.scix_url) + '" target="_blank" rel="noopener noreferrer">SciX</a>';
    var badgeHtml = badge ? '<span class="pill">' + esc(badge) + '</span>' : '';
    var topicMeta = {{}};
    (DATA.topics || []).forEach(function(t) {{ topicMeta[t.slug] = t.short; }});
    var tHtml = (item.topics || []).map(function(s) {{
      return '<button type="button" class="topic-pill" data-topic="' + esc(s) + '">' + esc(topicMeta[s] || s) + '</button>';
    }}).join('');
    var oHtml = (item.objects || []).slice(0, 6).map(function(o) {{
      return '<a class="pill" href="#search/' + encodeURIComponent(o) + '">⌖ ' + esc(o) + '</a>';
    }}).join('');
    var starred = isLocalStarred(item);
    var starSvg = '<svg' + (starred ? ' class="is-filled"' : '') + ' viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8L12 3.5z"/></svg>';
    var simSvg = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6.5" cy="12" r="2.5"/><circle cx="17.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/><path d="M8.8 10.9l6.4-3.3M8.8 13.1l6.4 3.3"/></svg>';
    var payload = esc(JSON.stringify({{
      url: item.url || '', arxivId: item.arxiv_id || item.pid || '',
      titleEn: item.title || '', titleZh: item.title_zh || '',
      authors: item.authors || '', question: item.snippet || '',
      method: item.method || '', result: '', resultEn: item.snippet_en || '', caveat: '',
      status: item.status || 'submitted', statusLabel: item.status_label || 'Submitted',
      reportDate: item.date || '', number: String(item.number || '')
    }}));
    var hasTitlePair = Boolean(item.title_zh && item.title);
    var hasSnipPair = Boolean(item.snippet && item.snippet_en && item.snippet_en !== item.snippet);
    return '<article class="s-card" data-paper="' + payload + '">'
      + '<div class="s-ledger">'
      + '<div class="s-meta">'
      + '<a class="pill" href="' + dayHash + '">' + esc(item.date || 'corpus') + (item.number ? ' · #' + item.number : '') + '</a>'
      + '<a class="pill" href="' + esc(item.url) + '" target="_blank" rel="noopener noreferrer">arXiv:' + esc(item.arxiv_id) + '</a>'
      + cite + badgeHtml
      + '</div>'
      + '<div class="s-actions">'
      + '<button type="button" class="card-action-btn star-toggle-btn' + (starred ? ' is-active' : '') + '" title="Toggle star (local)" aria-label="Toggle star">' + starSvg + '</button>'
      + '<a class="card-action-btn" href="' + simHash + '" title="Find similar papers" aria-label="Find similar papers">' + simSvg + '</a>'
      + '</div>'
      + '</div>'
      + (item.title_zh ? '<h3 class="s-lang-zh' + (hasTitlePair ? ' has-en-pair' : '') + '">' + esc(item.title_zh) + '</h3>' : '')
      + (item.title ? '<p class="s-en s-lang-en' + (hasTitlePair ? ' has-zh-pair' : '') + '">' + esc(item.title) + '</p>' : '')
      + (item.authors ? '<p class="s-auth">' + esc(item.authors) + '</p>' : '')
      + (item.snippet ? '<p class="s-snip' + (hasSnipPair ? ' s-lang-zh has-en-pair' : '') + '">' + esc(item.snippet) + '</p>' : '')
      + (hasSnipPair ? '<p class="s-snip s-lang-en has-zh-pair">' + esc(item.snippet_en) + '</p>' : '')
      + ((tHtml || oHtml) ? '<div class="s-tags">' + tHtml + oHtml + '</div>' : '')
      + '</article>';
  }}

  function runSearch() {{
    document.getElementById('cv-filters').style.display = 'flex';
    loadIndex(function(items) {{
      var q = cvQ.value.trim();
      var wantTopic = cvTopic.value;
      var wantMethod = cvMethod.value;
      var sortBy = cvSort.value;
      if (q.indexOf('topic:') === 0) {{
        wantTopic = q.slice(6).trim();
        q = '';
      }} else if (q.indexOf('method:') === 0) {{
        wantMethod = q.slice(7).trim();
        q = '';
      }}
      var tokens = tokenize(q);
      var out = [];
      items.forEach(function(it) {{
        if (wantTopic && (it.topics || []).indexOf(wantTopic) === -1) return;
        if (wantMethod && (it.method || '').toLowerCase() !== wantMethod.toLowerCase()) return;
        var sc = scoreSearch(it, tokens);
        if (tokens.length && sc <= 0) return;
        out.push({{ item: it, score: sc }});
      }});
      out.sort(function(a, b) {{
        if (sortBy === 'citations') return (b.item.citation_count || 0) - (a.item.citation_count || 0) || b.score - a.score;
        if (sortBy === 'date') return (b.item.date || '').localeCompare(a.item.date || '') || b.score - a.score;
        return b.score - a.score || (b.item.date || '').localeCompare(a.item.date || '');
      }});
      cvTitle.textContent = 'Search Corpus';
      if (cvDesc) cvDesc.textContent = 'Cross-day bilingual archive';
      cvSub.textContent = out.length + ' matches';
      cvResults.innerHTML = out.slice(0, 80).map(function(p) {{ return cardHtml(p.item); }}).join('') || '<p>No matches.</p>';
    }});
  }}

  function runSimilar(pid) {{
    document.getElementById('cv-filters').style.display = 'none';
    loadIndex(function(items) {{
      var seed = items.filter(function(it) {{ return it.pid === pid || it.arxiv_id === pid; }})[0];
      if (!seed) {{
        cvTitle.textContent = 'Paper not found: ' + pid;
        cvSub.textContent = '';
        cvResults.innerHTML = '';
        return;
      }}
      var seedWords = tokenize((seed.title || '') + ' ' + (seed.title_zh || '') + ' ' + (seed.snippet || '') + ' ' + (seed.snippet_en || ''));
      var seedSet = {{}};
      seedWords.forEach(function(w) {{ if (w.length > 2) seedSet[w] = true; }});
      var seedTopics = seed.topics || [];
      var seedObjs = seed.objects || [];
      var scored = [];
      items.forEach(function(it) {{
        if (it.pid === seed.pid) return;
        var sc = 0;
        (it.topics || []).forEach(function(t) {{ if (seedTopics.indexOf(t) !== -1) sc += 3; }});
        (it.objects || []).forEach(function(o) {{ if (seedObjs.indexOf(o) !== -1) sc += 5; }});
        if (seed.method && it.method === seed.method) sc += 1;
        tokenize((it.title || '') + ' ' + (it.title_zh || '') + ' ' + (it.snippet || '') + ' ' + (it.snippet_en || '')).forEach(function(w) {{
          if (seedSet[w]) sc += 1;
        }});
        if (sc > 0) scored.push({{ item: it, score: sc }});
      }});
      scored.sort(function(a, b) {{ return b.score - a.score; }});
      lastSimSeed = seed;
      cvTitle.textContent = 'Similar to ' + seed.arxiv_id;
      if (cvDesc) cvDesc.textContent = 'arXiv:' + seed.arxiv_id;
      cvSub.textContent = curLang() === 'en' ? (seed.title || seed.title_zh) : (seed.title_zh || seed.title);
      cvResults.innerHTML = scored.slice(0, 35).map(function(x) {{ return cardHtml(x.item, 'sim ' + x.score); }}).join('') || '<p>No similar papers found.</p>';
    }});
  }}

  function route() {{
    var h = (location.hash || '').replace(/^#/, '');
    if ((!h || h === 'latest') && (DATA.dates || []).length) h = 'day/' + DATA.dates[0];
    pendingTarget = '';
    corpusView.classList.remove('is-visible');
    viewer.style.display = 'block';

    if (h.indexOf('day/') === 0) {{
      var parts = h.slice(4).split('/');
      curMode = 'day';
      curKey = parts[0];
      pendingTarget = parts[1] || '';
      var frag = pendingTarget ? ('#' + pendingTarget) : '';
      syncCalendarAnchor();
      updateSubbarUi();
      viewer.src = 'arXiv_astro_ph_HE_daily_report_' + curKey + '.html' + frag;
      if (pendingTarget && viewer.contentWindow) {{
        try {{
          viewer.contentWindow.postMessage({{type: 'arxiv-scroll-target', targetId: pendingTarget}}, '*');
        }} catch (_e) {{}}
      }}
      return;
    }}
    if (h.indexOf('rollup/') === 0) {{
      var rParts = h.slice(7).split('/');
      curMode = rParts[0] || 'month';
      curKey = rParts[1] || ((DATA.periods[curMode + 's'] || [])[0] || '');
      syncCalendarAnchor();
      updateSubbarUi();
      if (curKey) viewer.src = 'rollups/' + curMode + '-' + curKey + '.html';
      return;
    }}
    if (h === 'starred') {{
      curMode = 'starred';
      curKey = 'Starred';
      updateSubbarUi();
      viewer.src = DATA.starred || 'starred.html';
      return;
    }}
    if (h.indexOf('similar/') === 0) {{
      curMode = 'similar';
      curKey = 'Similar';
      updateSubbarUi();
      viewer.style.display = 'none';
      corpusView.classList.add('is-visible');
      runSimilar(decodeURIComponent(h.slice(8)));
      return;
    }}
    if (h === 'search' || h.indexOf('search/') === 0) {{
      curMode = 'search';
      curKey = 'Search';
      updateSubbarUi();
      viewer.style.display = 'none';
      corpusView.classList.add('is-visible');
      if (h.indexOf('search/') === 0) cvQ.value = decodeURIComponent(h.slice(7));
      runSearch();
    }}
  }}

  applyLang(curLang(), false);
  if (langBtn) {{
    langBtn.addEventListener('click', function() {{
      applyLang(curLang() === 'en' ? 'zh' : 'en', true);
    }});
  }}
  window.addEventListener('storage', function(e) {{
    if (e && e.key === LANG_KEY && (e.newValue === 'zh' || e.newValue === 'en')) {{
      applyLang(e.newValue, false);
    }}
  }});
  applyTheme(curTheme(), false);
  if (themeBtn) {{
    themeBtn.addEventListener('click', function() {{
      applyTheme(curTheme() === 'dark' ? 'light' : 'dark', true);
    }});
  }}
  viewer.addEventListener('load', function() {{
    broadcastTheme(curTheme());
    broadcastLang(curLang());
    if (pendingTarget && viewer.contentWindow) {{
      try {{
        viewer.contentWindow.postMessage({{type: 'arxiv-scroll-target', targetId: pendingTarget}}, '*');
      }} catch (_e) {{}}
    }}
  }});

  document.getElementById('sb-search-form').addEventListener('submit', function(e) {{
    e.preventDefault();
    var qv = sbQ.value.trim();
    location.hash = qv ? ('#search/' + encodeURIComponent(qv)) : '#search';
  }});
  document.querySelectorAll('.dim-tab').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var dim = btn.dataset.dim;
      if (dim === 'day' && (DATA.dates || []).length) location.hash = '#day/' + DATA.dates[0];
      else if (dim && (DATA.periods[dim + 's'] || []).length) location.hash = '#rollup/' + dim + '/' + DATA.periods[dim + 's'][0];
    }});
  }});
  document.querySelectorAll('[data-view]').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var v = btn.dataset.view;
      if (v === 'search') location.hash = '#search';
      else if (v === 'starred') location.hash = '#starred';
    }});
  }});
  if (calPrev) calPrev.addEventListener('click', function() {{ stepPeriod(1); }});
  if (calNext) calNext.addEventListener('click', function() {{ stepPeriod(-1); }});
  if (calTrig) {{
    calTrig.addEventListener('click', function(e) {{
      e.stopPropagation();
      var open = calPop.hidden;
      if (open) {{ renderPopover(); calPop.hidden = false; calTrig.setAttribute('aria-expanded', 'true'); }}
      else {{ calPop.hidden = true; calTrig.setAttribute('aria-expanded', 'false'); }}
    }});
  }}
  if (calPop) calPop.addEventListener('click', function(e) {{ e.stopPropagation(); }});
  document.addEventListener('click', function(e) {{
    if (calPop && !calPop.hidden && calWrap && !calWrap.contains(e.target)) {{
      calPop.hidden = true;
      if (calTrig) calTrig.setAttribute('aria-expanded', 'false');
    }}
  }});
  document.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape' && calPop && !calPop.hidden) {{
      calPop.hidden = true;
      if (calTrig) calTrig.setAttribute('aria-expanded', 'false');
      return;
    }}
    if (e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey) {{
      var act = document.activeElement;
      var tag = act ? act.tagName : '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (act && act.isContentEditable)) return;
      if (sbQ) {{ e.preventDefault(); sbQ.focus(); sbQ.select(); }}
    }}
  }});
  [cvQ, cvTopic, cvMethod, cvSort].forEach(function(el) {{
    el.addEventListener('input', runSearch);
    el.addEventListener('change', runSearch);
  }});
  cvResults.addEventListener('click', function(e) {{
    var starBtn = e.target.closest ? e.target.closest('.star-toggle-btn') : null;
    if (starBtn) {{
      var card = starBtn.closest('.s-card');
      if (card) {{
        try {{
          var d = JSON.parse(card.dataset.paper || '{{}}');
          var base = 'arxiv-report:' + (d.reportDate || 'corpus') + ':' + (d.arxivId || 'paper');
          var nowStar = localStorage.getItem(base + ':starred') === '1';
          if (nowStar) {{ localStorage.removeItem(base + ':starred'); }}
          else {{
            localStorage.setItem(base + ':starred', '1');
            localStorage.setItem(base + ':paper', JSON.stringify(d));
          }}
          starBtn.classList.toggle('is-active', !nowStar);
          var svgEl = starBtn.querySelector('svg');
          if (svgEl) svgEl.classList.toggle('is-filled', !nowStar);
        }} catch (_e) {{}}
      }}
      return;
    }}
    var pill = e.target.closest ? e.target.closest('.topic-pill') : null;
    if (pill && pill.dataset.topic) {{
      cvTopic.value = pill.dataset.topic;
      runSearch();
    }}
  }});
  window.addEventListener('hashchange', route);
  route();
}})();
</script>
</body>
</html>"""


def build_static_site(
    *,
    out_dir: str | None = None,
    reports_dir: str = './reports',
    db_path: str | None = None,
    site_title: str | None = None,
) -> dict:
    """Export a privacy-safe static HTML + search bundle outside ``reports_dir``.

    Args:
        out_dir: Destination directory for the static site bundle. Defaults
            to ``SITE_OUTPUT_DIR``. Must differ from ``reports_dir``.
        reports_dir: Source directory containing reports and fragments.
        db_path: Optional SQLite corpus path override.
        site_title: Title displayed in exported pages.

    Returns:
        Summary dictionary with keys ``out_dir``, ``days``, ``weeks``,
        ``months``, ``years``, and ``indexed_papers``.

    Raises:
        ValueError: If ``out_dir`` resolves to ``reports_dir``.
    """
    target_out = out_dir or config.SITE_OUTPUT_DIR
    if os.path.abspath(target_out) == os.path.abspath(reports_dir):
        raise ValueError('SITE_OUTPUT_DIR must not equal reports_dir')

    resolved_db = _resolve_db_path(reports_dir, db_path)
    ensure_bootstrapped(db_path=resolved_db, reports_dir=reports_dir)
    resolved_title = site_title or config.SITE_TITLE

    os.makedirs(target_out, exist_ok=True)
    dates = _export_daily_reports(reports_dir, target_out, resolved_db)
    _export_starred_page(target_out)
    periods = _export_rollups(target_out, resolved_db, resolved_title)
    paper_count = _export_search_index(target_out, resolved_db)

    index_html = _render_static_index_html(
        site_title=resolved_title,
        dates=dates,
        periods=periods,
        paper_count=paper_count,
    )
    _write_atomic(os.path.join(target_out, 'index.html'), index_html, encoding='utf-8')

    latest_target = f'arXiv_astro_ph_HE_daily_report_{dates[0]}.html' if dates else 'index.html'
    latest_html = (
        '<!DOCTYPE html><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url={latest_target}">'
        f'<link rel="canonical" href="{latest_target}">'
        f'<a href="{latest_target}">Latest report</a>\n'
    )
    _write_atomic(os.path.join(target_out, 'latest.html'), latest_html, encoding='utf-8')
    _write_atomic(os.path.join(target_out, '.nojekyll'), '', encoding='utf-8')

    return {
        'out_dir': target_out,
        'days': len(dates),
        'weeks': len(periods['weeks']),
        'months': len(periods['months']),
        'years': len(periods['years']),
        'indexed_papers': paper_count,
    }
