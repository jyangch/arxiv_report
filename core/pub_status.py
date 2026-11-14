"""Infer publication status (preprint / submitted / accepted / published) from arXiv metadata."""

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


_STATUS_WORD_EN = {
    '已发表': 'Published',
    '已接收': 'Accepted',
    '已投稿': 'Submitted',
}


def normalize_status_label(label: str) -> str:
    """Normalize legacy Chinese status labels into English equivalents.

    Args:
        label: Raw badge string containing a legacy non-English status marker.

    Returns:
        Normalized English badge label, or ``''`` when input is empty.
    """
    raw = (label or '').strip()
    if not raw:
        return ''
    for zh, en in _STATUS_WORD_EN.items():
        if raw == zh:
            return en
        m_prefix = re.match(rf'^{zh}\s*\((.+)\)$', raw)
        if m_prefix:
            journal = m_prefix.group(1).strip()
            return f'{en} ({journal})' if journal else en
        m_suffix = re.match(rf'^(.+?)\s+{zh}$', raw)
        if m_suffix:
            journal = m_suffix.group(1).strip()
            return f'{en} ({journal})' if journal else en
        if raw.startswith(zh + ' '):
            rest = raw[len(zh) :].strip()
            return f'{en} ({rest})' if rest else en
    return raw


def classify_pub_status(comment: str, journal_ref: str, doi: str) -> tuple[str, str]:
    """Infer publication status from arXiv metadata fields.

    Detection priority (most deterministic first):
        1. ``journal_ref`` present -> published
        2. ``comment`` contains 'accepted' / 'in press' -> accepted
        3. ``comment`` contains 'published' -> published
        4. ``comment`` contains 'submitted' -> submitted
        5. ``doi`` present with no other status hint -> accepted
        6. otherwise -> preprint (no badge displayed)

    Args:
        comment: The arXiv submission comment field, possibly empty.
        journal_ref: The arXiv ``journal_ref`` field, possibly empty.
        doi: The arXiv ``doi`` field, possibly empty.

    Returns:
        A pair ``(status_class, label)`` where ``status_class`` is one of
        ``'published'``, ``'accepted'``, ``'submitted'``, ``'preprint'`` and
        ``label`` is the rendered English tag (empty for preprint).
    """
    if journal_ref:
        journal = _extract_journal_short(journal_ref)
        return ('published', f'Published ({journal})' if journal else 'Published')

    cl = (comment or '').lower()
    journal_in_comment = _extract_journal_from_comment(comment)

    def _wrap(text: str) -> str:
        return f'{text} ({journal_in_comment})' if journal_in_comment else text

    if 'accepted' in cl or 'in press' in cl:
        return ('accepted', _wrap('Accepted'))
    if 'published' in cl:
        return ('published', _wrap('Published'))
    if 'submitted' in cl:
        return ('submitted', _wrap('Submitted'))
    if doi:
        return ('accepted', 'Accepted')
    return ('preprint', '')
