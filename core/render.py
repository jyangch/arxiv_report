"""HTML rendering: wrap the LLM-generated body content in a styled standalone document."""

import contextlib
import datetime
import glob
from html import escape as _html_escape
import json
import os
import re
from urllib.parse import quote_plus

from core.citations import get_citation_map, scix_abs_url
from core.corpus import default_db_path
from core.fragments import (
    bare_arxiv_id,
    extract_fragment_from_report_html,
    finalize_fragment,
    iter_paper_item_spans,
    load_fragment,
    parse_fragment,
    save_fragment,
    strip_paper_tags,
)
from core.objects import extract_objects
from core.topics import TOPIC_BY_SLUG, TOPICS, topic_chip_css

REPORTS_DIR = './reports'
STARRED_REPORT = 'starred.html'
_REPORT_FILE_RE = re.compile(r'arXiv_astro_ph_HE_daily_report_(\d{4}-\d{2}-\d{2})\.html$')

# LLM sometimes splits the tag, e.g. `[Theory</span><span class="method-tag">]</span>`
_METHOD_TAG_RE = re.compile(
    r'<span class="method-tag">\s*\[?\s*([A-Za-z]+)\s*\]?\s*</span>'
    r'(?:\s*<span class="method-tag">\s*\]\s*</span>|\])?'
)


def normalize_method_tags(html: str) -> str:
    """Rewrite every method tag into the canonical `<span class="method-tag">[Tag]</span>` form."""
    return _METHOD_TAG_RE.sub(r'<span class="method-tag">[\1]</span>', html)


_GOOGLE_FONTS_HREF = (
    'https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700'
    '&family=Newsreader:ital,opsz,wght@0,6..72,400..600;1,6..72,400..600'
    '&family=Outfit:wght@300..700'
    '&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap'
)

_KICKER_SPARKLE_SVG = (
    '<svg class="kicker-sparkle" viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M12 2C12 9.5 14.5 12 22 12C14.5 12 12 14.5 12 22'
    'C12 14.5 9.5 12 2 12C9.5 12 12 9.5 12 2Z"/></svg>'
)


def _masthead_title_html(title: str) -> str:
    """Return the single-line masthead H1 markup with badge and sculpted wordmark."""
    lead, sep, accent = title.partition(' ')
    if sep and accent:
        wordmark = (
            f'<span class="masthead-wordmark">'
            f'<span class="masthead-lead">{lead}</span> '
            f'<span class="masthead-accent">{accent}</span>'
            f'</span>'
        )
    else:
        wordmark = f'<span class="masthead-wordmark">{title}</span>'
    return (
        f'<h1><span class="kicker-inline">{_KICKER_SPARKLE_SVG}ASTRO-PH.HE</span> {wordmark}</h1>'
    )


def _masthead_kicker_css(scope: str = '') -> str:
    """Return light and dark CSS rules for the archive badge and masthead wordmark."""
    prefix = f'{scope} ' if scope else ''
    dark_inline = (
        'background: rgba(104, 166, 248, 0.15);\n'
        '    border-color: rgba(104, 166, 248, 0.32);\n'
        '    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05), '
        '0 1px 2px rgba(0, 0, 0, 0.25);\n'
        '    color: #93c5fd;'
    )
    dark_inline_indented = dark_inline.replace('\n    ', '\n        ')
    return f"""{prefix}.kicker-inline {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 5px 10px 5px 8px;
    border-radius: 7px;
    background: rgba(24, 88, 168, 0.085);
    border: 1px solid rgba(24, 88, 168, 0.22);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7), 0 1px 2px rgba(15, 23, 42, 0.04);
    font-family: "JetBrains Mono", "SF Mono", Menlo, monospace;
    font-size: 11.5px;
    font-weight: 700;
    letter-spacing: 0.08em;
    line-height: 1;
    text-transform: uppercase;
    color: #15498c;
    white-space: nowrap;
}}
{prefix}.kicker-sparkle {{
    width: 12px;
    height: 12px;
    flex-shrink: 0;
    fill: #1858a8;
}}
{prefix}.masthead-wordmark {{
    display: inline-block;
    font-family: "Outfit", "Avenir Next", "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, sans-serif;
    font-weight: 560;
    letter-spacing: -0.024em;
    line-height: 1.08;
}}
{prefix}.masthead-lead {{
    font-style: normal;
    font-weight: 600;
    color: var(--text);
}}
{prefix}.masthead-accent {{
    font-style: normal;
    font-weight: 400;
    letter-spacing: -0.014em;
    color: #1858a8;
}}
:root[data-theme="dark"] {prefix}.kicker-inline {{
    {dark_inline}
}}
:root[data-theme="dark"] {prefix}.kicker-sparkle {{
    fill: #68a6f8;
}}
:root[data-theme="dark"] {prefix}.masthead-accent {{
    color: #93c5fd;
}}
@media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {prefix}.kicker-inline {{
        {dark_inline_indented}
    }}
    :root:not([data-theme="light"]) {prefix}.kicker-sparkle {{
        fill: #68a6f8;
    }}
    :root:not([data-theme="light"]) {prefix}.masthead-accent {{
        color: #93c5fd;
    }}
}}"""


def _lang_toggle_button_html() -> str:
    """Return the standalone content-language toggle button markup."""
    return (
        '<button id="standalone-lang-toggle" type="button" '
        'class="lang-toggle-btn standalone-lang-btn" '
        'title="Switch content language (中文 / EN)" '
        'aria-label="Switch content language">'
        '<span class="lang-opt" data-lang-opt="zh">中</span>'
        '<span class="lang-opt" data-lang-opt="en">EN</span>'
        '</button>'
    )


def _bilingual_report_css(*, include_highlights: bool = True) -> str:
    """Return pair-aware bilingual display CSS for rendered reports and starred pages."""
    hl_css = ''
    if include_highlights:
        hl_css = (
            ':root:not([data-lang="en"]) .highlight-box p.hl-en.has-hl-pair { display: none; }\n'
            ':root[data-lang="en"] .highlight-box p.hl-zh.has-hl-pair { display: none; }\n'
        )
    return (
        '.standalone-lang-btn {\n'
        '    display: inline-flex;\n'
        '    align-items: center;\n'
        '    height: 30px;\n'
        '    padding: 2px;\n'
        '    gap: 2px;\n'
        '    border: 1px solid var(--border);\n'
        '    border-radius: 999px;\n'
        '    background: var(--surface-alt);\n'
        '    color: var(--text-muted);\n'
        '    cursor: pointer;\n'
        '    font-family: inherit;\n'
        '    font-size: 0.78em;\n'
        '    font-weight: 600;\n'
        '    line-height: 1;\n'
        '    transition: border-color 0.15s, background 0.15s;\n'
        '}\n'
        '.standalone-lang-btn:hover { border-color: var(--primary); }\n'
        '.standalone-lang-btn .lang-opt {\n'
        '    display: inline-flex;\n'
        '    align-items: center;\n'
        '    justify-content: center;\n'
        '    height: 24px;\n'
        '    padding: 0 8px;\n'
        '    border-radius: 999px;\n'
        '    color: var(--text-muted);\n'
        '    transition: background 0.15s, color 0.15s;\n'
        '}\n'
        ':root:not([data-lang="en"]) .standalone-lang-btn .lang-opt[data-lang-opt="zh"],\n'
        ':root[data-lang="en"] .standalone-lang-btn .lang-opt[data-lang-opt="en"] {\n'
        '    background: var(--primary-soft);\n'
        '    color: var(--primary);\n'
        '    font-weight: 700;\n'
        '}\n'
        'html.in-frame .standalone-lang-btn { display: none; }\n'
        f'{hl_css}'
        ':root:not([data-lang="en"]) .paper-item.has-title-pair p.p-title-en { display: none; }\n'
        ':root[data-lang="en"] .paper-item.has-title-pair p.p-title-zh { display: none; }\n'
        ':root[data-lang="en"] .paper-item p.p-title-en {\n'
        '    margin: 4px 0 8px;\n'
        '    font-size: 1.20em;\n'
        '    font-weight: 700;\n'
        '    line-height: 1.38;\n'
        '    color: var(--text);\n'
        '}\n'
        ':root:not([data-lang="en"]) .paper-item p.p-field-en.has-zh-pair { display: none; }\n'
        ':root[data-lang="en"] .paper-item p.has-en-pair { display: none; }\n'
        '.paper-item .method-tag-en { margin: 0 2px 0 6px; }\n'
        ':root[data-lang="en"] .paper-item p:not(.has-en-pair) > strong[data-en-label]::before {\n'
        '    content: attr(data-en-label) " · ";\n'
        '    font-weight: 700;\n'
        '}'
    )


