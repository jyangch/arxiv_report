"""HTML rendering: wrap the LLM-generated body content in a styled standalone document."""

import datetime
import os

from core.config import CRAFT_ARXIV_FOLDER_ID, CRAFT_SPACE_ID

REPORTS_DIR = './reports'
STARRED_REPORT = 'starred.html'


def _write_starred_html(provider: str, as_of: datetime.datetime | None = None) -> str:
    """Write the standalone localStorage-backed starred paper list."""
    html = f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Starred paper list</title>
<style>
:root {{
    color-scheme: light dark;
    --bg: #fbfbfc;
    --surface: #ffffff;
    --surface-alt: #f4f6f9;
    --text: #1f2933;
    --text-muted: #6b7280;
    --primary: #1f4e8c;
    --primary-soft: #e4eef9;
    --highlight: #92400e;
    --highlight-bg: #fef7e3;
    --highlight-pill: #fde68a;
    --highlight-border: #f3d27a;
    --craft-bg: #ecfeff;
    --craft-fg: #0e7490;
    --craft-border: #67e8f9;
    --accent: #2f855a;
    --accent-soft: #e3f2eb;
    --status-published-bg: #d1fae5;
    --status-published-fg: #065f46;
    --status-accepted-bg: #dbeafe;
    --status-accepted-fg: #1e40af;
    --status-submitted-bg: #f3f4f6;
    --status-submitted-fg: #4b5563;
    --border: #e4e7eb;
    --link: #0b65c2;
}}
@media (prefers-color-scheme: dark) {{
    :root {{
        --bg: #0e1117;
        --surface: #161b22;
        --surface-alt: #1c2128;
        --text: #d9dde2;
        --text-muted: #8b95a1;
        --primary: #5a9ee6;
        --primary-soft: rgba(90, 158, 230, 0.16);
        --highlight: #fbbf24;
        --highlight-bg: rgba(251, 191, 36, 0.08);
        --highlight-pill: rgba(251, 191, 36, 0.22);
        --highlight-border: rgba(251, 191, 36, 0.35);
        --craft-bg: rgba(34, 211, 238, 0.14);
        --craft-fg: #67e8f9;
        --craft-border: rgba(103, 232, 249, 0.35);
        --accent: #6ee7a3;
        --accent-soft: rgba(110, 231, 163, 0.14);
        --status-published-bg: rgba(110, 231, 163, 0.18);
        --status-published-fg: #6ee7a3;
        --status-accepted-bg: rgba(90, 158, 230, 0.18);
        --status-accepted-fg: #5a9ee6;
        --status-submitted-bg: rgba(139, 149, 161, 0.18);
        --status-submitted-fg: #a1a8b0;
        --border: #2a3138;
        --link: #79b8ff;
    }}
}}
* {{ box-sizing: border-box; }}
body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue",
                 Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
                 "Noto Sans CJK SC", "Source Han Sans SC", sans-serif;
    line-height: 1.65;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    font-feature-settings: "kern", "liga", "palt";
}}
.container {{ max-width: 980px; margin: 0 auto; padding: 32px 24px 64px; }}
.header {{
    position: relative;
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 14px;
    align-items: center;
    margin-bottom: 24px;
    padding: 24px 26px 22px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--surface);
    box-shadow: 0 10px 26px rgba(31, 78, 140, 0.08);
    overflow: hidden;
}}
.header::before {{
    content: "";
    position: absolute;
    inset: 0 0 auto;
    height: 4px;
    background: linear-gradient(90deg, #4f46e5 0%, #0ea5e9 58%, #06b6d4 100%);
}}
h1 {{
    margin: 0;
    font-size: 1.9rem;
    line-height: 1.15;
    font-weight: 750;
    background: linear-gradient(120deg, #3730a3 0%, #0f6fc5 58%, #0891b2 100%);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
}}
.meta {{
    margin: 0;
    padding: 6px 12px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface-alt);
    color: var(--text-muted);
    font-size: 0.92rem;
    line-height: 1.35;
    font-variant-numeric: tabular-nums;
}}
@media (prefers-color-scheme: dark) {{
    .header {{ box-shadow: 0 10px 26px rgba(0, 0, 0, 0.22); }}
    h1 {{
        background: linear-gradient(120deg, #a5b4fc 0%, #38bdf8 58%, #67e8f9 100%);
        -webkit-background-clip: text;
        background-clip: text;
    }}
}}
.report-tools {{
    position: sticky;
    top: 0;
    z-index: 20;
    margin: 0 0 22px;
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: color-mix(in srgb, var(--surface) 92%, transparent);
    box-shadow: 0 8px 24px rgba(31, 78, 140, 0.08);
    backdrop-filter: blur(12px);
}}
.tool-row {{
    display: grid;
    grid-template-columns: minmax(220px, 1fr) minmax(120px, auto) minmax(150px, auto) auto auto;
    gap: 8px;
    align-items: center;
}}
.report-tools input,
.report-tools select {{
    width: 100%;
    min-height: 34px;
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--surface);
    color: var(--text);
    padding: 6px 9px;
    font: inherit;
    font-size: 0.84em;
}}
.report-tools input:focus,
.report-tools select:focus {{
    outline: 0;
    border-color: var(--primary);
    box-shadow: 0 0 0 3px var(--primary-soft);
}}
.tool-btn {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 34px;
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--surface-alt);
    color: var(--text-muted);
    cursor: pointer;
    font: inherit;
    font-size: 0.78em;
    font-weight: 600;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
}}
.tool-btn:hover {{
    background: var(--primary-soft);
    color: var(--primary);
    border-color: var(--primary);
}}
.visible-count {{
    color: var(--text-muted);
    font-size: 0.82em;
    white-space: nowrap;
    text-align: right;
}}
.mini-index {{
    display: flex;
    gap: 5px;
    overflow-x: auto;
    padding-top: 10px;
}}
.mini-index a {{
    flex: 0 0 auto;
    min-width: 30px;
    padding: 2px 7px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface);
    color: var(--text-muted);
    text-align: center;
    font-size: 0.74em;
    font-weight: 700;
    text-decoration: none;
}}
.mini-index a:hover,
.mini-index a.is-active {{
    background: var(--primary-soft);
    color: var(--primary);
    border-color: var(--primary);
}}
.empty-filter {{
    display: none;
    margin: 16px 0;
    padding: 12px 14px;
    border: 1px dashed var(--border);
    border-radius: 8px;
    color: var(--text-muted);
    background: var(--surface-alt);
    font-size: 0.9em;
}}
.empty-filter.is-visible {{ display: block; }}
.paper-list {{ display: flex; flex-direction: column; gap: 18px; }}
.paper-item {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 18px 24px;
    margin: 0;
    transition: border-color 0.2s, box-shadow 0.2s;
}}
.paper-item.is-starred {{
    border-color: var(--highlight-border);
    box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.12);
}}
.paper-item:target,
.paper-item.is-target {{
    border-color: var(--primary);
    box-shadow: 0 0 0 3px var(--primary-soft);
}}
.paper-item.is-hidden {{ display: none; }}
.paper-item h3 {{ margin: 0 0 12px; padding: 0; color: var(--primary); font-size: 1.12em; font-weight: 600; line-height: 1.35; }}
.paper-item a {{ color: var(--link); text-decoration: none; }}
.paper-item h3 a {{ color: inherit; }}
.paper-item a:hover {{ text-decoration: underline; }}
.paper-item p {{ margin: 6px 0; }}
strong {{ color: var(--text); font-weight: 600; }}
.paper-actions {{
    float: right;
    display: inline-flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 6px;
    max-width: min(100%, 176px);
    margin-left: 10px;
}}
.paper-action-btn {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface-alt);
    color: var(--text-muted);
    cursor: pointer;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
}}
.paper-action-btn:hover {{
    background: var(--primary-soft);
    color: var(--primary);
    border-color: var(--primary);
}}
.paper-action-btn.is-active {{
    background: var(--primary-soft);
    color: var(--primary);
    border-color: var(--primary);
}}
.star-toggle-btn.is-active {{
    background: var(--highlight-pill);
    color: var(--highlight);
    border-color: var(--highlight-border);
}}
.craft-save-btn.is-active {{
    background: var(--craft-bg);
    color: var(--craft-fg);
    border-color: var(--craft-border);
}}
.paper-action-btn svg {{
    width: 17px;
    height: 17px;
    display: block;
    stroke: currentColor;
    fill: none;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
}}
.paper-action-btn svg.is-filled {{
    fill: currentColor;
}}
.status-tag {{
    display: inline-block;
    padding: 1px 9px;
    border-radius: 999px;
    font-size: 0.72em;
    font-weight: 600;
    margin-left: 8px;
    vertical-align: 2px;
    letter-spacing: 0.2px;
}}
.status-published {{ background: var(--status-published-bg); color: var(--status-published-fg); }}
.status-accepted {{ background: var(--status-accepted-bg); color: var(--status-accepted-fg); }}
.status-submitted {{ background: var(--status-submitted-bg); color: var(--status-submitted-fg); }}
.method-tag {{
    display: inline-block;
    background: var(--accent-soft);
    color: var(--accent);
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.85em;
    font-weight: 600;
    margin-right: 6px;
}}
@media (max-width: 600px) {{
    .container {{ padding: 16px 12px 40px; }}
    .header {{ align-items: flex-start; }}
    h1 {{ font-size: 1.5em; }}
    .tool-row {{ grid-template-columns: 1fr; }}
    .visible-count {{ text-align: left; }}
    .paper-actions {{
        float: none;
        display: flex;
        justify-content: flex-start;
        margin: 8px 0 4px;
    }}
    .paper-item {{ padding: 14px 16px; }}
}}
</style>
</head>
<body>
<!-- source: {provider} -->
<div class="container">
    <header class="header">
        <h1>Starred paper list</h1>
        <p id="meta" class="meta"></p>
    </header>
    <section class="report-tools" aria-label="Starred paper tools">
        <div class="tool-row">
            <input id="report-search" type="search" placeholder="Search title, author, method, result..." autocomplete="off" />
            <select id="status-filter" aria-label="Filter by status">
                <option value="all">All status</option>
                <option value="accepted">Accepted</option>
                <option value="published">Published</option>
                <option value="submitted">Submitted</option>
            </select>
            <select id="method-filter" aria-label="Filter by method">
                <option value="all">All methods</option>
            </select>
            <button id="clear-filters" class="tool-btn" type="button">Clear</button>
            <span id="visible-count" class="visible-count">0 / 0 shown</span>
        </div>
        <nav id="paper-mini-index" class="mini-index" aria-label="Starred paper index"></nav>
    </section>
    <div id="empty-filter" class="empty-filter">No papers match the current filters.</div>
    <main id="list" class="paper-list"></main>
