"""Infer publication status (preprint / submitted / accepted / published) from arXiv metadata.

Detection priority (most deterministic first):
    1. journal_ref present  → published
    2. comment contains 'accepted' / 'in press'  → accepted
    3. comment contains 'published'  → published
    4. comment contains 'submitted'  → submitted
    5. doi present (no other status hint)  → accepted
    6. otherwise  → preprint (no badge displayed)
"""

import re


def _extract_journal_short(journal_ref: str) -> str:
    """Pull the journal abbreviation from a journal_ref like 'ApJ 950, 47 (2025)'."""
    if not journal_ref:
        return ''
    # Take the leading non-digit run as the journal name.
    # Handles 'Phys. Rev. D 109', 'A&A 678', 'Nature Astronomy 7', etc.
    match = re.match(r'^(.+?)\s+\d', journal_ref)
    if match:
        return match.group(1).strip()
    return journal_ref.split(',')[0].strip()


def _extract_journal_from_comment(comment: str) -> str:
    """Best-effort extraction of journal name following 'by/to/in' in a comment string."""
    if not comment:
        return ''
    m = re.search(
        r'(?:by|to|in)\s+([A-Z][\w\.\&\s]*?)(?=[,;\n]|\.\s|\s\d|\s+\(|$)',
        comment,
    )
    return m.group(1).strip().rstrip('.') if m else ''


def classify_pub_status(comment: str, journal_ref: str, doi: str) -> tuple[str, str]:
    """Return (status_class, label).

    status_class ∈ {'published', 'accepted', 'submitted', 'preprint'}
    label is the rendered Chinese tag (empty for preprint).
    """
    if journal_ref:
        journal = _extract_journal_short(journal_ref)
        return ('published', f'已发表 ({journal})' if journal else '已发表')

    cl = (comment or '').lower()
    journal_in_comment = _extract_journal_from_comment(comment)

    def _wrap(text: str) -> str:
        return f'{text} ({journal_in_comment})' if journal_in_comment else text

    if 'accepted' in cl or 'in press' in cl:
        return ('accepted', _wrap('已接收'))
    if 'published' in cl:
        return ('published', _wrap('已发表'))
    if 'submitted' in cl:
        return ('submitted', _wrap('已投稿'))
    if doi:
        return ('accepted', '已接收')
    return ('preprint', '')