def _bilingual_report_js() -> str:
    """Return JS helpers for syncing bilingual content language across standalone and iframe views."""
    return """\
function currentReportLang() {
    try {
        const saved = localStorage.getItem('arxiv-report:lang');
        if (saved === 'en' || saved === 'zh') return saved;
    } catch (err) {}
    if (typeof document !== 'undefined' && document.documentElement && document.documentElement.getAttribute) {
        const attr = document.documentElement.getAttribute('data-lang');
        if (attr === 'en' || attr === 'zh') return attr;
    }
    return 'zh';
}
function syncReportLangUi(lang) {
    if (typeof document === 'undefined') return;
    if (document.querySelectorAll) {
        document.querySelectorAll('.highlight-box h3').forEach(function(h) {
            const role = h.getAttribute ? h.getAttribute('data-lang-role') : '';
            const txt = (h.textContent || '').trim();
            if (role === 'hl-heading' || txt === '今日重点' || txt === "Today's Highlights") {
                if (h.setAttribute) h.setAttribute('data-lang-role', 'hl-heading');
                h.textContent = lang === 'en' ? "Today's Highlights" : '今日重点';
            }
        });
    }
    const btn = document.getElementById ? document.getElementById('standalone-lang-toggle') : null;
    if (btn) {
        btn.classList.toggle('is-en', lang === 'en');
        if (btn.setAttribute) {
            btn.setAttribute('aria-pressed', lang === 'en' ? 'true' : 'false');
            btn.title = lang === 'en'
                ? 'Switch content language to Chinese (中文)'
                : 'Switch content language to English (EN)';
        }
    }
}
function applyReportLang(lang, persist) {
    const norm = lang === 'en' ? 'en' : 'zh';
    if (typeof document !== 'undefined' && document.documentElement && document.documentElement.setAttribute) {
        document.documentElement.setAttribute('data-lang', norm);
    }
    if (persist) {
        try { localStorage.setItem('arxiv-report:lang', norm); } catch (err) {}
    }
    syncReportLangUi(norm);
}
function initStandaloneLangUi() {
    applyReportLang(currentReportLang(), false);
    const btn = (typeof document !== 'undefined' && document.getElementById)
        ? document.getElementById('standalone-lang-toggle')
        : null;
    if (btn && btn.addEventListener && !(btn.dataset && btn.dataset.langBound === '1')) {
        if (btn.dataset) btn.dataset.langBound = '1';
        btn.addEventListener('click', function() {
            applyReportLang(currentReportLang() === 'en' ? 'zh' : 'en', true);
        });
    }
    if (typeof window !== 'undefined' && window.addEventListener && !window.__arxivLangStorageBound) {
        window.__arxivLangStorageBound = true;
        window.addEventListener('storage', function(e) {
            if (e && e.key === 'arxiv-report:lang' && (e.newValue === 'zh' || e.newValue === 'en')) {
                applyReportLang(e.newValue, false);
            }
        });
    }
}"""


def _write_starred_html(provider: str, as_of: datetime.datetime | None = None) -> str:
    """Write the standalone localStorage-backed starred paper list."""
    starred_bilingual_css = _bilingual_report_css(include_highlights=False)
    starred_lang_btn_html = _lang_toggle_button_html()
    starred_bilingual_js = _bilingual_report_js()
    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Starred Papers · astro-ph.HE Daily Digest</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{_GOOGLE_FONTS_HREF}">
<style>
:root {{
    color-scheme: light dark;
    --bg: #f8f8f6;
    --surface: #ffffff;
    --surface-alt: #f1f2ee;
    --text: #181a1f;
    --text-muted: #5a606c;
    --title-en: #2c3542;
    --primary: #1858a8;
    --primary-soft: #e6effa;
    --highlight: #92400e;
    --highlight-bg: #fef7e3;
    --highlight-pill: #fde68a;
    --highlight-border: #f3d27a;
    --obsidian-bg: #f5f3ff;
    --obsidian-fg: #6d28d9;
    --obsidian-border: #c4b5fd;
    --accent: #1f7a4c;
    --accent-soft: #e6f4ec;
    --status-published-bg: #d1fae5;
    --status-published-fg: #065f46;
    --status-accepted-bg: #dbeafe;
    --status-accepted-fg: #1e40af;
    --status-submitted-bg: #f3f4f6;
    --status-submitted-fg: #4b5563;
    --border: #e4e5e0;
    --border-strong: #cfd1c9;
    --link: #1858a8;
}}
:root[data-theme="dark"] {{
    color-scheme: dark;
    --bg: #0d1014;
    --surface: #14181f;
    --surface-alt: #1b2029;
    --text: #e2e6ed;
    --text-muted: #8c96a5;
    --title-en: #cbd4e1;
    --primary: #68a6f8;
    --primary-soft: rgba(104, 166, 248, 0.14);
    --highlight: #fbbf24;
    --highlight-bg: rgba(251, 191, 36, 0.08);
    --highlight-pill: rgba(251, 191, 36, 0.20);
    --highlight-border: rgba(251, 191, 36, 0.35);
    --obsidian-bg: rgba(139, 92, 246, 0.16);
    --obsidian-fg: #c4b5fd;
    --obsidian-border: rgba(196, 181, 253, 0.35);
    --accent: #6ee7a3;
    --accent-soft: rgba(110, 231, 163, 0.14);
    --status-published-bg: rgba(110, 231, 163, 0.18);
    --status-published-fg: #6ee7a3;
    --status-accepted-bg: rgba(104, 166, 248, 0.18);
    --status-accepted-fg: #68a6f8;
    --status-submitted-bg: rgba(140, 150, 165, 0.18);
    --status-submitted-fg: #a1a8b0;
    --border: #252c37;
    --border-strong: #343d4d;
    --link: #79b8ff;
}}
@media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
        --bg: #0d1014;
        --surface: #14181f;
        --surface-alt: #1b2029;
        --text: #e2e6ed;
        --text-muted: #8c96a5;
        --title-en: #cbd4e1;
        --primary: #68a6f8;
        --primary-soft: rgba(104, 166, 248, 0.14);
        --highlight: #fbbf24;
        --highlight-bg: rgba(251, 191, 36, 0.08);
        --highlight-pill: rgba(251, 191, 36, 0.20);
        --highlight-border: rgba(251, 191, 36, 0.35);
        --obsidian-bg: rgba(139, 92, 246, 0.16);
        --obsidian-fg: #c4b5fd;
        --obsidian-border: rgba(196, 181, 253, 0.35);
        --accent: #6ee7a3;
        --accent-soft: rgba(110, 231, 163, 0.14);
        --status-published-bg: rgba(110, 231, 163, 0.18);
        --status-published-fg: #6ee7a3;
        --status-accepted-bg: rgba(104, 166, 248, 0.18);
        --status-accepted-fg: #68a6f8;
        --status-submitted-bg: rgba(140, 150, 165, 0.18);
        --status-submitted-fg: #a1a8b0;
        --border: #252c37;
        --border-strong: #343d4d;
        --link: #79b8ff;
    }}
}}
* {{ box-sizing: border-box; }}
html {{ overflow-y: scroll; scrollbar-gutter: stable; }}
body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, "Segoe UI",
                 "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
                 "Noto Sans CJK SC", "Source Han Sans SC", sans-serif;
    line-height: 1.65;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    font-feature-settings: "kern", "liga", "palt";
}}
.container {{ max-width: 1076px; margin: 0 auto; padding: 26px 28px 64px; }}
html.in-frame .container {{ padding-top: 26px; }}
.header {{
    position: relative;
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 14px;
    align-items: center;
    margin-bottom: 20px;
    padding: 0 0 16px;
    border-bottom: 1px solid var(--border);
}}
.header::after {{
    content: '';
    position: absolute;
    left: 0;
    bottom: -1px;
    width: 44px;
    height: 2px;
    border-radius: 2px;
    background: var(--primary);
}}
.page-heading {{
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    flex-wrap: wrap;
}}
.page-head-icon {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
    flex-shrink: 0;
    border-radius: 9px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--primary);
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}}
.page-head-icon svg {{
    width: 17px;
    height: 17px;
    display: block;
}}
.page-head-titles {{
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 6px 12px;
    min-width: 0;
}}
h1 {{
    margin: 0;
    font-family: "Outfit", "Avenir Next", "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 1.5rem;
    font-weight: 580;
    letter-spacing: -0.022em;
    line-height: 1.15;
    color: var(--text);
    font-variant-numeric: lining-nums proportional-nums;
}}
.page-head-sep {{
    width: 1.5px;
    height: 18px;
    border-radius: 1px;
    background: var(--border-strong);
    align-self: center;
    flex-shrink: 0;
}}
.page-head-sub {{
    margin: 0;
    font-family: "Outfit", "Avenir Next", "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 1.4rem;
    font-weight: 400;
    letter-spacing: -0.016em;
    line-height: 1.15;
    color: var(--text-muted);
    font-variant-numeric: lining-nums proportional-nums;
}}
.meta {{
    display: inline-flex;
    align-items: center;
    height: 28px;
    margin: 0;
    padding: 0 11px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface);
    color: var(--text-muted);
    font-family: "JetBrains Mono", "SF Mono", Menlo, monospace;
    font-size: 0.74rem;
    font-weight: 500;
    line-height: 1;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}}
