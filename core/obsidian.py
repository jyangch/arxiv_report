"""Format and persist per-paper Markdown notes into the local Obsidian vault.

Notes are written as UTF-8 Markdown files named ``[<arxiv_id>] <title_zh>.md``
inside the configured vault folder. Existing notes matching ``[<arxiv_id>]``
are never overwritten; an optional personal note is appended instead.
"""

from collections.abc import Mapping
import contextlib
import os
import re
import tempfile
import threading
from typing import Any

_CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]+')
_WHITESPACE_RE = re.compile(r'\s+')
_LEADING_METHOD_TAG_RE = re.compile(r'^\s*\[([A-Za-z][A-Za-z0-9_-]*)\]\s*')
_SAVED_NOTE_RE = re.compile(r'^\[([^\]]+)\].*\.md$')
_FRONTMATTER_ARXIV_RE = re.compile(r'(?m)^arxiv_id:\s*"?([^"\s]+)"?\s*$')
_BODY_ARXIV_RE = re.compile(r'\[arXiv:([^\]\s]+)\]')
_MAX_FILENAME_BYTES = 255
_lock = threading.Lock()


def _sanitize_filename_part(value: str) -> str:
    cleaned = _CONTROL_CHARS_RE.sub('', value or '')
    cleaned = cleaned.replace('/', '／').replace('\\', '＼').replace(':', '：')
    cleaned = _WHITESPACE_RE.sub(' ', cleaned).strip().rstrip('.')
    return cleaned


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
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='.obsidian-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


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


def render_obsidian_markdown(payload: Mapping[str, Any]) -> str:
    """Render clean Markdown body for an Obsidian paper note.

    Args:
        payload: Mapping of scraped paper metadata and optional personal ``note``.

    Returns:
        Complete UTF-8 Markdown document text terminated with a newline.
    """
    arxiv_id = str(payload.get('arxiv_id') or '').strip()
    url = str(payload.get('url') or '').strip()
    title_en = str(payload.get('title_en') or '').strip() or '未记录'
    authors_raw = str(payload.get('authors') or '').strip()
    question = str(payload.get('question') or '').strip()
    method_raw = str(payload.get('method') or '').strip()
    method_tag_raw = str(payload.get('method_tag') or '').strip()
    result = str(payload.get('result') or '').strip()
    caveat = str(payload.get('caveat') or '').strip()
    note = str(payload.get('note') or '').strip()

    _, method_body = _extract_method_parts(method_raw, method_tag_raw)

    body_lines = [
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


def _extract_arxiv_id_from_file(file_path: str) -> str | None:
    try:
        with open(file_path, encoding='utf-8') as f:
            head = f.read(2048)
    except OSError:
        return None
    fm_match = _FRONTMATTER_ARXIV_RE.search(head)
    if fm_match:
        return fm_match.group(1).strip() or None
    body_match = _BODY_ARXIV_RE.search(head)
    if body_match:
        return body_match.group(1).strip() or None
    return None


def _find_existing_note(folder_path: str, clean_id: str, raw_id: str) -> str | None:
    prefix = f'[{clean_id}]'
    try:
        names = sorted(os.listdir(folder_path))
    except FileNotFoundError:
        return None
    for name in names:
        if name.startswith(prefix) and name.endswith('.md'):
            return name
    for name in names:
        if not name.endswith('.md') or _SAVED_NOTE_RE.match(name):
            continue
        found_id = _extract_arxiv_id_from_file(os.path.join(folder_path, name))
        if found_id and (found_id == raw_id or _sanitize_filename_part(found_id) == clean_id):
            return name
    return None


def save_obsidian_note(
    payload: Mapping[str, Any],
    *,
    vault_path: str,
    folder_name: str,
) -> dict[str, str]:
    """Write or safely append a paper note inside the Obsidian vault folder.

    If a ``[<arxiv_id>]*.md`` note (or a renamed ``.md`` note containing
    ``arxiv_id``) already exists in ``folder_name``, its body is never
    overwritten: an empty ``note`` leaves the file untouched
    (``status='exists'``), whereas a non-empty ``note`` appends a personal-note
    section at the end (``status='appended'``).

    Args:
        payload: Paper metadata mapping including ``arxiv_id`` and ``title_zh``.
        vault_path: Root path of the local Obsidian vault.
        folder_name: Subfolder inside ``vault_path`` where arXiv notes live.

    Returns:
        Dictionary with ``status``, ``filename``, ``path``, and ``markdown``.

    Raises:
        ValueError: If ``arxiv_id`` is missing or blank.
    """
    raw_id = str(payload.get('arxiv_id') or '').strip()
    clean_id = _sanitize_filename_part(raw_id)
    if not clean_id:
        raise ValueError('arxiv_id must not be empty')

    clean_vault = os.path.expanduser(vault_path or '').strip()
    if not clean_vault:
        raise ValueError('OBSIDIAN_VAULT_PATH is not configured')
    folder_path = os.path.join(clean_vault, folder_name)
    markdown = render_obsidian_markdown(payload)

    with _lock:
        os.makedirs(folder_path, exist_ok=True)
        existing_name = _find_existing_note(folder_path, clean_id, raw_id)
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
    """Return sorted arXiv IDs of all saved ``.md`` notes in the vault folder.

    Args:
        vault_path: Root path of the local Obsidian vault.
        folder_name: Subfolder inside ``vault_path`` where arXiv notes live.

    Returns:
        Sorted list of unique arXiv identifier strings found on disk.
    """
    clean_vault = os.path.expanduser(vault_path or '').strip()
    if not clean_vault:
        return []
    folder_path = os.path.join(clean_vault, folder_name)
    try:
        names = os.listdir(folder_path)
    except OSError:
        return []
    found: set[str] = set()
    for name in names:
        if not name.endswith('.md'):
            continue
        match = _SAVED_NOTE_RE.match(name)
        if match:
            arxiv_id = match.group(1).strip()
            if arxiv_id:
                found.add(arxiv_id)
            continue
        fallback_id = _extract_arxiv_id_from_file(os.path.join(folder_path, name))
        if fallback_id:
            found.add(fallback_id)
    return sorted(found)
