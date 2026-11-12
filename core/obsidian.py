"""Format and persist per-paper Markdown notes into the local Obsidian vault.

Notes are written as UTF-8 Markdown files named ``[<arxiv_id>] <title_zh>.md``
inside the configured vault folder. Existing notes matching ``[<arxiv_id>]``
are never overwritten; an optional personal note is appended instead.
"""

from collections.abc import Mapping
import datetime
import os
import re
from typing import Any

_CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]+')
_WHITESPACE_RE = re.compile(r'\s+')
_LEADING_METHOD_TAG_RE = re.compile(r'^\s*\[([A-Za-z][A-Za-z0-9_-]*)\]\s*')
_PLAIN_YAML_SCALAR_RE = re.compile(r'^[A-Za-z0-9._\u0080-\uffff][A-Za-z0-9 ._\u0080-\uffff-]*$')
_ISO_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_SAVED_NOTE_RE = re.compile(r'^\[([^\]]+)\].*\.md$')
_YAML_RESERVED = frozenset({'true', 'false', 'null', 'yes', 'no', 'on', 'off', '~'})
_MAX_FILENAME_BYTES = 255


def _sanitize_filename_part(value: str) -> str:
    cleaned = _CONTROL_CHARS_RE.sub('', value or '')
    cleaned = cleaned.replace('/', '／').replace('\\', '＼').replace(':', '：')
    cleaned = _WHITESPACE_RE.sub(' ', cleaned).strip().rstrip('.')
    return cleaned


def _yaml_scalar(value: str, *, force_quote: bool = False) -> str:
    text = (value or '').strip()
    if (
        not force_quote
        and text
        and text.lower() not in _YAML_RESERVED
        and (
            _ISO_DATE_RE.match(text)
            or (_PLAIN_YAML_SCALAR_RE.match(text) and not text.endswith(' '))
        )
    ):
        return text
    escaped = (
        text.replace('\\', '\\\\')
        .replace('"', '\\"')
        .replace('\n', '\\n')
        .replace('\r', '\\r')
        .replace('\t', '\\t')
    )
    return f'"{escaped}"'


def _extract_method_parts(method: str, method_tag: str) -> tuple[str, str]:
    raw_method = (method or '').strip()
    raw_tag = (method_tag or '').strip().strip('[]').strip()
    match = _LEADING_METHOD_TAG_RE.match(raw_method)
    if match:
        extracted_tag = match.group(1)
        rest = raw_method[match.end() :].strip()
        # Strip accidental repeated leading tag such as "[Theory] [Theory] ..."
        dup = _LEADING_METHOD_TAG_RE.match(rest)
        if dup and dup.group(1).lower() == extracted_tag.lower():
            rest = rest[dup.end() :].strip()
        final_tag = raw_tag or extracted_tag
        body_method = f'[{final_tag}] {rest}'.strip() if rest else f'[{final_tag}]'
        return final_tag, body_method
    if raw_tag and raw_method:
        return raw_tag, f'[{raw_tag}] {raw_method}'
    return raw_tag, raw_method


def _atomic_write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp_path = f'{path}.tmp.{os.getpid()}'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(tmp_path, path)


def note_filename(arxiv_id: str, title_zh: str) -> str:
    """Build a safe APFS filename ``[<arxiv_id>] <title_zh>.md`` within 255 UTF-8 bytes.

    Args:
        arxiv_id: Paper identifier such as ``2608.03043v1``.
        title_zh: Chinese paper title used in the note filename.

    Returns:
        Sanitized filename ending in ``.md`` whose UTF-8 byte length is at most 255.

    Raises:
        ValueError: If ``arxiv_id`` is empty after sanitization.
    """
    clean_id = _sanitize_filename_part(arxiv_id)
    if not clean_id:
        raise ValueError('arxiv_id must not be empty')
    clean_title = _sanitize_filename_part(title_zh) or '未命名论文'
    prefix = f'[{clean_id}] '
    suffix = '.md'
    budget = _MAX_FILENAME_BYTES - len(prefix.encode('utf-8')) - len(suffix.encode('utf-8'))
    if budget <= 0:
        raise ValueError('arxiv_id is too long for a valid filename')
    while clean_title and len(clean_title.encode('utf-8')) > budget:
        clean_title = clean_title[:-1].rstrip().rstrip('.')
    clean_title = clean_title or 'paper'
    return f'{prefix}{clean_title}{suffix}'