.header-right {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
}}
{starred_bilingual_css}
.report-tools {{
    position: sticky;
    top: 0;
    z-index: 20;
    margin: 0 0 18px;
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: color-mix(in srgb, var(--surface) 92%, transparent);
    box-shadow: 0 2px 10px rgba(15, 23, 42, 0.04);
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
.paper-item.is-obsidian {{
    border-color: color-mix(in srgb, var(--obsidian-border) 70%, var(--border));
}}
.paper-item:target,
.paper-item.is-target {{
    border-color: var(--primary);
    box-shadow: 0 0 0 3px var(--primary-soft);
}}
.paper-item.is-hidden {{ display: none; }}
.paper-item h3 {{ margin: 0 0 8px; padding: 0; color: var(--primary); font-size: 1.02em; font-weight: 700; line-height: 1.35; }}
.paper-item a {{ color: var(--link); text-decoration: none; }}
.paper-item h3 a {{ color: inherit; }}
.paper-item a:hover {{ text-decoration: underline; }}
.paper-item p {{ margin: 6px 0; }}
.paper-item p.p-field-en {{
    margin: 6px 0;
    color: var(--text);
}}
.paper-item p.p-title-zh {{
    margin: 4px 0 4px;
    font-size: 1.22em;
    font-weight: 800;
    letter-spacing: -0.01em;
    line-height: 1.42;
    color: var(--text);
}}
.paper-item p.p-title-en {{
    margin: 0 0 7px;
    font-family: "Newsreader", Georgia, "Times New Roman", serif;
    font-size: 1.13em;
    font-weight: 600;
    line-height: 1.38;
    color: var(--title-en);
}}
.paper-item p.p-authors {{
    margin: 0 0 12px;
    padding-bottom: 10px;
    border-bottom: 1px dashed var(--border);
    font-size: 0.85em;
    color: var(--text-muted);
}}
.sr-only-label {{
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
}}
strong {{ color: var(--text); font-weight: 600; }}
.paper-actions {{
    float: right;
    display: inline-flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 6px;
    max-width: min(100%, 216px);
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
.obsidian-save-btn.is-active {{
    background: var(--obsidian-bg);
    color: var(--obsidian-fg);
    border-color: var(--obsidian-border);
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
*::-webkit-scrollbar {{ width: 7px; height: 7px; }}
*::-webkit-scrollbar-track {{ background: transparent; }}
*::-webkit-scrollbar-thumb {{ background: rgba(120, 128, 140, 0.24); border-radius: 4px; }}
*::-webkit-scrollbar-thumb:hover {{ background: rgba(120, 128, 140, 0.4); }}
@media (max-width: 820px) {{
    .container {{ padding: 18px 14px 48px; }}
    html.in-frame .container {{ padding-top: 18px; }}
    h1 {{ font-size: 1.34rem; }}
    .page-head-sub {{ font-size: 1.12rem; }}
    .page-head-sep {{ display: none; }}
}}
@media (max-width: 600px) {{
    .container {{ padding: 16px 12px 40px; }}
    .header {{ align-items: flex-start; }}
    h1 {{ font-size: 1.42rem; }}
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
        <div class="page-heading">
            <span class="page-head-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3.2l2.6 5.4 5.9.9-4.3 4.2 1 5.9-5.2-2.8-5.2 2.8 1-5.9-4.3-4.2 5.9-.9z"/></svg>
            </span>
            <div class="page-head-titles">
                <h1>Starred Papers</h1>
                <span class="page-head-sep" aria-hidden="true"></span>
                <p class="page-head-sub">Saved reading list</p>
            </div>
        </div>
        <div class="header-right">
            <p id="meta" class="meta"></p>
            {starred_lang_btn_html}
        </div>
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
const UNMARKED_PREFIX = 'arxiv-report-obsidian-unmarked:';

function savedObsidianIds() {{
    return Array.isArray(window.__OBSIDIAN_SAVED_IDS__)
        ? new Set(window.__OBSIDIAN_SAVED_IDS__)
        : new Set();
}}

function stateEntries(kind) {{
    const rows = [];
    const savedIds = savedObsidianIds();
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
            const id = String(data.arxivId || '').trim();
            const unmarked = id && localStorage.getItem(UNMARKED_PREFIX + id) === '1';
            data.obsidian = localStorage.getItem(base + ':obsidian') === '1'
                || localStorage.getItem(base + ':crafted') === '1'
                || (!unmarked && Boolean(id) && savedIds.has(id));
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
    return value || 'Unknown';
}}

function normalizeStatusLabel(label) {{
    const s = String(label || '').trim();
    if (!s) return '';
    if (s.includes('Published') || s.includes('Accepted') || s.includes('Submitted')) return s;
    const mPub = s.match(/^(.+?)\\s*已发表$/);
    if (mPub) return 'Published (' + mPub[1].trim() + ')';
    const mAcc = s.match(/^(.+?)\\s*已接收$/);
    if (mAcc) return 'Accepted (' + mAcc[1].trim() + ')';
    const mSub = s.match(/^(.+?)\\s*已投稿$/);
    if (mSub) return 'Submitted (' + mSub[1].trim() + ')';
    if (s === '已发表') return 'Published';
    if (s === '已接收') return 'Accepted';
    if (s === '已投稿') return 'Submitted';
    return s;
}}

function arxivUrl(data) {{
    const rawUrl = String(data.url || '').trim();
    if (/^(?:https?:)?\\/\\/(?:export\\.)?arxiv\\.org\\//i.test(rawUrl)) {{
        return rawUrl.replace(/^http:\\/\\//i, 'https://').replace(/^\\/\\//, 'https://');
    }}
    const id = String(data.arxivId || '').trim().replace(/^arXiv:/i, '');
    if (id && id !== 'Unknown' && id !== '未记录') {{
        return 'https://arxiv.org/abs/' + id;
    }}
    return rawUrl || data.reportHref || '#';
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
        data.questionEn,
        data.method,
        data.methodEn,
        data.result,
        data.resultEn,
        data.caveat,
        data.caveatEn,
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
        external: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17L17 7"/><path d="M9 7h8v8"/><path d="M19 17v2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2"/></svg>',
        similar: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.3 10.9l7.4-3.8"/><path d="M8.3 13.1l7.4 3.8"/></svg>'
    }};
    return icons[name] || '';
}}

function setButtonIcon(btn, name) {{
    btn.innerHTML = iconSvg(name);
}}

function field(label, value, enLabel, pClass, hideLabel) {{
    const p = document.createElement('p');
    p.className = pClass || 'field';
    const strong = document.createElement('strong');
    strong.textContent = label;
    if (hideLabel) strong.className = 'sr-only-label';
    if (enLabel) strong.setAttribute('data-en-label', enLabel);
    p.appendChild(strong);
    p.appendChild(document.createTextNode(text(value)));
    return p;
}}

function enPairField(enLabel, value, methodTag, hasZhPair) {{
    const p = document.createElement('p');
    p.className = hasZhPair ? 'p-field-en has-zh-pair' : 'p-field-en';
    const strong = document.createElement('strong');
    strong.textContent = enLabel + ': ';
    p.appendChild(strong);
    if (methodTag) {{
        const tag = document.createElement('span');
        tag.className = 'method-tag method-tag-en';
        tag.textContent = '[' + methodTag + ']';
        p.appendChild(tag);
    }}
    let body = text(value);
    if (methodTag) {{
        body = body.replace(new RegExp('^\\\\s*\\\\[?' + methodTag + '\\\\]?\\\\s*'), '');
    }}
    p.appendChild(document.createTextNode(body));
    return p;
}}

function methodField(data) {{
    const p = document.createElement('p');
    const strong = document.createElement('strong');
    strong.textContent = '研究方法：';
    strong.setAttribute('data-en-label', 'Method');
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

function extractMethodParts(method, methodTag) {{
    const rawMethod = String(method || '').trim();
    const rawTag = String(methodTag || '').trim().replace(/^\\[|\\]$/g, '').trim();
    const m = rawMethod.match(/^\\s*\\[([A-Za-z][A-Za-z0-9_-]*)\\]\\s*/);
    if (m) {{
        const extracted = m[1];
        let rest = rawMethod.slice(m[0].length).trim();
        const dup = rest.match(/^\\s*\\[([A-Za-z][A-Za-z0-9_-]*)\\]\\s*/);
        if (dup && dup[1].toLowerCase() === extracted.toLowerCase()) {{
            rest = rest.slice(dup[0].length).trim();
        }}
        const finalTag = rawTag || extracted;
        return {{tag: finalTag, body: rest ? '[' + finalTag + '] ' + rest : '[' + finalTag + ']'}};
    }}
    if (rawTag && rawMethod) return {{tag: rawTag, body: '[' + rawTag + '] ' + rawMethod}};
    return {{tag: rawTag, body: rawMethod}};
}}

function buildObsidianMarkdown(data, note) {{
    const arxivId = String(data.arxivId || '').trim();
    const url = String(data.url || '').trim();
    const titleEn = text(data.titleEn);
    const authorsRaw = String(data.authors || '').trim();
    const methodParts = extractMethodParts(data.method, data.methodTag);
    const noteText = String(note || '').trim();

    const lines = [
        titleEn,
        '',
        '[arXiv:' + (arxivId || 'Unknown') + '](' + url + ')',
        '',
        '**作者：** ' + (authorsRaw || 'Unknown'),
        ''
    ];
    const analysis = [];
    if (data.question) analysis.push('**研究问题：** ' + data.question);
    if (methodParts.body) analysis.push('**研究方法：** ' + methodParts.body);
    if (data.result) analysis.push('**研究结果：** ' + data.result);
    if (data.caveat) analysis.push('**潜在不足与延伸方向：** ' + data.caveat);
    if (analysis.length) lines.push(analysis.join('\\n'));
    if (noteText) lines.push('', '---', '', '**个人备注：** ' + noteText);
    return lines.join('\\n') + '\\n';
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

function toggleStarred(data) {{
    writeState(data, 'starred', '');
    render();
}}

function toggleObsidian(data, btn) {{
    const id = String(data.arxivId || '').trim();
    if (data.obsidian) {{
        writeState(data, 'obsidian', '');
        writeState(data, 'crafted', '');
        if (id) localStorage.setItem(UNMARKED_PREFIX + id, '1');
        render();
        return;
    }}
    const note = prompt('Optional personal note:');
    if (note === null) return;
    fetch('/api/obsidian/save', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
            arxiv_id: id,
            url: data.url || '',
            title_en: data.titleEn || '',
            title_zh: data.titleZh || '',
            authors: data.authors || '',
            question: data.question || '',
            method: data.method || '',
            method_tag: data.methodTag || '',
            result: data.result || '',
            caveat: data.caveat || '',
            paper_status: data.status || 'submitted',
            report_date: data.reportDate || '',
            note: note
        }})
    }}).then(function(resp) {{
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
    }}).then(function() {{
        if (id) {{
            localStorage.removeItem(UNMARKED_PREFIX + id);
            if (Array.isArray(window.__OBSIDIAN_SAVED_IDS__) && window.__OBSIDIAN_SAVED_IDS__.indexOf(id) === -1) {{
                window.__OBSIDIAN_SAVED_IDS__.push(id);
            }}
        }}
        writeState(data, 'obsidian', '1');
        data.obsidian = true;
        btn.classList.add('is-active');
        btn.title = 'Remove Obsidian mark';
        btn.setAttribute('aria-label', btn.title);
        btn.textContent = '✓';
        setTimeout(function() {{ render(); }}, 900);
    }}).catch(function(err) {{
        console.error('Failed to save Obsidian note', err);
        alert('Failed to save to Obsidian: ' + err.message);
    }});
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
            + (data.obsidian ? ' is-obsidian' : '');

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

        const obsidianBtn = document.createElement('button');
        obsidianBtn.type = 'button';
        obsidianBtn.className = 'paper-action-btn obsidian-save-btn' + (data.obsidian ? ' is-active' : '');
        obsidianBtn.title = data.obsidian ? 'Remove Obsidian mark' : 'Save to Obsidian';
        obsidianBtn.setAttribute('aria-label', obsidianBtn.title);
        setButtonIcon(obsidianBtn, 'bookmark');
        obsidianBtn.addEventListener('click', function() {{ toggleObsidian(data, obsidianBtn); }});
        if (typeof window !== 'undefined' && window.__ARXIV_CAN_WRITE__ === false) {{
            obsidianBtn.style.display = 'none';
        }}

        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'paper-action-btn';
        copyBtn.title = 'Copy Markdown';
        copyBtn.setAttribute('aria-label', 'Copy Markdown');
        setButtonIcon(copyBtn, 'copy');
        copyBtn.addEventListener('click', function() {{
            const note = prompt('Optional personal note:');
            if (note === null) return;
            copyText(buildObsidianMarkdown(data, note), copyBtn, '✓');
        }});

        const jumpBtn = document.createElement('button');
        jumpBtn.type = 'button';
        jumpBtn.className = 'paper-action-btn';
        jumpBtn.title = 'Open in daily report';
        jumpBtn.setAttribute('aria-label', 'Open in daily report');
        setButtonIcon(jumpBtn, 'external');
        jumpBtn.addEventListener('click', function() {{
            if (window.__ARXIV_STATIC_SITE__ && data.reportDate) {{
                const anchor = data.number ? '/p' + data.number : '';
                window.top.location.href = 'index.html#day/' + data.reportDate + anchor;
                return;
            }}
            let href = String(data.reportHref || '').trim();
            const proto = (typeof window !== 'undefined' && window.location && window.location.protocol) || '';
            if (proto === 'http:' || proto === 'https:') {{
                const legacy = href.match(/arXiv_astro_ph_HE_daily_report_(\\d{{4}}-\\d{{2}}-\\d{{2}})\\.html(#p\\d+)?$/);
                if (legacy) {{
                    href = '/r/' + legacy[1] + (legacy[2] || '');
                }} else if (!href && data.reportDate) {{
                    href = '/r/' + data.reportDate + (data.number ? '#p' + data.number : '');
                }}
            }}
            window.top.location.href = href || arxivUrl(data);
        }});

        const similarBtn = document.createElement('button');
        similarBtn.type = 'button';
        similarBtn.className = 'paper-action-btn similar-link-btn';
        similarBtn.title = 'Find similar papers';
        similarBtn.setAttribute('aria-label', 'Find similar papers');
        setButtonIcon(similarBtn, 'similar');
        similarBtn.addEventListener('click', function() {{
            const bare = String(data.arxivId || '')
                .trim()
                .replace(/^arXiv:/i, '')
                .replace(/v\\d+$/i, '');
            if (bare) {{
                window.top.location.href = window.__ARXIV_STATIC_SITE__
                    ? 'index.html#similar/' + encodeURIComponent(bare)
                    : '/similar/' + encodeURIComponent(bare);
            }}
        }});

        if (window.__ARXIV_CAN_WRITE__ === false) {{
            obsidianBtn.style.display = 'none';
        }}

        actions.appendChild(starBtn);
        actions.appendChild(obsidianBtn);
        actions.appendChild(copyBtn);
        actions.appendChild(jumpBtn);
        actions.appendChild(similarBtn);
        h3.appendChild(actions);
        h3.appendChild(document.createTextNode('[' + String(index + 1) + '] '));
        const link = document.createElement('a');
        const href = arxivUrl(data);
        link.href = href;
        if (/^https?:\\/\\//i.test(href)) {{
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
        }} else if (href !== '#') {{
            link.target = '_top';
        }}
        link.textContent = text(data.arxivId);
        h3.appendChild(link);
        const normStatus = normalizeStatusLabel(data.statusLabel);
        const showStatus = data.statusVisible === true
            || (data.statusVisible === undefined && normStatus && normStatus !== 'Submitted');
        if (showStatus) {{
            const status = document.createElement('span');
            status.className = 'status-tag status-' + (data.status || 'submitted');
            status.textContent = normStatus || data.statusLabel;
            h3.appendChild(status);
        }}

        item.appendChild(h3);
        if (data.titleZh) item.appendChild(field('中文标题：', data.titleZh, 'Title (ZH)', 'p-title-zh', true));
        if (data.titleEn) item.appendChild(field('英文标题：', data.titleEn, 'Title (EN)', 'p-title-en', true));
        if (data.titleZh && data.titleEn) item.classList.add('has-title-pair');
        if (data.authors) item.appendChild(field('作者：', data.authors, 'Authors', 'p-authors', true));
        if (data.question) {{
            const qP = field('研究问题：', data.question, 'Research Question');
            if (data.questionEn) qP.classList.add('has-en-pair');
            item.appendChild(qP);
        }}
        if (data.questionEn) item.appendChild(enPairField('Research Question', data.questionEn, '', Boolean(data.question)));
        if (data.method) {{
            const mP = methodField(data);
            if (data.methodEn) mP.classList.add('has-en-pair');
            item.appendChild(mP);
            if (data.methodEn) item.appendChild(enPairField('Method', data.methodEn, data.methodTag, true));
        }} else if (data.methodEn) {{
            item.appendChild(enPairField('Method', data.methodEn, data.methodTag, false));
        }}
        if (data.result) {{
            const rP = field('研究结果：', data.result, 'Results');
            if (data.resultEn) rP.classList.add('has-en-pair');
            item.appendChild(rP);
        }}
        if (data.resultEn) item.appendChild(enPairField('Results', data.resultEn, '', Boolean(data.result)));
        if (data.caveat) {{
            const cP = field('潜在不足与延伸方向：', data.caveat, 'Limitations & Next Steps');
            if (data.caveatEn) cP.classList.add('has-en-pair');
            item.appendChild(cP);
            if (data.caveatEn) item.appendChild(enPairField('Limitations & Next Steps', data.caveatEn, '', true));
        }} else if (data.caveatEn) {{
            item.appendChild(enPairField('Limitations & Next Steps', data.caveatEn, '', false));
        }}
        list.appendChild(item);
    }});
    initStarredTools(rows);
}}
{starred_bilingual_js}
if (typeof document !== 'undefined' && document.documentElement) {{
    try {{
        if (window.self !== window.top) {{
            document.documentElement.classList.add('in-frame');
        }}
        const savedTheme = localStorage.getItem('arxiv-report:theme');
        if (savedTheme === 'light' || savedTheme === 'dark') {{
            document.documentElement.setAttribute('data-theme', savedTheme);
        }}
    }} catch (err) {{}}
    initStandaloneLangUi();
    if (typeof window !== 'undefined' && window.addEventListener) {{
        window.addEventListener('message', function(e) {{
            const msg = e && e.data;
            if (!msg) return;
            if (msg.type === 'arxiv-lang-change') {{
                applyReportLang(msg.lang, false);
                return;
            }}
            if (msg.type !== 'arxiv-theme-change') return;
            if (msg.theme === 'light' || msg.theme === 'dark') {{
                document.documentElement.setAttribute('data-theme', msg.theme);
            }} else {{
                document.documentElement.removeAttribute('data-theme');
            }}
        }});
    }}
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


def _resolve_render_db_path(db_path: str | None) -> str | None:
    if db_path:
        return db_path if os.path.exists(db_path) else None
    if os.path.normpath(REPORTS_DIR) != 'reports':
        candidate = os.path.join(REPORTS_DIR, '.data', 'papers.db')
        return candidate if os.path.exists(candidate) else None
    candidate = default_db_path()
    return candidate if os.path.exists(candidate) else None


def _decorate_fragment_cards(fragment_html: str, db_path: str | None = None) -> str:
    """Attach topic attributes and clickable topic, object, and SciX badges onto cards."""
    clean = strip_paper_tags(fragment_html)
    parsed = parse_fragment(clean)
    spans = iter_paper_item_spans(clean)
    if not spans:
        return clean

    resolved_db = _resolve_render_db_path(db_path)
    pids = [item.get('pid', '') for item in parsed['items'].values() if item.get('pid')]
    cite_map = get_citation_map(pids, db_path=resolved_db) if resolved_db and pids else {}

    out: list[str] = []
    cursor = 0
    for num, start, end in spans:
        out.append(clean[cursor:start])
        card = clean[start:end]
        item = parsed['items'].get(num, {})
        slugs: list[str] = item.get('topic_slugs') or []
        pid = item.get('pid') or ''
        obj_text = '\n'.join(
            part
            for part in (
                item.get('title_en', ''),
                item.get('title_zh', ''),
                item.get('question_zh', ''),
                item.get('result_zh', ''),
            )
            if part
        )
        objs = extract_objects(obj_text)
        if slugs:
            attr = f' data-topics="{_html_escape(",".join(slugs))}"'
            gt_pos = card.find('>')
            if gt_pos != -1:
                card = card[:gt_pos] + attr + card[gt_pos:]

        badges: list[str] = []
        for slug in slugs:
            spec = TOPIC_BY_SLUG.get(slug)
            if spec:
                badges.append(
                    f'<button type="button" class="card-topic-pill" data-topic="{spec.slug}" '
                    f'title="{_html_escape(spec.label_en)}">{_html_escape(spec.short)}</button>'
                )
        for obj in objs:
            href = f'/search?q={quote_plus(obj)}'
            badges.append(
                f'<a class="obj-tag" href="{href}" target="_top" data-obj="{_html_escape(obj)}" '
                f'title="Search {_html_escape(obj)} across corpus">⌖ {_html_escape(obj)}</a>'
            )
        if pid:
            cinfo = cite_map.get(pid)
            c_url = cinfo['scix_url'] if cinfo else scix_abs_url(pid, '')
            c_count = cinfo['citation_count'] if cinfo else 0
            c_src = (cinfo['source'] if cinfo else 'scix').upper()
            if c_count > 0:
                badges.append(
                    f'<a class="scix-tag is-cited" href="{_html_escape(c_url)}" target="_blank" '
                    f'rel="noopener noreferrer" title="Cited {c_count} times ({c_src}) · Open in NASA SciX">'
                    f'SciX · {c_count}</a>'
                )
            else:
                badges.append(
                    f'<a class="scix-tag" href="{_html_escape(c_url)}" target="_blank" '
                    f'rel="noopener noreferrer" title="Open in NASA SciX / ADS">SciX</a>'
                )

        if badges and card.endswith('</div>'):
            tags_div = f'<div class="paper-tags">{"".join(badges)}</div>\n</div>'
            card = card[:-6].rstrip() + '\n' + tags_div
        out.append(card)
        cursor = end

    out.append(clean[cursor:])
    return ''.join(out)


def render_report_html(
    date_str: str,
    paper_count: int,
    provider: str,
    body_content: str,
    db_path: str | None = None,
) -> str:
    """Wrap a finalized report body fragment in the full standalone HTML document.

    Args:
        date_str: Report date formatted as ``YYYY-MM-DD``.
        paper_count: Total number of papers in the daily report.
        provider: Provider slug shown in the header badge.
        body_content: Clean body fragment (index box, optional highlights, cards).
        db_path: Optional SQLite database path override for citation lookup.

    Returns:
        Complete UTF-8 standalone HTML document string.
    """
    decorated_body = _decorate_fragment_cards(body_content, db_path=db_path)
    topic_catalog_json = json.dumps(
        [{'slug': t.slug, 'short': t.short, 'label': t.label_en} for t in TOPICS],
        ensure_ascii=False,
    )
    extra_chip_css = topic_chip_css()
    report_kicker_css = _masthead_kicker_css('.report-header')
    report_title_html = _masthead_title_html('Daily Digest')
    report_bilingual_css = _bilingual_report_css(include_highlights=True)
    report_lang_btn_html = _lang_toggle_button_html()
    report_bilingual_js = _bilingual_report_js()
    html_layout = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>astro-ph.HE Daily Digest · {date_str}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{_GOOGLE_FONTS_HREF}">
<style>
:root {{
    color-scheme: light dark;
    --bg: #f8f8f6;
    --surface: #ffffff;
    --surface-alt: #f1f2ee;
    --text: #181a1f;
    --text-muted: #5a606c;
    --title-en: #2c3542;
    --primary: #1858a8;
    --primary-soft: #e6effa;
    --accent: #1f7a4c;
    --accent-soft: #e6f4ec;
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
    --obsidian-bg: #f5f3ff;
    --obsidian-fg: #6d28d9;
    --obsidian-border: #c4b5fd;
    --border: #e4e5e0;
    --link: #1858a8;
    --shadow: 0 2px 10px rgba(24, 26, 31, 0.04);
    --report-header-muted: #5a606c;
    --report-header-pill-bg: #f1f2ee;
    --report-header-pill-text: #5a606c;
    --report-header-pill-strong: #181a1f;
}}
:root[data-theme="dark"] {{
    color-scheme: dark;
    --bg: #0d1014;
    --surface: #14181f;
    --surface-alt: #1b2029;
    --text: #e2e6ed;
    --text-muted: #8c96a5;
    --title-en: #cbd4e1;
    --primary: #68a6f8;
    --primary-soft: rgba(104, 166, 248, 0.14);
    --accent: #6ee7a3;
    --accent-soft: rgba(110, 231, 163, 0.14);
    --highlight: #fbbf24;
    --highlight-bg: rgba(251, 191, 36, 0.08);
    --highlight-pill: rgba(251, 191, 36, 0.20);
    --highlight-border: rgba(251, 191, 36, 0.35);
    --status-published-bg: rgba(110, 231, 163, 0.18);
    --status-published-fg: #6ee7a3;
    --status-accepted-bg: rgba(104, 166, 248, 0.18);
    --status-accepted-fg: #68a6f8;
    --status-submitted-bg: rgba(140, 150, 165, 0.18);
    --status-submitted-fg: #a1a8b0;
    --obsidian-bg: rgba(139, 92, 246, 0.16);
    --obsidian-fg: #c4b5fd;
    --obsidian-border: rgba(196, 181, 253, 0.35);
    --border: #252c37;
    --link: #79b8ff;
    --shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
    --report-header-muted: #8c96a5;
    --report-header-pill-bg: #1b2029;
    --report-header-pill-text: #8c96a5;
    --report-header-pill-strong: #e2e6ed;
}}
@media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
        --bg: #0d1014;
        --surface: #14181f;
        --surface-alt: #1b2029;
        --text: #e2e6ed;
        --text-muted: #8c96a5;
        --title-en: #cbd4e1;
        --primary: #68a6f8;
        --primary-soft: rgba(104, 166, 248, 0.14);
        --accent: #6ee7a3;
        --accent-soft: rgba(110, 231, 163, 0.14);
        --highlight: #fbbf24;
        --highlight-bg: rgba(251, 191, 36, 0.08);
        --highlight-pill: rgba(251, 191, 36, 0.20);
        --highlight-border: rgba(251, 191, 36, 0.35);
        --status-published-bg: rgba(110, 231, 163, 0.18);
        --status-published-fg: #6ee7a3;
        --status-accepted-bg: rgba(104, 166, 248, 0.18);
        --status-accepted-fg: #68a6f8;
        --status-submitted-bg: rgba(140, 150, 165, 0.18);
        --status-submitted-fg: #a1a8b0;
        --obsidian-bg: rgba(139, 92, 246, 0.16);
        --obsidian-fg: #c4b5fd;
        --obsidian-border: rgba(196, 181, 253, 0.35);
        --border: #252c37;
        --link: #79b8ff;
        --shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
        --report-header-muted: #8c96a5;
        --report-header-pill-bg: #1b2029;
        --report-header-pill-text: #8c96a5;
        --report-header-pill-strong: #e2e6ed;
    }}
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{
    font-family: "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, "Segoe UI",
                 "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
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
    padding: 28px 24px 64px;
}}
html.in-frame .container {{ padding-top: 16px; }}
a {{ color: var(--link); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
code, .entry-id {{
    font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
    font-size: 0.9em;
}}
.report-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 14px;
    margin: 0 0 22px;
    padding: 8px 0 18px;
    border-bottom: 2px solid var(--border);
    text-align: left;
}}
.report-header-title {{ min-width: 0; }}
.report-header .kicker {{
    margin: 0 0 5px;
    font-family: "JetBrains Mono", "SF Mono", Menlo, monospace;
    font-size: 0.72em;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--report-header-muted);
}}
.report-header h1 {{
    margin: 0;
    display: inline-flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 13px;
    font-size: 1.76em;
    font-weight: 560;
    letter-spacing: -0.022em;
    line-height: 1.1;
    color: var(--text);
}}
{report_kicker_css}
.report-meta {{
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    font-size: 0.84em;
}}
{report_bilingual_css}
.report-meta .pill {{
    display: inline-flex;
    align-items: center;
    padding: 3px 12px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--report-header-pill-bg);
    color: var(--report-header-pill-text);
    font-family: "JetBrains Mono", "SF Mono", Menlo, monospace;
    font-variant-numeric: tabular-nums;
}}
.report-meta .pill strong {{
    color: var(--report-header-pill-strong);
    margin-right: 4px;
    font-weight: 700;
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
.mini-index a.is-obsidian {{
    background: var(--obsidian-bg);
    color: var(--obsidian-fg);
    border-color: var(--obsidian-border);
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
    display: none !important;
    background: var(--surface-alt);
    border: 1px solid var(--border);
    padding: 16px 20px;
    border-radius: 12px;
    margin: 22px 0;
}}
.index-box p {{ margin: 8px 0; }}
.index-fold summary {{
    cursor: pointer;
    font-size: 0.88em;
    font-weight: 700;
    color: var(--text);
    list-style-position: inside;
    user-select: none;
}}
.index-fold[open] summary {{ margin-bottom: 10px; }}
.index-box a {{
    display: inline-block;
    background: var(--primary-soft);
    color: var(--primary);
    padding: 1px 9px;
    margin: 2px 1px;
    border-radius: 5px;
    font-size: 0.88em;
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
.paper-item p.p-field-en {{
    margin: 6px 0;
    color: var(--text);
}}
.paper-item p.p-title-zh {{
    margin: 4px 0 4px;
    font-size: 1.22em;
    font-weight: 800;
    letter-spacing: -0.01em;
    line-height: 1.42;
    color: var(--text);
}}
.paper-item p.p-title-en {{
    margin: 0 0 7px;
    font-family: "Newsreader", Georgia, "Times New Roman", serif;
    font-size: 1.13em;
    font-weight: 600;
    line-height: 1.38;
    color: var(--title-en);
}}
.paper-item p.p-authors {{
    margin: 0 0 12px;
    padding-bottom: 10px;
    border-bottom: 1px dashed var(--border);
    font-size: 0.85em;
    color: var(--text-muted);
}}
.sr-only-label {{
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
}}
.fallback-abstract {{
    margin-top: 8px;
    padding: 8px 12px;
    border: 1px dashed var(--border);
    border-radius: 8px;
    background: var(--surface-alt);
    font-size: 0.92em;
}}
.fallback-abstract summary {{
    cursor: pointer;
    color: var(--text-muted);
    font-weight: 600;
}}
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
.obsidian-save-btn.is-active {{
    background: var(--obsidian-bg);
    color: var(--obsidian-fg);
    border-color: var(--obsidian-border);
}}
.paper-item.is-hidden {{ display: none; }}
.paper-item.is-starred {{
    border-color: var(--highlight-border);
    box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.12);
}}
.paper-item.is-obsidian {{
    border-color: color-mix(in srgb, var(--obsidian-border) 70%, var(--border));
}}
h3 {{
    color: var(--primary);
    margin: 0 0 8px;
    padding: 0;
    font-size: 1.02em;
    font-weight: 700;
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
{extra_chip_css}
@media (max-width: 600px) {{
    .container {{ padding: 16px 12px 40px; }}
    .report-header {{ padding: 6px 0 14px; align-items: flex-start; }}
    .report-meta {{ justify-content: flex-start; }}
    .report-header h1 {{ font-size: 1.48em; gap: 6px 10px; }}
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
        <div class="report-header-title">
            {report_title_html}
        </div>
        <div class="report-meta">
            <span class="pill"><strong>{date_str}</strong></span>
            <span class="pill"><strong>{paper_count}</strong> papers</span>
            <span class="pill">Generated by {provider}</span>
            {report_lang_btn_html}
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
            <span id="visible-count" class="visible-count">{paper_count} / {paper_count} shown</span>
        </div>
        <div id="topic-chips" class="topic-chips-bar" aria-label="Filter by topic"></div>
        <nav id="paper-mini-index" class="mini-index" aria-label="Paper index"></nav>
    </section>
    <div id="empty-filter" class="empty-filter">No papers match the current filters.</div>
    {decorated_body}
</div>
<script>
function focusPaperHash(raw) {{
    const id = String(raw || '').replace(/^#/, '');
    if (!/^p\\d+$/.test(id) || typeof document === 'undefined') return;
    let target = document.getElementById ? document.getElementById(id) : null;
    if (!target && document.querySelectorAll) {{
        document.querySelectorAll('.paper-item').forEach(function(item) {{
            if (item.id === id) target = item;
        }});
    }}
    if (!target) return;
    if (document.querySelectorAll) {{
        document.querySelectorAll('.paper-item').forEach(function(el) {{
            el.classList.remove('is-target');
        }});
    }}
    target.classList.add('is-target');
    if (target.scrollIntoView) {{
        target.scrollIntoView({{behavior: 'auto', block: 'center'}});
    }}
}}
{report_bilingual_js}
if (typeof document !== 'undefined' && document.documentElement) {{
    try {{
        if (window.self !== window.top) {{
            document.documentElement.classList.add('in-frame');
        }}
        const savedTheme = localStorage.getItem('arxiv-report:theme');
        if (savedTheme === 'light' || savedTheme === 'dark') {{
            document.documentElement.setAttribute('data-theme', savedTheme);
        }}
    }} catch (err) {{}}
    initStandaloneLangUi();
    if (typeof window !== 'undefined' && window.addEventListener) {{
        window.addEventListener('message', function(e) {{
            const msg = e && e.data;
            if (!msg) return;
            if (msg.type === 'arxiv-scroll-target') {{
                focusPaperHash(msg.targetId);
                return;
            }}
            if (msg.type === 'arxiv-lang-change') {{
                applyReportLang(msg.lang, false);
                return;
            }}
            if (msg.type !== 'arxiv-theme-change') return;
            if (msg.theme === 'light' || msg.theme === 'dark') {{
                document.documentElement.setAttribute('data-theme', msg.theme);
            }} else {{
                document.documentElement.removeAttribute('data-theme');
            }}
        }});
        window.addEventListener('hashchange', function() {{
            if (window.location && window.location.hash) {{
                focusPaperHash(window.location.hash);
            }}
        }});
    }}
}}
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
const REPORT_DATE = '{date_str}';
const TOPIC_CATALOG = {topic_catalog_json};
const UNMARKED_PREFIX = 'arxiv-report-obsidian-unmarked:';

function normalizeText(text) {{
    return (text || '').toLowerCase().replace(/\\s+/g, ' ').trim();
}}

function paperField(item, label) {{
    const paras = item.querySelectorAll('p');
    for (const p of paras) {{
        const strong = p.querySelector('strong');
        if (strong && strong.textContent.trim() === label) {{
            let raw = p.textContent.slice(strong.textContent.length);
            const enTag = p.querySelector ? p.querySelector('.method-tag-en') : null;
            if (enTag && enTag.textContent) {{
                const tagStr = enTag.textContent;
                const idx = raw.indexOf(tagStr);
                if (idx === 0 || (idx > 0 && raw.slice(0, idx).trim() === '')) {{
                    raw = raw.slice(idx + tagStr.length);
                }}
            }}
            return raw.trim();
        }}
    }}
    return '';
}}

function paperFieldAny(item, labels) {{
    for (const lbl of labels) {{
        const val = paperField(item, lbl);
        if (val) return val;
    }}
    return '';
}}

function normalizeStatusLabel(label) {{
    const s = String(label || '').trim();
    if (!s) return '';
    if (s.includes('Published') || s.includes('Accepted') || s.includes('Submitted')) return s;
    const mPub = s.match(/^(.+?)\\s*已发表$/);
    if (mPub) return 'Published (' + mPub[1].trim() + ')';
    const mAcc = s.match(/^(.+?)\\s*已接收$/);
    if (mAcc) return 'Accepted (' + mAcc[1].trim() + ')';
    const mSub = s.match(/^(.+?)\\s*已投稿$/);
    if (mSub) return 'Submitted (' + mSub[1].trim() + ')';
    if (s === '已发表') return 'Published';
    if (s === '已接收') return 'Accepted';
    if (s === '已投稿') return 'Submitted';
    return s;
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

function buildPaperData(item) {{
    const link = item.querySelector('h3 a');
    return {{
        url: link ? link.href : '',
        arxivId: link ? link.textContent.trim() : '',
        titleEn: paperField(item, '英文标题：'),
        titleZh: paperField(item, '中文标题：'),
        authors: paperField(item, '作者：'),
        question: paperField(item, '研究问题：'),
        questionEn: paperFieldAny(item, ['Research Question:', 'Research Question']),
        method: paperField(item, '研究方法：'),
        methodEn: paperFieldAny(item, ['Method:', 'Method']),
        methodTag: paperMethod(item),
        result: paperField(item, '研究结果：'),
        resultEn: paperFieldAny(item, ['Results:', 'Results']),
        caveat: paperField(item, '潜在不足与延伸方向：'),
        caveatEn: paperFieldAny(item, [
            'Limitations & Next Steps:',
            'Limitations & Next Steps',
            'Limitations and Next Steps:',
            'Limitations and Next Steps'
        ]),
        status: paperStatus(item),
        reportDate: REPORT_DATE
    }};
}}

function paperIdentity(item) {{
    const data = buildPaperData(item);
    return data.arxivId || item.id || 'paper';
}}

function paperStateBase(item) {{
    return 'arxiv-report:' + REPORT_DATE + ':' + paperIdentity(item);
}}

function paperStateKey(item, kind) {{
    return paperStateBase(item) + ':' + kind;
}}

function paperSnapshot(item) {{
    const data = buildPaperData(item);
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
        copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1"/></svg>',
        similar: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.3 10.9l7.4-3.8"/><path d="M8.3 13.1l7.4 3.8"/></svg>'
    }};
    return icons[name] || '';
}}

function setButtonIcon(btn, name) {{
    btn.innerHTML = iconSvg(name);
}}

function readRawStorage(key) {{
    try {{
        return window.localStorage.getItem(key) || '';
    }} catch (_err) {{
        return '';
    }}
}}

function writeRawStorage(key, value) {{
    try {{
        if (value) {{
            window.localStorage.setItem(key, value);
        }} else {{
            window.localStorage.removeItem(key);
        }}
    }} catch (_err) {{}}
}}

function isPaperObsidianSaved(item) {{
    const id = paperIdentity(item);
    if (readPaperState(item, 'obsidian') === '1' || readPaperState(item, 'crafted') === '1') {{
        return true;
    }}
    if (!id || readRawStorage(UNMARKED_PREFIX + id) === '1') return false;
    const savedIds = Array.isArray(window.__OBSIDIAN_SAVED_IDS__) ? window.__OBSIDIAN_SAVED_IDS__ : [];
    return savedIds.indexOf(id) !== -1;
}}

function syncPaperState(item) {{
    const isStarred = readPaperState(item, 'starred') === '1';
    const isObsidian = isPaperObsidianSaved(item);
    item.dataset.starred = isStarred ? 'starred' : '';
    item.dataset.obsidian = isObsidian ? 'obsidian' : '';
    item.classList.toggle('is-starred', isStarred);
    item.classList.toggle('is-obsidian', isObsidian);

    const starBtn = item.querySelector('.star-toggle-btn');
    if (starBtn) {{
        setButtonIcon(starBtn, isStarred ? 'star-filled' : 'star');
        starBtn.classList.toggle('is-active', isStarred);
        starBtn.title = isStarred ? 'Remove star' : 'Mark as starred';
        starBtn.setAttribute('aria-label', starBtn.title);
    }}
    const obsidianBtn = item.querySelector('.obsidian-save-btn');
    if (obsidianBtn) {{
        obsidianBtn.classList.toggle('is-active', isObsidian);
        obsidianBtn.title = isObsidian ? 'Remove Obsidian mark' : 'Save to Obsidian';
        obsidianBtn.setAttribute('aria-label', obsidianBtn.title);
    }}
    const indexLink = document.querySelector('#paper-mini-index a[data-target-id="' + item.id + '"]');
    if (indexLink) {{
        indexLink.classList.toggle('is-starred', isStarred);
        indexLink.classList.toggle('is-obsidian', isObsidian);
    }}
}}

function notifyPaperStateChanged() {{
    document.dispatchEvent(new CustomEvent('paper-state-change'));
}}

function extractMethodParts(method, methodTag) {{
    const rawMethod = String(method || '').trim();
    const rawTag = String(methodTag || '').trim().replace(/^\\[|\\]$/g, '').trim();
    const m = rawMethod.match(/^\\s*\\[([A-Za-z][A-Za-z0-9_-]*)\\]\\s*/);
    if (m) {{
        const extracted = m[1];
        let rest = rawMethod.slice(m[0].length).trim();
        const dup = rest.match(/^\\s*\\[([A-Za-z][A-Za-z0-9_-]*)\\]\\s*/);
        if (dup && dup[1].toLowerCase() === extracted.toLowerCase()) {{
            rest = rest.slice(dup[0].length).trim();
        }}
        const finalTag = rawTag || extracted;
        return {{tag: finalTag, body: rest ? '[' + finalTag + '] ' + rest : '[' + finalTag + ']'}};
    }}
    if (rawTag && rawMethod) return {{tag: rawTag, body: '[' + rawTag + '] ' + rawMethod}};
    return {{tag: rawTag, body: rawMethod}};
}}

function buildObsidianMarkdown(item, note) {{
    const data = buildPaperData(item);
    const arxivId = String(data.arxivId || '').trim();
    const url = String(data.url || '').trim();
    const titleEn = String(data.titleEn || '').trim() || 'Unknown';
    const authorsRaw = String(data.authors || '').trim();
    const methodParts = extractMethodParts(data.method, data.methodTag);
    const noteText = String(note || '').trim();

    const lines = [
        titleEn,
        '',
        '[arXiv:' + (arxivId || 'Unknown') + '](' + url + ')',
        '',
        '**作者：** ' + (authorsRaw || 'Unknown'),
        ''
    ];
    const analysis = [];
    if (data.question) analysis.push('**研究问题：** ' + data.question);
    if (methodParts.body) analysis.push('**研究方法：** ' + methodParts.body);
    if (data.result) analysis.push('**研究结果：** ' + data.result);
    if (data.caveat) analysis.push('**潜在不足与延伸方向：** ' + data.caveat);
    if (analysis.length) lines.push(analysis.join('\\n'));
    if (noteText) lines.push('', '---', '', '**个人备注：** ' + noteText);
    return lines.join('\\n') + '\\n';
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

function askNote() {{
    return prompt('Optional personal note:');
}}

function toggleObsidianSave(item, btn, e) {{
    e.preventDefault();
    const data = buildPaperData(item);
    const id = String(data.arxivId || '').trim();
    if (isPaperObsidianSaved(item)) {{
        writePaperState(item, 'obsidian', '');
        writePaperState(item, 'crafted', '');
        if (id) writeRawStorage(UNMARKED_PREFIX + id, '1');
        writePaperSnapshot(item);
        syncPaperState(item);
        notifyPaperStateChanged();
        return;
    }}
    const note = askNote();
    if (note === null) return;
    fetch('/api/obsidian/save', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
            arxiv_id: id,
            url: data.url || '',
            title_en: data.titleEn || '',
            title_zh: data.titleZh || '',
            authors: data.authors || '',
            question: data.question || '',
            method: data.method || '',
            method_tag: data.methodTag || '',
            result: data.result || '',
            caveat: data.caveat || '',
            paper_status: data.status || 'submitted',
            report_date: data.reportDate || '',
            note: note
        }})
    }}).then(function(resp) {{
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
    }}).then(function() {{
        if (id) {{
            writeRawStorage(UNMARKED_PREFIX + id, '');
            if (Array.isArray(window.__OBSIDIAN_SAVED_IDS__) && window.__OBSIDIAN_SAVED_IDS__.indexOf(id) === -1) {{
                window.__OBSIDIAN_SAVED_IDS__.push(id);
            }}
        }}
        writePaperState(item, 'obsidian', '1');
        writePaperSnapshot(item);
        syncPaperState(item);
        notifyPaperStateChanged();
        btn.textContent = '✓';
        btn.title = 'Saved to Obsidian';
        setTimeout(function() {{
            setButtonIcon(btn, 'bookmark');
            syncPaperState(item);
        }}, 1600);
    }}).catch(function(err) {{
        console.error('Failed to save Obsidian note', err);
        alert('Failed to save to Obsidian: ' + err.message);
    }});
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
    btn.className = 'paper-action-btn obsidian-save-btn';
    setButtonIcon(btn, 'bookmark');
    btn.title = 'Save to Obsidian';
    btn.setAttribute('aria-label', 'Save to Obsidian');
    btn.addEventListener('click', function(e) {{
        toggleObsidianSave(item, btn, e);
    }});
    if (window.__ARXIV_CAN_WRITE__ === false) {{
        btn.style.display = 'none';
    }}

    const copyMd = document.createElement('button');
    copyMd.type = 'button';
    copyMd.className = 'paper-action-btn';
    setButtonIcon(copyMd, 'copy');
    copyMd.title = 'Copy Markdown';
    copyMd.setAttribute('aria-label', 'Copy Markdown');
    copyMd.addEventListener('click', function() {{
        const note = askNote();
        if (note === null) return;
        copyText(buildObsidianMarkdown(item, note), copyMd, '✓');
    }});

    const similarBtn = document.createElement('button');
    similarBtn.type = 'button';
    similarBtn.className = 'paper-action-btn similar-link-btn';
    setButtonIcon(similarBtn, 'similar');
    similarBtn.title = 'Find similar papers';
    similarBtn.setAttribute('aria-label', 'Find similar papers');
    similarBtn.addEventListener('click', function() {{
        const bare = String(paperIdentity(item) || '')
            .trim()
            .replace(/^arXiv:/i, '')
            .replace(/v\\d+$/i, '');
        if (bare) {{
            window.top.location.href = window.__ARXIV_STATIC_SITE__
                ? 'index.html#similar/' + encodeURIComponent(bare)
                : '/similar/' + encodeURIComponent(bare);
        }}
    }});

    actions.appendChild(starBtn);
    actions.appendChild(btn);
    actions.appendChild(copyMd);
    actions.appendChild(similarBtn);
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
    const chipsBar = document.getElementById('topic-chips');
    const empty = document.getElementById('empty-filter');
    if (!papers.length || !search || !status || !method || !clear || !count || !index) return;

    const methods = Array.from(new Set(papers.map(paperMethod).filter(Boolean))).sort();
    methods.forEach(function(name) {{
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name;
        method.appendChild(option);
    }});

    document.querySelectorAll('.highlight-box h3').forEach(function(h) {{
        const txt = (h.textContent || '').trim();
        if (txt === '今日重点' || txt === "Today's Highlights") {{
            h.setAttribute('data-lang-role', 'hl-heading');
        }}
    }});
    const hlGroups = new Map();
    document.querySelectorAll('.highlight-box p').forEach(function(p) {{
        const attrLang = ((p.getAttribute && p.getAttribute('data-lang')) || '').toLowerCase();
        const isEn = attrLang === 'en'
            || p.classList.contains('hl-en')
            || (attrLang !== 'zh' && !p.classList.contains('hl-zh') && !/[\\u4e00-\\u9fff]/.test(p.textContent || ''));
        p.classList.add(isEn ? 'hl-en' : 'hl-zh');
        const link = p.querySelector ? p.querySelector('a') : null;
        const href = link && link.getAttribute ? link.getAttribute('href') : '';
        if (href && href.indexOf('#p') === 0) {{
            const entry = hlGroups.get(href) || {{zh: [], en: []}};
            entry[isEn ? 'en' : 'zh'].push(p);
            hlGroups.set(href, entry);
        }}
    }});
    hlGroups.forEach(function(entry) {{
        if (entry.zh.length && entry.en.length) {{
            entry.zh.forEach(function(p) {{ p.classList.add('has-hl-pair'); }});
            entry.en.forEach(function(p) {{ p.classList.add('has-hl-pair'); }});
        }}
    }});
    syncReportLangUi(currentReportLang());

    const topicCounts = {{}};
    const links = new Map();
    papers.forEach(function(item, i) {{
        item.querySelectorAll('.status-tag').forEach(function(tag) {{
            const norm = normalizeStatusLabel(tag.textContent);
            if (norm) tag.textContent = norm;
        }});
        if (!item.id) item.id = 'p' + (i + 1);
        item.dataset.searchText = normalizeText(item.textContent);
        item.dataset.status = paperStatus(item);
        item.dataset.method = paperMethod(item);
        const rawTopics = (item.dataset.topics || '').split(',').map(function(s) {{ return s.trim(); }}).filter(Boolean);
        rawTopics.forEach(function(slug) {{
            topicCounts[slug] = (topicCounts[slug] || 0) + 1;
        }});
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

    let wantedTopic = 'all';
    const topicButtons = new Map();

    function syncTopicChipUi() {{
        topicButtons.forEach(function(chipBtn, slug) {{
            chipBtn.classList.toggle('is-active', slug === wantedTopic);
        }});
    }}

    if (chipsBar && Object.keys(topicCounts).length > 0) {{
        const allBtn = document.createElement('button');
        allBtn.type = 'button';
        allBtn.className = 'topic-chip is-active';
        allBtn.dataset.topic = 'all';
        allBtn.innerHTML = '<span>All</span><span class="chip-count">' + papers.length + '</span>';
        allBtn.addEventListener('click', function() {{
            wantedTopic = 'all';
            syncTopicChipUi();
            applyFilters();
        }});
        chipsBar.appendChild(allBtn);
        topicButtons.set('all', allBtn);

        TOPIC_CATALOG.forEach(function(spec) {{
            const cnt = topicCounts[spec.slug] || 0;
            if (!cnt) return;
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'topic-chip';
            chip.dataset.topic = spec.slug;
            chip.title = spec.label;
            chip.innerHTML = '<span>' + spec.short + '</span><span class="chip-count">' + cnt + '</span>';
            chip.addEventListener('click', function() {{
                wantedTopic = wantedTopic === spec.slug ? 'all' : spec.slug;
                syncTopicChipUi();
                applyFilters();
            }});
            chipsBar.appendChild(chip);
            topicButtons.set(spec.slug, chip);
        }});

        document.addEventListener('click', function(e) {{
            const pill = e.target.closest ? e.target.closest('.card-topic-pill') : null;
            if (!pill || !pill.dataset.topic) return;
            wantedTopic = wantedTopic === pill.dataset.topic ? 'all' : pill.dataset.topic;
            syncTopicChipUi();
            applyFilters();
        }});
    }}
    document.addEventListener('click', function(e) {{
        const objTag = e.target.closest ? e.target.closest('.obj-tag') : null;
        if (objTag && window.__ARXIV_STATIC_SITE__) {{
            const q = objTag.dataset.obj || objTag.textContent.replace(/^⌖\\s*/, '').trim();
            if (q) {{
                e.preventDefault();
                window.top.location.href = 'index.html#search/' + encodeURIComponent(q);
            }}
        }}
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
            const itemTopics = item.dataset.topics ? item.dataset.topics.split(',') : [];
            const okTopic = wantedTopic === 'all' || itemTopics.indexOf(wantedTopic) !== -1;
            const show = okSearch && okStatus && okMethod && okTopic;
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
        wantedTopic = 'all';
        syncTopicChipUi();
        applyFilters();
    }});
    applyFilters();

    document.querySelectorAll('.index-box').forEach(function(box) {{
        box.hidden = true;
    }});

    const EN_LABELS = {{
        '研究问题：': 'Research Question',
        '研究方法：': 'Method',
        '研究结果：': 'Results',
        '潜在不足与延伸方向：': 'Limitations & Next Steps',
    }};

    papers.forEach(function(item) {{
        const h3 = item.querySelector('h3');
        if (!h3) return;
        let pZh = null;
        let pEn = null;
        let pAuth = null;
        const zhFieldMap = new Map();
        const enFieldMap = new Map();
        Array.from(item.querySelectorAll(':scope > p')).forEach(function(p) {{
            const st = p.querySelector('strong');
            if (!st) return;
            const lbl = st.textContent.trim();
            if (lbl === '中文标题：') pZh = p;
            else if (lbl === '英文标题：') pEn = p;
            else if (lbl === '作者：') pAuth = p;
            else if (EN_LABELS[lbl]) {{
                st.setAttribute('data-en-label', EN_LABELS[lbl]);
                zhFieldMap.set(lbl, p);
            }} else {{
                const cleanEn = lbl.replace(/[:：]\\s*$/, '');
                if (cleanEn === 'Research Question' || cleanEn === 'Method' || cleanEn === 'Results'
                    || cleanEn === 'Limitations & Next Steps' || cleanEn === 'Limitations and Next Steps') {{
                    p.classList.add('p-field-en');
                    const key = cleanEn === 'Limitations and Next Steps' ? 'Limitations & Next Steps' : cleanEn;
                    enFieldMap.set(key, p);
                }}
            }}
        }});
        if (pZh && pEn) item.classList.add('has-title-pair');
        const ordered = [pZh, pEn, pAuth].filter(Boolean);
        let anchor = h3;
        ordered.forEach(function(p) {{
            const st = p.querySelector('strong');
            if (st) st.classList.add('sr-only-label');
            if (p === pZh) p.classList.add('p-title-zh');
            else if (p === pEn) p.classList.add('p-title-en');
            else if (p === pAuth) p.classList.add('p-authors');
            anchor.insertAdjacentElement('afterend', p);
            anchor = p;
        }});
        const mTag = item.querySelector('.method-tag');
        Object.keys(EN_LABELS).forEach(function(zhKey) {{
            const zhP = zhFieldMap.get(zhKey);
            const enP = enFieldMap.get(EN_LABELS[zhKey]);
            if (zhP && enP) {{
                zhP.classList.add('has-en-pair');
                enP.classList.add('has-zh-pair');
                zhP.insertAdjacentElement('afterend', enP);
            }}
            if (zhKey === '研究方法：' && enP && mTag && !(enP.querySelector && enP.querySelector('.method-tag-en'))) {{
                const enStrong = enP.querySelector('strong');
                const tagCopy = document.createElement('span');
                tagCopy.className = 'method-tag method-tag-en';
                tagCopy.textContent = mTag.textContent.trim();
                if (enStrong) {{
                    enStrong.insertAdjacentElement('afterend', tagCopy);
                }} else {{
                    enP.appendChild(tagCopy);
                }}
            }}
        }});
    }});
}}

initPaperStateActions();
initReportTools();
if (typeof window !== 'undefined' && window.location && window.location.hash) {{
    focusPaperHash(window.location.hash);
}}
</script>
</body>
</html>"""
    return html_layout


def save_html(
    papers: list[dict],
    report: str,
    provider: str,
    as_of: datetime.datetime | None = None,
) -> str:
    """Wrap the LLM-generated body in a styled HTML shell and write it to disk.

    Also stores the raw fragment under ``REPORTS_DIR/fragments/<date>.html``,
    runs fallback completion for any missing paper cards, writes
    ``starred.html``, and synchronizes the SQLite corpus when real arXiv IDs
    are present.

    Args:
        papers: Original paper list for completeness checks and corpus syncing.
        report: HTML body fragment produced by ``generate_report``.
        provider: Provider slug shown in the report header.
        as_of: Reference timestamp for the filename and header date.

    Returns:
        The path the file was written to.
    """
    date_str = (as_of or datetime.datetime.now()).strftime('%Y-%m-%d')
    body_content, warnings = finalize_fragment(report, papers, normalize_method_tags)
    for warning in warnings:
        print(f'⚠️ {warning}')

    os.makedirs(REPORTS_DIR, exist_ok=True)
    save_fragment(date_str, body_content, provider, reports_dir=REPORTS_DIR)

    html_layout = render_report_html(
        date_str=date_str,
        paper_count=len(papers),
        provider=provider,
        body_content=body_content,
    )
    filename = f'{REPORTS_DIR}/arXiv_astro_ph_HE_daily_report_{date_str}.html'
    with open(filename, 'w', encoding='utf-8-sig') as f:
        f.write(html_layout)
    _write_starred_html(provider, as_of=as_of)

    if any(bare_arxiv_id(p.get('url', '')) for p in papers):
        with contextlib.suppress(Exception):
            from core import corpus

            custom_db = (
                os.path.join(REPORTS_DIR, '.data', 'papers.db')
                if os.path.normpath(REPORTS_DIR) != 'reports'
                else None
            )
            corpus.sync_daily_report(date_str, papers, body_content, db_path=custom_db)

    print(f'✨ Sync version of daily report (Index + Details) generated: {filename}')
    return filename


def rerender_report(
    date_str: str,
    reports_dir: str | None = None,
    db_path: str | None = None,
) -> str:
    """Re-render a single daily report HTML from its stored fragment without calling LLM.

    Args:
        date_str: Target date formatted as ``YYYY-MM-DD``.
        reports_dir: Root reports directory; defaults to ``REPORTS_DIR``.
        db_path: Optional SQLite database path override for citation lookup.

    Returns:
        Path to the rewritten report HTML file.

    Raises:
        FileNotFoundError: If neither fragment nor HTML report exists for ``date_str``.
        ValueError: If parsed card count mismatches the existing report HTML.
    """
    target_dir = reports_dir or REPORTS_DIR
    report_path = os.path.join(target_dir, f'arXiv_astro_ph_HE_daily_report_{date_str}.html')
    loaded = load_fragment(date_str, target_dir)
    if loaded is None:
        if not os.path.exists(report_path):
            raise FileNotFoundError(f'Missing report and fragment for {date_str}')
        with open(report_path, encoding='utf-8-sig') as f:
            existing_html = f.read()
        fragment_html, provider = extract_fragment_from_report_html(existing_html)
        save_fragment(date_str, fragment_html, provider, target_dir)
    else:
        fragment_html, provider = loaded

    new_count = len(iter_paper_item_spans(fragment_html))
    if os.path.exists(report_path):
        with open(report_path, encoding='utf-8-sig') as f:
            old_html = f.read()
        old_count = len(iter_paper_item_spans(old_html))
        if old_count and new_count != old_count:
            raise ValueError(
                f'Paper count mismatch while rerendering {date_str}: {old_count} -> {new_count}'
            )

    orig_dir = REPORTS_DIR
    try:
        globals()['REPORTS_DIR'] = target_dir
        html_layout = render_report_html(
            date_str=date_str,
            paper_count=new_count,
            provider=provider,
            body_content=fragment_html,
            db_path=db_path,
        )
    finally:
        globals()['REPORTS_DIR'] = orig_dir
    tmp_path = f'{report_path}.tmp'
    with open(tmp_path, 'w', encoding='utf-8-sig') as f:
        f.write(html_layout)
    os.replace(tmp_path, report_path)
    return report_path


def rerender_all_reports(
    reports_dir: str | None = None,
    db_path: str | None = None,
) -> int:
    """Re-render every existing daily report HTML and ``starred.html`` in place."""
    target_dir = reports_dir or REPORTS_DIR
    pattern = os.path.join(target_dir, 'arXiv_astro_ph_HE_daily_report_*.html')
    count = 0
    for path in sorted(glob.glob(pattern)):
        m = _REPORT_FILE_RE.search(os.path.basename(path))
        if not m:
            continue
        rerender_report(m.group(1), reports_dir=target_dir, db_path=db_path)
        count += 1
    orig_dir = REPORTS_DIR
    try:
        globals()['REPORTS_DIR'] = target_dir
        _write_starred_html('local')
    finally:
        globals()['REPORTS_DIR'] = orig_dir
    return count
