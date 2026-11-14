"""Deterministic astrophysical source designation extractor."""

import re

_PREFIX_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Gamma-ray bursts: GRB 221009A, GRB250419A
    (re.compile(r'\bGRB\s?(\d{6,7}[A-Za-z]?)\b'), 'GRB {}'),
    # Einstein Probe transients: EP240414a, EP 241021a, EP J...
    (re.compile(r'\b[Ee][Pp]\s?(\d{6}[a-zA-Z]?)\b'), 'EP{}'),
    (re.compile(r'\bEP\s+(J\d{4}(?:\.\d+)?[+-]\d{4})\b'), 'EP {}'),
    # Gravitational-wave events: GW170817, GW 230529_181500
    (re.compile(r'\bGW\s?(\d{6}(?:_\d{6})?)\b'), 'GW{}'),
    # Fast radio bursts: FRB 20201124A, FRB 121102
    (re.compile(r'\bFRB\s?(\d{6,8}[A-Za-z]?)\b'), 'FRB {}'),
    # Supernovae & AT transients: require trailing letter(s) so "at 2024" never matches
    (re.compile(r'\bSN\s?(\d{4}[a-zA-Z]{1,4})\b'), 'SN {}'),
    (re.compile(r'\bAT\s?(\d{4}[a-zA-Z]{1,4})\b'), 'AT {}'),
    # ZTF transients: ZTF24abxy...
    (re.compile(r'\bZTF\s?(\d{2}[a-z]{5,8})\b'), 'ZTF{}'),
    # IceCube neutrino alerts: IceCube-170922A
    (re.compile(r'\bIceCube-(\d{6}[A-Z]?)\b'), 'IceCube-{}'),
)

_CATALOG_RE = re.compile(
    r'\b(PSR|SGR|AXP|1E|4U|MAXI|Swift|IGR|SAX|XTE|EXO|HESS|LHAASO|1LHAASO|'
    r'HAWC|VER|MAGIC|4FGL|3FGL)\s+(J\d{4}(?:\.\d+)?[+-]\d{2,4}[A-Za-z]?|B\d{4}[+-]\d{2}[A-Za-z]?)\b'
)

_NAMED_RE = re.compile(
    r'(?<![A-Za-z0-9])('
    r'GX\s?\d{1,3}[+-]\d{1,2}|'
    r'GRS\s?1915\+105|'
    r'Cyg(?:nus)?\s?X-[123]|'
    r'Sco\s?X-1|Vela\s?X-1|Her\s?X-1|'
    r'LMC\s?X-[134]|SMC\s?X-[123]|'
    r'SS\s?433|V404\s?Cyg|'
    r'Mrk\s?\d{1,4}|NGC\s?\d{3,5}|3C\s?\d{2,4}(?:\.\d+)?|'
    r'PKS\s?\d{4}[+-]\d{2,4}|TXS\s?\d{4}[+-]\d{3,4}|'
    r'Sgr\s?A\*|M87\*?|Cen\s?A|Cas\s?A|Geminga'
    r')(?![A-Za-z0-9])'
)


def canonical_object_token(name: str) -> str:
    """Normalize a source designation into an ASCII identifier token.

    Args:
        name: Raw or formatted astronomical object name.

    Returns:
        Lowercase ASCII token suitable for feature indexing and deduplication.
    """
    cleaned = (name or '').strip().lower()
    cleaned = cleaned.replace('*', '_star').replace('+', 'p').replace('-', 'm')
    cleaned = re.sub(r'[^a-z0-9_.]+', '_', cleaned)
    return re.sub(r'_+', '_', cleaned).strip('_')


def _normalize_named_match(raw: str) -> str:
    compact = re.sub(r'\s+', ' ', raw).strip()
    compact = re.sub(r'^Cygnus\s+X-', 'Cyg X-', compact)
    compact = re.sub(r'^(GX|GRS|SS|Mrk|NGC|PKS|TXS|Cen|Cas|Sgr|Sco|Vela|Her)\s*', r'\1 ', compact)
    compact = re.sub(r'\s+', ' ', compact).strip()
    return compact.replace('Sgr A *', 'Sgr A*')


def extract_objects(text: str, max_items: int = 8) -> list[str]:
    """Extract unique astrophysical source designations from English/Chinese text.

    Args:
        text: Combined title, abstract, or digest text to scan.
        max_items: Maximum number of distinct designations to return.

    Returns:
        Ordered list of normalized object designation strings.
    """
    if not text:
        return []

    found: list[tuple[int, str]] = []
    for pattern, template in _PREFIX_RULES:
        for match in pattern.finditer(text):
            val = match.group(1)
            if template.startswith(('GRB ', 'FRB ', 'IceCube-')):
                val = val.upper()
            elif template.startswith(('SN ', 'AT ')):
                digits, letters = val[:4], val[4:]
                val = digits + (letters.upper() if len(letters) == 1 else letters.lower())
            found.append((match.start(), template.format(val)))

    for match in _CATALOG_RE.finditer(text):
        found.append((match.start(), f'{match.group(1)} {match.group(2)}'))

    for match in _NAMED_RE.finditer(text):
        found.append((match.start(), _normalize_named_match(match.group(1))))

    found.sort(key=lambda pair: pair[0])
    out: list[str] = []
    seen: set[str] = set()
    for _, name in found:
        tok = canonical_object_token(name)
        if not tok or tok in seen:
            continue
        seen.add(tok)
        out.append(name)
        if len(out) >= max_items:
            break
    return out