def render_obsidian_markdown(payload: Mapping[str, Any], *, created_date: str | None = None) -> str:
    """Render YAML frontmatter and Markdown body for an Obsidian paper note.

    Args:
        payload: Mapping of scraped paper metadata and optional personal ``note``.
        created_date: Optional ``YYYY-MM-DD`` override for deterministic testing.

    Returns:
        Complete UTF-8 Markdown document text terminated with a newline.
    """
    arxiv_id = str(payload.get('arxiv_id') or '').strip()
    url = str(payload.get('url') or '').strip()
    title_zh = str(payload.get('title_zh') or '').strip() or '未记录'
    title_en = str(payload.get('title_en') or '').strip() or '未记录'
    authors_raw = str(payload.get('authors') or '').strip()
    question = str(payload.get('question') or '').strip()
    method_raw = str(payload.get('method') or '').strip()
    method_tag_raw = str(payload.get('method_tag') or '').strip()
    result = str(payload.get('result') or '').strip()
    caveat = str(payload.get('caveat') or '').strip()
    paper_status = str(payload.get('paper_status') or '').strip() or 'submitted'
    report_date = str(payload.get('report_date') or '').strip()
    note = str(payload.get('note') or '').strip()
    created = (created_date or datetime.date.today().isoformat()).strip()

    method_tag, method_body = _extract_method_parts(method_raw, method_tag_raw)
    author_items = [item.strip() for item in authors_raw.split(',') if item.strip()]

    yaml_lines = [
        '---',
        f'arxiv_id: {_yaml_scalar(arxiv_id, force_quote=True)}',
        f'url: {_yaml_scalar(url, force_quote=True)}',
    ]
    if author_items:
        yaml_lines.append('authors:')
        for author in author_items:
            yaml_lines.append(f'  - {_yaml_scalar(author)}')
    else:
        yaml_lines.append('authors: []')
    if method_tag:
        yaml_lines.append(f'method: {_yaml_scalar(method_tag)}')
    yaml_lines.append(f'paper_status: {_yaml_scalar(paper_status)}')
    if report_date:
        yaml_lines.append(f'report_date: {_yaml_scalar(report_date)}')
    yaml_lines.append(f'created: {_yaml_scalar(created)}')
    yaml_lines.append('---')

    body_lines = [
        '\n'.join(yaml_lines),
        '',
        f'# {title_zh}',
        '',
        title_en,
        '',
        f'[arXiv:{arxiv_id or "未记录"}]({url})',
        '',
        f'**作者：** {authors_raw or "未记录"}',
        '',
    ]

    analysis_lines: list[str] = []
    if question:
        analysis_lines.append(f'**研究问题：** {question}')
    if method_body:
        analysis_lines.append(f'**研究方法：** {method_body}')
    if result:
        analysis_lines.append(f'**研究结果：** {result}')
    if caveat:
        analysis_lines.append(f'**潜在不足与延伸方向：** {caveat}')
    if analysis_lines:
        body_lines.append('\n'.join(analysis_lines))

    if note:
        body_lines.extend(['', '---', '', f'**个人备注：** {note}'])

    return '\n'.join(body_lines).rstrip() + '\n'


def _find_existing_note(folder_path: str, clean_id: str) -> str | None:
    prefix = f'[{clean_id}]'
    try:
        names = sorted(os.listdir(folder_path))
    except FileNotFoundError:
        return None
    for name in names:
        if name.startswith(prefix) and name.endswith('.md'):
            return name
    return None


def save_obsidian_note(
    payload: Mapping[str, Any],
    *,
    vault_path: str,
    folder_name: str,
    created_date: str | None = None,
) -> dict[str, str]:
    """Write or safely append a paper note inside the Obsidian vault folder.

    If a ``[<arxiv_id>]*.md`` note already exists in ``folder_name``, its body
    is never overwritten: an empty ``note`` leaves the file untouched
    (``status='exists'``), whereas a non-empty ``note`` appends a personal-note
    section at the end (``status='appended'``).

    Args:
        payload: Paper metadata mapping including ``arxiv_id`` and ``title_zh``.
        vault_path: Root path of the local Obsidian vault.
        folder_name: Subfolder inside ``vault_path`` where arXiv notes live.
        created_date: Optional ``YYYY-MM-DD`` override for the ``created`` field.

    Returns:
        Dictionary with ``status``, ``filename``, ``path``, and ``markdown``.

    Raises:
        ValueError: If ``arxiv_id`` is missing or blank.
    """
    raw_id = str(payload.get('arxiv_id') or '').strip()
    clean_id = _sanitize_filename_part(raw_id)
    if not clean_id:
        raise ValueError('arxiv_id must not be empty')

    folder_path = os.path.join(vault_path, folder_name)
    os.makedirs(folder_path, exist_ok=True)
    markdown = render_obsidian_markdown(payload, created_date=created_date)

    existing_name = _find_existing_note(folder_path, clean_id)
    if existing_name is not None:
        existing_path = os.path.join(folder_path, existing_name)
        note_text = str(payload.get('note') or '').strip()
        if note_text:
            with open(existing_path, encoding='utf-8') as f:
                current = f.read().rstrip()
            updated = f'{current}\n\n---\n\n**个人备注：** {note_text}\n'
            _atomic_write_text(existing_path, updated)
            return {
                'status': 'appended',
                'filename': existing_name,
                'path': existing_path,
                'markdown': markdown,
            }
        return {
            'status': 'exists',
            'filename': existing_name,
            'path': existing_path,
            'markdown': markdown,
        }

    filename = note_filename(clean_id, str(payload.get('title_zh') or ''))
    target_path = os.path.join(folder_path, filename)
    _atomic_write_text(target_path, markdown)
    return {
        'status': 'created',
        'filename': filename,
        'path': target_path,
        'markdown': markdown,
    }


def list_saved_arxiv_ids(vault_path: str, folder_name: str) -> list[str]:
    """Return sorted arXiv IDs of all ``[<arxiv_id>]*.md`` files in the vault folder.

    Args:
        vault_path: Root path of the local Obsidian vault.
        folder_name: Subfolder inside ``vault_path`` where arXiv notes live.

    Returns:
        Sorted list of unique arXiv identifier strings found on disk.
    """
    folder_path = os.path.join(vault_path, folder_name)
    try:
        names = os.listdir(folder_path)
    except OSError:
        return []
    found: set[str] = set()
    for name in names:
        match = _SAVED_NOTE_RE.match(name)
        if match:
            arxiv_id = match.group(1).strip()
            if arxiv_id:
                found.add(arxiv_id)
    return sorted(found)
