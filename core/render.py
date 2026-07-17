"""HTML rendering: wrap the LLM-generated body content in a styled standalone document."""

import datetime
import os

from core.config import CRAFT_ARXIV_FOLDER_ID, CRAFT_SPACE_ID

REPORTS_DIR = './reports'


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
<title>高能天体物理 arXiv 日报 · {date_str}</title>
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
    --border: #e4e7eb;
    --link: #0b65c2;
    --shadow: 0 4px 16px rgba(31, 78, 140, 0.10);
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
        --border: #2a3138;
        --link: #79b8ff;
        --shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
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
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    box-shadow: var(--shadow);
    overflow: hidden;
    text-align: center;
}}
.report-header::before {{
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 4px;
    background: linear-gradient(90deg, #4f46e5 0%, #06b6d4 100%);
}}
.report-header .kicker {{
    margin: 0 0 10px;
    font-size: 0.72em;
    font-weight: 700;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--text-muted);
}}
.report-header h1 {{
    margin: 0;
    font-size: 2em;
    font-weight: 800;
    letter-spacing: -0.022em;
    line-height: 1.15;
    background: linear-gradient(120deg, #4f46e5 0%, #06b6d4 100%);
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
    padding: 3px 12px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface-alt);
    color: var(--text-muted);
}}
.report-meta .pill strong {{
    color: var(--text);
    margin-right: 4px;
    font-weight: 600;
}}
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
.craft-save-btn {{
    float: right;
    font-size: 0.72em;
    font-weight: 600;
    padding: 3px 10px;
    margin-left: 8px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface-alt);
    color: var(--text-muted);
    cursor: pointer;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
}}
.craft-save-btn:hover {{
    background: var(--primary-soft);
    color: var(--primary);
    border-color: var(--primary);
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
    .index-box, .paper-item {{ padding: 14px 16px; }}
}}
</style>
</head>
<body>
<div class="container">
    <header class="report-header">
        <p class="kicker">arXiv · astro-ph.HE Daily Report</p>
        <h1>高能天体物理 arXiv 日报</h1>
        <div class="report-meta">
            <span class="pill"><strong>{date_str}</strong></span>
            <span class="pill"><strong>{len(papers)}</strong>篇论文</span>
            <span class="pill">Generated by {provider}</span>
        </div>
    </header>
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

function saveToCraft(item) {{
    const link = item.querySelector('h3 a');
    const url = link ? link.href : '';
    const arxivId = link ? link.textContent.trim() : '';
    const titleEn = craftField(item, '英文标题：');
    const titleZh = craftField(item, '中文标题：');
    const authors = craftField(item, '作者：');
    const question = craftField(item, '研究问题：');
    const method = craftField(item, '研究方法：');
    const result = craftField(item, '研究结果：');
    const caveat = craftField(item, '潜在不足与延伸方向：');

    const note = prompt('个人备注（可选）：');
    if (note === null) return;

    let content = '# ' + titleZh + '\\n' + titleEn + '\\n\\n';
    content += '[arXiv:' + arxivId + '](' + url + ')\\n\\n';
    content += '**作者：** ' + authors + '\\n';
    if (question) content += '\\n**研究问题：** ' + question;
    content += '\\n**研究方法：** ' + method;
    content += '\\n**研究结果：** ' + result;
    if (caveat) content += '\\n**潜在不足与延伸方向：** ' + caveat;
    if (note) content += '\\n\\n---\\n**个人备注：** ' + note;

    const title = '[' + arxivId + '] ' + titleZh;
    const params = new URLSearchParams({{
        spaceId: CRAFT_SPACE_ID,
        folderId: CRAFT_FOLDER_ID,
        title: title,
        content: content
    }});
    window.location.href = 'craftdocs://x-callback-url/createdocument?' + params.toString();
}}

document.querySelectorAll('.paper-item').forEach(function(item) {{
    const h3 = item.querySelector('h3');
    if (!h3) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'craft-save-btn';
    btn.textContent = '📥 收藏到 Craft';
    btn.addEventListener('click', function() {{
        saveToCraft(item);
        btn.textContent = '已收藏 ✓';
        setTimeout(function() {{ btn.textContent = '📥 收藏到 Craft'; }}, 3000);
    }});
    h3.appendChild(btn);
}});
</script>
</body>
</html>"""

    os.makedirs(REPORTS_DIR, exist_ok=True)
    filename = f'{REPORTS_DIR}/arXiv_astro_ph_HE_daily_report_{date_str}.html'
    with open(filename, 'w', encoding='utf-8-sig') as f:
        f.write(html_layout)
    print(f'✨ Sync version of daily report (Index + Details) generated: {filename}')
    return filename