</div>
<script>
const CRAFT_SPACE_ID = '{CRAFT_SPACE_ID}';
const CRAFT_FOLDER_ID = '{CRAFT_ARXIV_FOLDER_ID}';

function stateEntries(kind) {{
    const rows = [];
    for (let i = 0; i < localStorage.length; i += 1) {{
        const key = localStorage.key(i);
        if (!key || !key.startsWith('arxiv-report:') || !key.endsWith(':' + kind)) continue;
        if (localStorage.getItem(key) !== '1') continue;
        const base = key.slice(0, -1 * (kind.length + 1));
        const raw = localStorage.getItem(base + ':paper');
        if (!raw) continue;
        try {{
            const data = JSON.parse(raw);
            data.stateBase = base;
            data.starred = localStorage.getItem(base + ':starred') === '1';
            data.crafted = localStorage.getItem(base + ':crafted') === '1';
            rows.push(data);
        }} catch (_err) {{}}
    }}
    rows.sort(function(a, b) {{
        return String(b.reportDate || '').localeCompare(String(a.reportDate || ''))
            || String(a.number || '').localeCompare(String(b.number || ''));
    }});
    return rows;
}}

function text(value) {{
    return value || '未记录';
}}

function normalizeText(value) {{
    return (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
}}

function rowStatus(data) {{
    return data.status || 'submitted';
}}

function rowMethod(data) {{
    return data.methodTag || '';
}}

function rowSearchText(data) {{
    return normalizeText([
        data.arxivId,
        data.titleEn,
        data.titleZh,
        data.authors,
        data.question,
        data.method,
        data.result,
        data.caveat,
        data.statusLabel,
        data.reportDate
    ].join(' '));
}}

function iconSvg(name) {{
    const icons = {{
        star: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8L12 3.5z"/></svg>',
        'star-filled': '<svg class="is-filled" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8L12 3.5z"/></svg>',
        bookmark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5h10a1 1 0 0 1 1 1v15l-6-3.3-6 3.3v-15a1 1 0 0 1 1-1z"/></svg>',
        copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1"/></svg>',
        external: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17L17 7"/><path d="M9 7h8v8"/><path d="M19 17v2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2"/></svg>'
    }};
    return icons[name] || '';
}}

function setButtonIcon(btn, name) {{
    btn.innerHTML = iconSvg(name);
}}

function field(label, value) {{
    const p = document.createElement('p');
    p.className = 'field';
    const strong = document.createElement('strong');
    strong.textContent = label;
    p.appendChild(strong);
    p.appendChild(document.createTextNode(text(value)));
    return p;
}}

function methodField(data) {{
    const p = document.createElement('p');
    const strong = document.createElement('strong');
    strong.textContent = '研究方法：';
    p.appendChild(strong);
    if (data.methodTag) {{
        const methodTag = document.createElement('span');
        methodTag.className = 'method-tag';
        methodTag.textContent = '[' + data.methodTag + ']';
        p.appendChild(methodTag);
    }}
    let methodText = text(data.method);
    if (data.methodTag) {{
        methodText = methodText.replace(new RegExp('^\\\\s*\\\\[?' + data.methodTag + '\\\\]?\\\\s*'), '');
    }}
    p.appendChild(document.createTextNode(methodText));
    return p;
}}

function stateKey(data, kind) {{
    return data.stateBase + ':' + kind;
}}

function writeState(data, kind, value) {{
    if (value) {{
        localStorage.setItem(stateKey(data, kind), value);
    }} else {{
        localStorage.removeItem(stateKey(data, kind));
    }}
}}

function craftTitle(data) {{
    return '[' + text(data.arxivId) + '] ' + text(data.titleZh);
}}

function craftContent(data, note, includeDocumentTitle) {{
    let content = includeDocumentTitle ? '# ' + craftTitle(data) + '\\n\\n' : '';
    content += '# ' + text(data.titleZh) + '\\n' + text(data.titleEn) + '\\n\\n';
    content += '[arXiv:' + text(data.arxivId) + '](' + text(data.url) + ')\\n\\n';
    content += '**作者：** ' + text(data.authors) + '\\n';
    if (data.question) content += '\\n**研究问题：** ' + data.question;
    if (data.method) content += '\\n**研究方法：** ' + data.method;
    if (data.result) content += '\\n**研究结果：** ' + data.result;
    if (data.caveat) content += '\\n**潜在不足与延伸方向：** ' + data.caveat;
    if (note) content += '\\n\\n---\\n**个人备注：** ' + note;
    return content;
}}

function craftUrl(title, content) {{
    const query = [
        'spaceId=' + encodeURIComponent(CRAFT_SPACE_ID),
        'folderId=' + encodeURIComponent(CRAFT_FOLDER_ID),
        'title=' + encodeURIComponent(title),
        'content=' + encodeURIComponent(content)
    ].join('&');
    return 'craftdocs://createdocument?' + query;
}}

function buildCraftUrl(data, note) {{
    return craftUrl(craftTitle(data), craftContent(data, note, false));
}}

function buildCraftMarkdown(data, note) {{
    return craftContent(data, note, true);
}}

function fallbackCopyText(value) {{
    const area = document.createElement('textarea');
    area.value = value;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.left = '-9999px';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    if (!ok) throw new Error('copy command failed');
}}

function copyText(value, btn, doneLabel) {{
    const done = function() {{
        const old = btn.innerHTML;
        btn.textContent = doneLabel;
        setTimeout(function() {{ btn.innerHTML = old; }}, 1600);
    }};
    if (navigator.clipboard && window.isSecureContext) {{
        navigator.clipboard.writeText(value).then(done).catch(function() {{
            fallbackCopyText(value);
            done();
        }});
    }} else {{
        fallbackCopyText(value);
        done();
    }}
}}

function copyTextSilently(value) {{
    if (navigator.clipboard && window.isSecureContext) {{
        return navigator.clipboard.writeText(value).catch(function() {{
            fallbackCopyText(value);
        }});
    }}
    fallbackCopyText(value);
    return Promise.resolve();
}}

function toggleStarred(data) {{
    writeState(data, 'starred', '');
    render();
}}

function toggleCraft(data, btn) {{
    if (data.crafted) {{
        writeState(data, 'crafted', '');
        render();
        return;
    }}
    const note = prompt('个人备注（可选）：');
    if (note === null) return;
    writeState(data, 'crafted', '1');
    copyTextSilently(buildCraftMarkdown(data, note)).finally(function() {{
        window.top.location.href = buildCraftUrl(data, note);
    }});
    btn.classList.add('is-active');
}}

function initStarredTools(rows) {{
    const search = document.getElementById('report-search');
    const status = document.getElementById('status-filter');
    const method = document.getElementById('method-filter');
    const clear = document.getElementById('clear-filters');
    const count = document.getElementById('visible-count');
    const index = document.getElementById('paper-mini-index');
    const empty = document.getElementById('empty-filter');
    const papers = Array.from(document.querySelectorAll('.paper-item'));
    if (!search || !status || !method || !clear || !count || !index) return;

    const previousMethod = method.value || 'all';
    method.innerHTML = '<option value="all">All methods</option>';
    const methods = Array.from(new Set(rows.map(rowMethod).filter(Boolean))).sort();
    methods.forEach(function(name) {{
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name;
        method.appendChild(option);
    }});
    method.value = methods.includes(previousMethod) ? previousMethod : 'all';

    index.innerHTML = '';
    const links = new Map();
    papers.forEach(function(item, i) {{
        const data = rows[i];
        item.id = 'p' + (i + 1);
        item.dataset.searchText = rowSearchText(data);
        item.dataset.status = rowStatus(data);
        item.dataset.method = rowMethod(data);
        const a = document.createElement('a');
        a.href = '#' + item.id;
        a.dataset.targetId = item.id;
        a.textContent = String(i + 1);
        a.title = data.arxivId ? '[' + String(i + 1) + '] ' + data.arxivId : a.textContent;
        index.appendChild(a);
        links.set(item.id, a);
    }});

    function applyFilters() {{
        const q = normalizeText(search.value);
        const wantedStatus = status.value;
        const wantedMethod = method.value;
        let visible = 0;
        papers.forEach(function(item) {{
            const okSearch = !q || item.dataset.searchText.includes(q);
            const okStatus = wantedStatus === 'all' || item.dataset.status === wantedStatus;
            const okMethod = wantedMethod === 'all' || item.dataset.method === wantedMethod;
            const show = okSearch && okStatus && okMethod;
            item.classList.toggle('is-hidden', !show);
            const link = links.get(item.id);
            if (link) link.hidden = !show;
            if (show) visible += 1;
        }});
        count.textContent = visible + ' / ' + papers.length + ' shown';
        if (empty) empty.classList.toggle('is-visible', visible === 0 && papers.length > 0);
    }}

    search.oninput = applyFilters;
    status.onchange = applyFilters;
    method.onchange = applyFilters;
    clear.onclick = function() {{
        search.value = '';
        status.value = 'all';
        method.value = 'all';
        applyFilters();
    }};
    applyFilters();
}}

function render(kind) {{
    const list = document.getElementById('list');
    const meta = document.getElementById('meta');
    const rows = stateEntries('starred');
    list.innerHTML = '';
    meta.textContent = rows.length + ' starred papers';
    if (!rows.length) {{
        initStarredTools(rows);
        return;
    }}
    rows.forEach(function(data, index) {{
        const item = document.createElement('article');
        item.className = 'paper-item'
            + (data.starred ? ' is-starred' : '')
            + (data.crafted ? ' is-crafted' : '');

        const h3 = document.createElement('h3');
        const actions = document.createElement('span');
        actions.className = 'paper-actions';
        const starBtn = document.createElement('button');
        starBtn.type = 'button';
        starBtn.className = 'paper-action-btn star-toggle-btn is-active';
        starBtn.title = 'Remove star';
        starBtn.setAttribute('aria-label', 'Remove star');
        setButtonIcon(starBtn, 'star-filled');
        starBtn.addEventListener('click', function() {{ toggleStarred(data); }});

        const craftBtn = document.createElement('button');
        craftBtn.type = 'button';
        craftBtn.className = 'paper-action-btn craft-save-btn' + (data.crafted ? ' is-active' : '');
        craftBtn.title = data.crafted ? 'Remove Craft mark' : 'Save to Craft';
        craftBtn.setAttribute('aria-label', craftBtn.title);
        setButtonIcon(craftBtn, 'bookmark');
        craftBtn.addEventListener('click', function() {{ toggleCraft(data, craftBtn); }});

        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'paper-action-btn';
        copyBtn.title = 'Copy Markdown';
        copyBtn.setAttribute('aria-label', 'Copy Markdown');
        setButtonIcon(copyBtn, 'copy');
        copyBtn.addEventListener('click', function() {{
            const note = prompt('个人备注（可选）：');
            if (note === null) return;
            copyText(buildCraftMarkdown(data, note), copyBtn, '✓');
        }});

        const jumpBtn = document.createElement('button');
        jumpBtn.type = 'button';
        jumpBtn.className = 'paper-action-btn';
        jumpBtn.title = 'Open in daily report';
        jumpBtn.setAttribute('aria-label', 'Open in daily report');
        setButtonIcon(jumpBtn, 'external');
        jumpBtn.addEventListener('click', function() {{
            window.top.location.href = data.reportHref || data.url || '#';
        }});

        actions.appendChild(starBtn);
        actions.appendChild(craftBtn);
        actions.appendChild(copyBtn);
        actions.appendChild(jumpBtn);
        h3.appendChild(actions);
        h3.appendChild(document.createTextNode('[' + String(index + 1) + '] '));
        const link = document.createElement('a');
        link.href = data.reportHref || data.url || '#';
        link.target = '_top';
        link.textContent = text(data.arxivId);
        h3.appendChild(link);
        const showStatus = data.statusVisible === true
            || (data.statusVisible === undefined && data.statusLabel && data.statusLabel !== '已投稿');
        if (showStatus) {{
            const status = document.createElement('span');
            status.className = 'status-tag status-' + (data.status || 'submitted');
            status.textContent = data.statusLabel;
            h3.appendChild(status);
        }}

        item.appendChild(h3);
        item.appendChild(field('英文标题：', data.titleEn));
        item.appendChild(field('中文标题：', data.titleZh));
        if (data.authors) item.appendChild(field('作者：', data.authors));
        item.appendChild(field('研究问题：', data.question));
        if (data.method) item.appendChild(methodField(data));
        item.appendChild(field('研究结果：', data.result));
        if (data.caveat) item.appendChild(field('潜在不足与延伸方向：', data.caveat));
        list.appendChild(item);
    }});
    initStarredTools(rows);
}}
render();
</script>
</body>
</html>"""

    os.makedirs(REPORTS_DIR, exist_ok=True)
    filename = os.path.join(REPORTS_DIR, STARRED_REPORT)
    with open(filename, 'w', encoding='utf-8-sig') as f:
        f.write(html)
    return filename


def save_html(
    papers: list[dict],
    report: str,
    provider: str,
    as_of: datetime.datetime | None = None,
) -> str:
    """Wrap the LLM-generated body in a styled HTML shell and write it to disk.

    The filename uses ``YYYY-MM-DD`` derived from ``as_of`` (or today). The
    output file is overwritten if it already exists.

    Args:
        papers: Original paper list; only its length is used in the header line.
        report: HTML body fragment produced by ``generate_report``.
        provider: Provider slug shown in the report header.
        as_of: Reference timestamp for the filename and header date.

    Returns:
        The path the file was written to (relative to the working directory).
    """
    date_str = (as_of or datetime.datetime.now()).strftime('%Y-%m-%d')
    body_content = report.replace('```html', '').replace('```', '')
    html_layout = f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>高能天体物理论文日报 · {date_str}</title>
<style>
:root {{
    color-scheme: light dark;
    --bg: #fbfbfc;
    --surface: #ffffff;
    --surface-alt: #f4f6f9;
    --text: #1f2933;
    --text-muted: #6b7280;
    --primary: #1f4e8c;
    --primary-soft: #e4eef9;
    --accent: #2f855a;
    --accent-soft: #e3f2eb;
    --highlight: #92400e;
    --highlight-bg: #fef7e3;
    --highlight-pill: #fde68a;
    --highlight-border: #f3d27a;
    --status-published-bg: #d1fae5;
    --status-published-fg: #065f46;
    --status-accepted-bg: #dbeafe;
    --status-accepted-fg: #1e40af;
    --status-submitted-bg: #f3f4f6;
    --status-submitted-fg: #4b5563;
    --craft-bg: #ecfeff;
    --craft-fg: #0e7490;
    --craft-border: #67e8f9;
    --border: #e4e7eb;
    --link: #0b65c2;
    --shadow: 0 4px 16px rgba(31, 78, 140, 0.10);
    --report-header-bg: linear-gradient(180deg, #ffffff 0%, #fbfcff 100%);
    --report-header-border: color-mix(in srgb, var(--border) 86%, #4f46e5);
    --report-header-line: linear-gradient(90deg, #4f46e5 0%, #06b6d4 100%);
    --report-header-title: linear-gradient(120deg, #4f46e5 0%, #0ea5e9 100%);
    --report-header-muted: #64748b;
    --report-header-pill-bg: #f6fafc;
    --report-header-pill-text: #64748b;
    --report-header-pill-strong: #1e293b;
}}
@media (prefers-color-scheme: dark) {{
    :root {{
        --bg: #0e1117;
        --surface: #161b22;
        --surface-alt: #1c2128;
        --text: #d9dde2;
        --text-muted: #8b95a1;
        --primary: #5a9ee6;
        --primary-soft: rgba(90, 158, 230, 0.16);
        --accent: #6ee7a3;
        --accent-soft: rgba(110, 231, 163, 0.14);
        --highlight: #fbbf24;
        --highlight-bg: rgba(251, 191, 36, 0.08);
        --highlight-pill: rgba(251, 191, 36, 0.22);
        --highlight-border: rgba(251, 191, 36, 0.35);
        --status-published-bg: rgba(110, 231, 163, 0.18);
        --status-published-fg: #6ee7a3;
        --status-accepted-bg: rgba(90, 158, 230, 0.18);
        --status-accepted-fg: #5a9ee6;
        --status-submitted-bg: rgba(139, 149, 161, 0.18);
        --status-submitted-fg: #a1a8b0;
        --craft-bg: rgba(34, 211, 238, 0.14);
        --craft-fg: #67e8f9;
        --craft-border: rgba(103, 232, 249, 0.35);
        --border: #2a3138;
        --link: #79b8ff;
        --shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
        --report-header-bg: linear-gradient(180deg, #161b22 0%, #131824 100%);
        --report-header-border: rgba(129, 140, 248, 0.24);
        --report-header-line: linear-gradient(90deg, #818cf8 0%, #22d3ee 100%);
        --report-header-title: linear-gradient(120deg, #a5b4fc 0%, #22d3ee 100%);
        --report-header-muted: #93a4b8;
        --report-header-pill-bg: rgba(255, 255, 255, 0.04);
        --report-header-pill-text: #a7b4c5;
        --report-header-pill-strong: #eef6ff;
    }}
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue",
                 Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
                 "Noto Sans CJK SC", "Source Han Sans SC", sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.65;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    font-feature-settings: "kern", "liga", "palt";
}}
.container {{
    max-width: 960px;
    margin: 0 auto;
    padding: 32px 24px 64px;
}}
a {{ color: var(--link); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
code, .entry-id {{
    font-family: "SF Mono", "JetBrains Mono", Menlo, Consolas, "Courier New", monospace;
    font-size: 0.92em;
}}
.report-header {{
    position: relative;
    margin: 8px 0 32px;
    padding: 30px 32px 24px;
    background: var(--report-header-bg);
    border: 1px solid var(--report-header-border);
    border-radius: 16px;
    box-shadow: 0 10px 24px rgba(79, 70, 229, 0.075);
    overflow: hidden;
    text-align: center;
}}
.report-header::before {{
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 4px;
    background: var(--report-header-line);
}}
.report-header .kicker {{
    margin: 0 0 10px;
    font-size: 0.72em;
    font-weight: 700;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--report-header-muted);
}}
.report-header h1 {{
    margin: 0;
    font-size: 2em;
    font-weight: 800;
    letter-spacing: -0.018em;
    line-height: 1.15;
    background: var(--report-header-title);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    display: inline-block;
}}
.report-meta {{
    margin: 16px 0 0;
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 8px;
    font-size: 0.85em;
}}
.report-meta .pill {{
    display: inline-flex;
    align-items: center;
    padding: 3px 13px;
    border: 1px solid var(--report-header-border);
    border-radius: 999px;
    background: var(--report-header-pill-bg);
    color: var(--report-header-pill-text);
}}
.report-meta .pill strong {{
    color: var(--report-header-pill-strong);
    margin-right: 4px;
    font-weight: 600;
}}
.report-tools {{
    position: sticky;
    top: 0;
    z-index: 20;
    margin: -14px 0 22px;
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: color-mix(in srgb, var(--surface) 92%, transparent);
    box-shadow: 0 8px 24px rgba(31, 78, 140, 0.08);
    backdrop-filter: blur(12px);
}}
.tool-row {{
    display: grid;
    grid-template-columns: minmax(220px, 1fr) minmax(120px, auto) minmax(150px, auto) auto auto;
    gap: 8px;
    align-items: center;
}}
.report-tools input,
.report-tools select {{
    width: 100%;
    min-height: 34px;
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--surface);
    color: var(--text);
    padding: 6px 9px;
    font: inherit;
    font-size: 0.84em;
}}
.report-tools input:focus,
.report-tools select:focus {{
    outline: 0;
    border-color: var(--primary);
    box-shadow: 0 0 0 3px var(--primary-soft);
}}
.tool-btn,
.paper-action-btn {{
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--surface-alt);
    color: var(--text-muted);
    cursor: pointer;
    font: inherit;
    font-size: 0.78em;
    font-weight: 600;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
}}
.tool-btn {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 34px;
    padding: 6px 10px;
}}
.tool-link {{ text-decoration: none; }}
.tool-link:hover {{ text-decoration: none; }}
.tool-btn:hover,
.paper-action-btn:hover {{
    background: var(--primary-soft);
    color: var(--primary);
    border-color: var(--primary);
}}
.visible-count {{
    color: var(--text-muted);
    font-size: 0.82em;
    white-space: nowrap;
    text-align: right;
}}
.mini-index {{
    display: flex;
    gap: 5px;
    overflow-x: auto;
    padding-top: 10px;
}}
.mini-index a {{
    flex: 0 0 auto;
    min-width: 30px;
    padding: 2px 7px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface);
    color: var(--text-muted);
    text-align: center;
    font-size: 0.74em;
    font-weight: 700;
    text-decoration: none;
}}
.mini-index a:hover,
.mini-index a.is-active {{
    background: var(--primary-soft);
    color: var(--primary);
    border-color: var(--primary);
}}
.mini-index a.is-starred {{
    background: var(--highlight-pill);
    color: var(--highlight);
    border-color: var(--highlight-border);
    opacity: 1;
}}
.mini-index a.is-crafted {{
    background: var(--craft-bg);
    color: var(--craft-fg);
    border-color: var(--craft-border);
}}
.empty-filter {{
    display: none;
    margin: 16px 0;
    padding: 12px 14px;
    border: 1px dashed var(--border);
    border-radius: 8px;
    color: var(--text-muted);
    background: var(--surface-alt);
    font-size: 0.9em;
}}
.empty-filter.is-visible {{ display: block; }}
.highlight-box {{
    background: var(--highlight-bg);
    border: 1px solid var(--highlight-border);
    padding: 20px 24px;
    border-radius: 12px;
    margin: 28px 0;
}}
.highlight-box h3 {{
    margin: 0 0 12px;
    color: var(--highlight);
    font-size: 1.1em;
}}
.highlight-box p {{ margin: 8px 0; }}
.highlight-box a {{
    display: inline-block;
    background: var(--highlight-pill);
    color: var(--highlight);
    padding: 1px 9px;
    margin-right: 6px;
    border-radius: 5px;
    font-size: 0.9em;
    font-weight: 600;
    transition: background 0.15s, color 0.15s;
}}
.highlight-box a:hover {{
    background: var(--highlight);
    color: #ffffff;
    text-decoration: none;
}}
.index-box {{
    background: var(--surface-alt);
    border: 1px solid var(--border);
    padding: 20px 24px;
    border-radius: 12px;
    margin: 28px 0;
}}
.index-box p {{ margin: 8px 0; }}
.index-box a {{
    display: inline-block;
    background: var(--primary-soft);
    color: var(--primary);
    padding: 1px 9px;
    margin: 2px 1px;
    border-radius: 5px;
    font-size: 0.9em;
    transition: background 0.15s, color 0.15s;
}}
.index-box a:hover {{
    background: var(--primary);
    color: #ffffff;
    text-decoration: none;
}}
.paper-item {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 18px 24px;
    margin: 18px 0;
    transition: border-color 0.2s, box-shadow 0.2s;
}}
.paper-item:target,
.paper-item.is-target {{
    border-color: var(--primary);
    box-shadow: 0 0 0 3px var(--primary-soft);
}}
.paper-item p {{ margin: 6px 0; }}
.paper-actions {{
    float: right;
    display: inline-flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 6px;
    max-width: min(100%, 132px);
    margin-left: 10px;
}}
.paper-action-btn {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    padding: 0;
    font-size: 1.02em;
    border-radius: 999px;
    white-space: nowrap;
    line-height: 1;
}}
.paper-action-btn svg {{
    width: 17px;
    height: 17px;
    display: block;
    stroke: currentColor;
    fill: none;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
}}
.paper-action-btn svg.is-filled {{
    fill: currentColor;
}}
.paper-action-btn.is-active {{
    background: var(--primary-soft);
    color: var(--primary);
    border-color: var(--primary);
}}
.star-toggle-btn.is-active {{
    background: var(--highlight-pill);
    color: var(--highlight);
    border-color: var(--highlight-border);
}}
.craft-save-btn.is-active {{
    background: var(--craft-bg);
    color: var(--craft-fg);
    border-color: var(--craft-border);
}}
.paper-item.is-hidden {{ display: none; }}
.paper-item.is-starred {{
    border-color: var(--highlight-border);
    box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.12);
}}
.paper-item.is-crafted {{
    border-color: color-mix(in srgb, var(--craft-border) 70%, var(--border));
}}
h3 {{
    color: var(--primary);
    margin: 0 0 12px;
    padding: 0;
    font-size: 1.12em;
    font-weight: 600;
}}
h3 a {{ color: inherit; }}
h3 a:hover {{ text-decoration: underline; }}
.status-tag {{
    display: inline-block;
    padding: 1px 9px;
    border-radius: 999px;
    font-size: 0.72em;
    font-weight: 600;
    margin-left: 8px;
    vertical-align: 2px;
    letter-spacing: 0.2px;
}}
.status-published {{ background: var(--status-published-bg); color: var(--status-published-fg); }}
.status-accepted {{ background: var(--status-accepted-bg); color: var(--status-accepted-fg); }}
.status-submitted {{ background: var(--status-submitted-bg); color: var(--status-submitted-fg); }}
.method-tag {{
    display: inline-block;
    background: var(--accent-soft);
    color: var(--accent);
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.85em;
    font-weight: 600;
    margin-right: 6px;
}}
.errbar {{
    display: inline-block;
    vertical-align: -0.45em;
    font-size: 0.72em;
    line-height: 1;
    margin: 0 2px;
    text-align: left;
}}
.errbar sup,
.errbar sub {{
    display: block;
    font-size: 1em;
    line-height: 1.05;
    vertical-align: baseline;
    position: static;
    top: auto;
    bottom: auto;
    margin: 0;
    padding: 0;
}}
strong {{ color: var(--text); font-weight: 600; }}
@media (max-width: 600px) {{
    .container {{ padding: 16px 12px 40px; }}
    .report-header {{ padding: 22px 18px 18px; }}
    .report-header h1 {{ font-size: 1.5em; }}
    .report-tools {{ margin-top: 0; }}
    .tool-row {{ grid-template-columns: 1fr; }}
    .visible-count {{ text-align: left; }}
    .paper-actions {{
        float: none;
        display: flex;
        justify-content: flex-start;
        margin: 8px 0 4px;
    }}
    .index-box, .paper-item {{ padding: 14px 16px; }}
}}
</style>
</head>
<body>
<div class="container">
    <header class="report-header">
        <p class="kicker">arXiv · astro-ph.HE Daily Report</p>
        <h1>高能天体物理论文日报</h1>
        <div class="report-meta">
            <span class="pill"><strong>{date_str}</strong></span>
            <span class="pill"><strong>{len(papers)}</strong>篇论文</span>
            <span class="pill">Generated by {provider}</span>
        </div>
    </header>
    <section class="report-tools" aria-label="Report tools">
        <div class="tool-row">
            <input id="report-search" type="search" placeholder="Search title, author, method, result..." autocomplete="off" />
            <select id="status-filter" aria-label="Filter by status">
                <option value="all">All status</option>
                <option value="accepted">Accepted</option>
                <option value="published">Published</option>
                <option value="submitted">Submitted</option>
            </select>
            <select id="method-filter" aria-label="Filter by method">
                <option value="all">All methods</option>
            </select>
            <button id="clear-filters" class="tool-btn" type="button">Clear</button>
            <span id="visible-count" class="visible-count">{len(papers)} / {len(papers)} shown</span>
        </div>
        <nav id="paper-mini-index" class="mini-index" aria-label="Paper index"></nav>
    </section>
    <div id="empty-filter" class="empty-filter">No papers match the current filters.</div>
    {body_content}
</div>
<script>
document.addEventListener('click', function(e) {{
    const link = e.target.closest('a[href^="#"]');
    if (!link) return;
    const id = link.getAttribute('href').slice(1);
    if (!id) return;
    const target = document.getElementById(id);
    if (!target) return;
    e.preventDefault();
    document.querySelectorAll('.is-target').forEach(el => el.classList.remove('is-target'));
    target.classList.add('is-target');
    target.scrollIntoView({{behavior: 'auto', block: 'center'}});
}});
</script>
<script>
const CRAFT_SPACE_ID = '{CRAFT_SPACE_ID}';
const CRAFT_FOLDER_ID = '{CRAFT_ARXIV_FOLDER_ID}';
const REPORT_DATE = '{date_str}';

function normalizeText(text) {{
    return (text || '').toLowerCase().replace(/\\s+/g, ' ').trim();
}}

function craftField(item, label) {{
    const paras = item.querySelectorAll('p');
    for (const p of paras) {{
        const strong = p.querySelector('strong');
        if (strong && strong.textContent.trim() === label) {{
            return p.textContent.slice(strong.textContent.length).trim();
        }}
    }}
    return '';
}}

function paperNumber(item) {{
    const id = item.id || '';
    const match = id.match(/p(\\d+)/);
    if (match) return match[1];
    const link = item.querySelector('h3 a');
    return link ? link.textContent.trim() : '';
}}

function paperStatus(item) {{
    const tag = item.querySelector('.status-tag');
    if (!tag) return 'submitted';
    if (tag.classList.contains('status-accepted')) return 'accepted';
    if (tag.classList.contains('status-published')) return 'published';
    return 'submitted';
}}

function paperStatusLabel(item) {{
    const tag = item.querySelector('.status-tag');
    return tag ? tag.textContent.trim() : '';
}}

function paperHasStatusTag(item) {{
    return Boolean(item.querySelector('.status-tag'));
}}

function paperMethod(item) {{
    const tag = item.querySelector('.method-tag');
    return tag ? tag.textContent.trim().replace(/^\\[(.*)\\]$/, '$1') : '';
}}

function buildCraftData(item) {{
    const link = item.querySelector('h3 a');
    return {{
        url: link ? link.href : '',
        arxivId: link ? link.textContent.trim() : '',
        titleEn: craftField(item, '英文标题：'),
        titleZh: craftField(item, '中文标题：'),
        authors: craftField(item, '作者：'),
        question: craftField(item, '研究问题：'),
        method: craftField(item, '研究方法：'),
        result: craftField(item, '研究结果：'),
        caveat: craftField(item, '潜在不足与延伸方向：')
    }};
}}

function paperIdentity(item) {{
    const data = buildCraftData(item);
    return data.arxivId || item.id || 'paper';
}}

function paperStateBase(item) {{
    return 'arxiv-report:' + REPORT_DATE + ':' + paperIdentity(item);
}}

function paperStateKey(item, kind) {{
    return paperStateBase(item) + ':' + kind;
}}

function paperSnapshot(item) {{
    const data = buildCraftData(item);
    return Object.assign(data, {{
        reportDate: REPORT_DATE,
        reportHref: '/r/' + REPORT_DATE + '#' + item.id,
        number: paperNumber(item),
        status: paperStatus(item),
        statusLabel: paperStatusLabel(item),
        statusVisible: paperHasStatusTag(item),
        methodTag: paperMethod(item)
    }});
}}

function writePaperSnapshot(item) {{
    try {{
        localStorage.setItem(paperStateBase(item) + ':paper', JSON.stringify(paperSnapshot(item)));
    }} catch (_err) {{
        // Browsers may block localStorage in some embedded contexts.
    }}
}}

function readPaperState(item, kind) {{
    try {{
        return window.localStorage.getItem(paperStateKey(item, kind)) || '';
    }} catch (_err) {{
        return '';
    }}
}}

function writePaperState(item, kind, value) {{
    try {{
        if (value) {{
            localStorage.setItem(paperStateKey(item, kind), value);
        }} else {{
            localStorage.removeItem(paperStateKey(item, kind));
        }}
    }} catch (_err) {{
        // Browsers may block localStorage in some embedded contexts.
    }}
}}

function iconSvg(name) {{
    const icons = {{
        star: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8L12 3.5z"/></svg>',
        'star-filled': '<svg class="is-filled" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8L12 3.5z"/></svg>',
        bookmark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5h10a1 1 0 0 1 1 1v15l-6-3.3-6 3.3v-15a1 1 0 0 1 1-1z"/></svg>',
        copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1"/></svg>'
    }};
    return icons[name] || '';
}}

function setButtonIcon(btn, name) {{
    btn.innerHTML = iconSvg(name);
}}

function syncPaperState(item) {{
    const isStarred = readPaperState(item, 'starred') === '1';
    const isCrafted = readPaperState(item, 'crafted') === '1';
    item.dataset.starred = isStarred ? 'starred' : '';
    item.dataset.crafted = isCrafted ? 'crafted' : '';
    item.classList.toggle('is-starred', isStarred);
    item.classList.toggle('is-crafted', isCrafted);

    const starBtn = item.querySelector('.star-toggle-btn');
    if (starBtn) {{
        setButtonIcon(starBtn, isStarred ? 'star-filled' : 'star');
        starBtn.classList.toggle('is-active', isStarred);
        starBtn.title = isStarred ? 'Remove star' : 'Mark as starred';
        starBtn.setAttribute('aria-label', starBtn.title);
    }}
    const craftBtn = item.querySelector('.craft-save-btn');
    if (craftBtn) {{
        craftBtn.classList.toggle('is-active', isCrafted);
        craftBtn.title = isCrafted ? 'Remove Craft mark' : 'Save to Craft';
        craftBtn.setAttribute('aria-label', craftBtn.title);
    }}
    const indexLink = document.querySelector('#paper-mini-index a[data-target-id="' + item.id + '"]');
    if (indexLink) {{
        indexLink.classList.toggle('is-starred', isStarred);
        indexLink.classList.toggle('is-crafted', isCrafted);
    }}
}}

function notifyPaperStateChanged() {{
    document.dispatchEvent(new CustomEvent('paper-state-change'));
}}

function craftUrl(title, content) {{
    const query = [
        'spaceId=' + encodeURIComponent(CRAFT_SPACE_ID),
        'folderId=' + encodeURIComponent(CRAFT_FOLDER_ID),
        'title=' + encodeURIComponent(title),
        'content=' + encodeURIComponent(content)
    ].join('&');
    return 'craftdocs://createdocument?' + query;
}}

function craftTitle(data) {{
    return '[' + data.arxivId + '] ' + data.titleZh;
}}

function craftContent(data, note, includeDocumentTitle) {{
    let content = includeDocumentTitle ? '# ' + craftTitle(data) + '\\n\\n' : '';
    content += '# ' + data.titleZh + '\\n' + data.titleEn + '\\n\\n';
    content += '[arXiv:' + data.arxivId + '](' + data.url + ')\\n\\n';
    content += '**作者：** ' + data.authors + '\\n';
    if (data.question) content += '\\n**研究问题：** ' + data.question;
    content += '\\n**研究方法：** ' + data.method;
    content += '\\n**研究结果：** ' + data.result;
    if (data.caveat) content += '\\n**潜在不足与延伸方向：** ' + data.caveat;
    if (note) content += '\\n\\n---\\n**个人备注：** ' + note;
    return content;
}}

function buildCraftUrl(item, note) {{
    const data = buildCraftData(item);
    return craftUrl(craftTitle(data), craftContent(data, note, false));
}}

function buildCraftMarkdown(item, note) {{
    return craftContent(buildCraftData(item), note, true);
}}

function prepareCraftLink(item, e) {{
    const note = prompt('个人备注（可选）：');
    if (note === null) {{
        e.preventDefault();
        return '';
    }}
    try {{
        return buildCraftUrl(item, note);
    }} catch (err) {{
        console.error('Failed to build Craft URL', err);
        alert('Craft URL 生成失败：' + err.message);
        return '';
    }}
}}

function fallbackCopyText(text) {{
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.left = '-9999px';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    if (!ok) throw new Error('copy command failed');
}}

function copyText(text, btn, doneLabel) {{
    const done = function() {{
        const old = btn.innerHTML;
        btn.textContent = doneLabel;
        setTimeout(function() {{ btn.innerHTML = old; }}, 1600);
    }};
    if (navigator.clipboard && window.isSecureContext) {{
        navigator.clipboard.writeText(text).then(done).catch(function() {{
            fallbackCopyText(text);
            done();
        }});
    }} else {{
        fallbackCopyText(text);
        done();
    }}
}}

function copyTextSilently(text) {{
    if (navigator.clipboard && window.isSecureContext) {{
        return navigator.clipboard.writeText(text).catch(function() {{
            fallbackCopyText(text);
        }});
    }}
    fallbackCopyText(text);
    return Promise.resolve();
}}

function askNote() {{
    return prompt('个人备注（可选）：');
}}

function openCraftWithClipboardFallback(item, btn, e) {{
    if (readPaperState(item, 'crafted') === '1') {{
        e.preventDefault();
        writePaperState(item, 'crafted', '');
        writePaperSnapshot(item);
        syncPaperState(item);
        notifyPaperStateChanged();
        return;
    }}
    const note = askNote();
    if (note === null) {{
        e.preventDefault();
        return;
    }}
    const url = buildCraftUrl(item, note);
    const markdown = buildCraftMarkdown(item, note);
    writePaperState(item, 'crafted', '1');
    writePaperSnapshot(item);
    syncPaperState(item);
    notifyPaperStateChanged();
    copyTextSilently(markdown).then(function() {{
        btn.textContent = '✓';
        btn.title = 'Requested Craft save; Markdown copied';
    }}).catch(function() {{
        btn.textContent = '!';
        btn.title = 'Requested Craft save; Markdown copy failed';
    }});
    window.top.location.href = url;
    setTimeout(function() {{ setButtonIcon(btn, 'bookmark'); syncPaperState(item); }}, 3200);
}}

function initPaperStateActions() {{
document.querySelectorAll('.paper-item').forEach(function(item) {{
    const h3 = item.querySelector('h3');
    if (!h3) return;
    if (h3.querySelector('.paper-actions')) return;
    const actions = document.createElement('span');
    actions.className = 'paper-actions';

    const starBtn = document.createElement('button');
    starBtn.type = 'button';
    starBtn.className = 'paper-action-btn star-toggle-btn';
    setButtonIcon(starBtn, 'star');
    starBtn.addEventListener('click', function() {{
        const next = readPaperState(item, 'starred') === '1' ? '' : '1';
        writePaperState(item, 'starred', next);
        writePaperSnapshot(item);
        syncPaperState(item);
        notifyPaperStateChanged();
    }});

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'paper-action-btn craft-save-btn';
    setButtonIcon(btn, 'bookmark');
    btn.title = 'Save to Craft';
    btn.setAttribute('aria-label', 'Save to Craft');
    btn.addEventListener('click', function(e) {{
        try {{
            openCraftWithClipboardFallback(item, btn, e);
        }} catch (err) {{
            e.preventDefault();
            console.error('Failed to prepare Craft save', err);
            alert('Craft 收藏准备失败：' + err.message);
        }}
    }});

    const copyMd = document.createElement('button');
    copyMd.type = 'button';
    copyMd.className = 'paper-action-btn';
    setButtonIcon(copyMd, 'copy');
    copyMd.title = 'Copy Markdown';
    copyMd.setAttribute('aria-label', 'Copy Markdown');
    copyMd.addEventListener('click', function() {{
        const note = askNote();
        if (note === null) return;
        copyText(buildCraftMarkdown(item, note), copyMd, '✓');
    }});

    actions.appendChild(starBtn);
    actions.appendChild(btn);
    actions.appendChild(copyMd);
    h3.appendChild(actions);
    writePaperSnapshot(item);
    syncPaperState(item);
}});
}}

function initReportTools() {{
    if (!document.getElementById) return;
    const papers = Array.from(document.querySelectorAll('.paper-item'));
    const search = document.getElementById('report-search');
    const status = document.getElementById('status-filter');
    const method = document.getElementById('method-filter');
    const clear = document.getElementById('clear-filters');
    const count = document.getElementById('visible-count');
    const index = document.getElementById('paper-mini-index');
    const empty = document.getElementById('empty-filter');
    if (!papers.length || !search || !status || !method || !clear || !count || !index) return;

    const methods = Array.from(new Set(papers.map(paperMethod).filter(Boolean))).sort();
    methods.forEach(function(name) {{
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name;
        method.appendChild(option);
    }});

    const links = new Map();
    papers.forEach(function(item, i) {{
        if (!item.id) item.id = 'p' + (i + 1);
        item.dataset.searchText = normalizeText(item.textContent);
        item.dataset.status = paperStatus(item);
        item.dataset.method = paperMethod(item);
        writePaperSnapshot(item);
        const a = document.createElement('a');
        a.href = '#' + item.id;
        a.dataset.targetId = item.id;
        a.textContent = paperNumber(item) || String(i + 1);
        a.title = item.querySelector('h3') ? item.querySelector('h3').textContent.trim() : a.textContent;
        index.appendChild(a);
        links.set(item.id, a);
        syncPaperState(item);
    }});

    function applyFilters() {{
        const q = normalizeText(search.value);
        const wantedStatus = status.value;
        const wantedMethod = method.value;
        let visible = 0;
        papers.forEach(function(item) {{
            const okSearch = !q || item.dataset.searchText.includes(q);
            const okStatus = wantedStatus === 'all' || item.dataset.status === wantedStatus;
            const okMethod = wantedMethod === 'all' || item.dataset.method === wantedMethod;
            const show = okSearch && okStatus && okMethod;
            item.classList.toggle('is-hidden', !show);
            const link = links.get(item.id);
            if (link) link.hidden = !show;
            if (show) visible += 1;
        }});
        count.textContent = visible + ' / ' + papers.length + ' shown';
        if (empty) empty.classList.toggle('is-visible', visible === 0);
    }}

    search.addEventListener('input', applyFilters);
    status.addEventListener('change', applyFilters);
    method.addEventListener('change', applyFilters);
    document.addEventListener('paper-state-change', applyFilters);
    clear.addEventListener('click', function() {{
        search.value = '';
        status.value = 'all';
        method.value = 'all';
        applyFilters();
    }});
    applyFilters();
}}

initPaperStateActions();
initReportTools();
</script>
</body>
</html>"""

    os.makedirs(REPORTS_DIR, exist_ok=True)
    filename = f'{REPORTS_DIR}/arXiv_astro_ph_HE_daily_report_{date_str}.html'
    with open(filename, 'w', encoding='utf-8-sig') as f:
        f.write(html_layout)
    _write_starred_html(provider, as_of=as_of)
    print(f'✨ Sync version of daily report (Index + Details) generated: {filename}')
    return filename
