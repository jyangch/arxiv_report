"""Single-source topic taxonomy, prompt lines, classifier, and chip styles."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class TopicSpec:
    """Metadata and light/dark colors for one report topic category."""

    slug: str
    short: str
    label: str
    label_en: str
    light_fg: str
    light_bg: str
    dark_fg: str
    dark_bg: str


TOPICS: tuple[TopicSpec, ...] = (
    TopicSpec(
        'grb',
        'GRB',
        '伽马射线暴（GRB）',
        'Gamma-Ray Bursts (GRB)',
        '#b45309',
        '#fef3c7',
        '#fbbf24',
        'rgba(251, 191, 36, 0.16)',
    ),
    TopicSpec(
        'efxt',
        'FXT/EP',
        '快 X 射线暂现源（FXT / EP）',
        'Fast X-ray Transients (FXT / EP)',
        '#c2410c',
        '#ffedd5',
        '#fb923c',
        'rgba(251, 146, 60, 0.16)',
    ),
    TopicSpec(
        'frb',
        'FRB',
        '快速射电暴（FRB）',
        'Fast Radio Bursts (FRB)',
        '#be185d',
        '#fce7f3',
        '#f472b6',
        'rgba(244, 114, 182, 0.16)',
    ),
    TopicSpec(
        'sne_kn',
        'SNe/KN',
        '超新星与千新星（SNe / Kilonova / FBOT）',
        'Supernovae & Kilonovae (SNe / KN)',
        '#b91c1c',
        '#fee2e2',
        '#f87171',
        'rgba(248, 113, 113, 0.16)',
    ),
    TopicSpec(
        'tde_qpe',
        'TDE/QPE',
        '潮汐瓦解与核区暂现源（TDE / QPE）',
        'TDEs & Nuclear Transients (TDE / QPE)',
        '#9f1239',
        '#ffe4e6',
        '#fda4af',
        'rgba(253, 164, 175, 0.16)',
    ),
    TopicSpec(
        'ns_psr',
        'NS/PSR',
        '中子星、脉冲星与磁星（NS / Pulsar / Magnetar）',
        'Neutron Stars, Pulsars & Magnetars (NS / PSR)',
        '#0f766e',
        '#ccfbf1',
        '#5eead4',
        'rgba(94, 234, 212, 0.16)',
    ),
    TopicSpec(
        'xrb_wd',
        'XRB/WD',
        'X 射线双星、白矮星新星与恒星耀发（XRB / ULX / CV / Nova）',
        'X-ray Binaries, ULXs & Accreting WDs (XRB / WD)',
        '#0369a1',
        '#e0f2fe',
        '#7dd3fc',
        'rgba(125, 211, 252, 0.16)',
    ),
    TopicSpec(
        'gw',
        'GW',
        '致密双星并合与引力波（GW / Mergers）',
        'Gravitational Waves & Mergers (GW)',
        '#6d28d9',
        '#ede9fe',
        '#c4b5fd',
        'rgba(196, 181, 253, 0.16)',
    ),
    TopicSpec(
        'bh_gr',
        'BH/GR',
        '黑洞强场引力与视界物理（BH / Strong Gravity / EHT / Sgr A*）',
        'Black Hole Physics & Strong Gravity (BH / GR)',
        '#1d4ed8',
        '#dbeafe',
        '#93c5fd',
        'rgba(147, 197, 253, 0.16)',
    ),
    TopicSpec(
        'agn',
        'AGN',
        '活动星系核、耀变体与喷流（AGN / Blazar / Jets）',
        'AGN, Blazars & Relativistic Jets (AGN)',
        '#4338ca',
        '#e0e7ff',
        '#a5b4fc',
        'rgba(165, 180, 252, 0.16)',
    ),
    TopicSpec(
        'snr_icm',
        'SNR/ICM',
        '超新星遗迹、银盘扩散辐射与星系团（SNR / ISM / ICM）',
        'SNRs, Diffuse ISM & Galaxy Clusters (SNR / ICM)',
        '#4d7c0f',
        '#ecfccb',
        '#bef264',
        'rgba(190, 242, 100, 0.16)',
    ),
    TopicSpec(
        'vhe',
        'VHE',
        '甚高能与超高能伽马天文（VHE / PeVatron）',
        'TeV-PeV Gamma-Ray Astronomy (VHE)',
        '#047857',
        '#d1fae5',
        '#6ee7b7',
        'rgba(110, 231, 183, 0.16)',
    ),
    TopicSpec(
        'cr_nu',
        'CR/ν',
        '宇宙射线与高能中微子（CR / UHECR / HE ν）',
        'Cosmic Rays & High-Energy Neutrinos (CR / ν)',
        '#15803d',
        '#dcfce7',
        '#86efac',
        'rgba(134, 239, 172, 0.16)',
    ),
    TopicSpec(
        'dm_alp',
        'DM/ALP',
        '暗物质、轴子与天体粒子新物理（DM / ALP / PBH / New Physics）',
        'Dark Matter, Axions & New Physics (DM / ALP)',
        '#7e22ce',
        '#f3e8ff',
        '#d8b4fe',
        'rgba(216, 180, 254, 0.16)',
    ),
    TopicSpec(
        'plasma',
        'Plasma',
        '高能等离子体、激波与辐射机制（Plasma / MHD / Shocks / Radiation）',
        'High-Energy Plasma, Shocks & Radiation (Plasma)',
        '#0e7490',
        '#cffafe',
        '#67e8f9',
        'rgba(103, 232, 249, 0.16)',
    ),
    TopicSpec(
        'inst_ml',
        'Inst/ML',
        '高能仪器载荷、标定管线与统计机器学习（Instrumentation / Pipelines / ML）',
        'Instrumentation, Missions & Methods (Inst / ML)',
        '#475569',
        '#f1f5f9',
        '#cbd5e1',
        'rgba(203, 213, 225, 0.14)',
    ),
)

TOPIC_BY_SLUG: dict[str, TopicSpec] = {t.slug: t for t in TOPICS}
TOPIC_BY_LABEL: dict[str, TopicSpec] = {t.label: t for t in TOPICS}
_TOPIC_BY_SHORT: dict[str, TopicSpec] = {t.short.lower(): t for t in TOPICS}

# Maps legacy labels, slugs, and short names (lowercased) to an intermediate key
# so historical migration can distinguish SNe/TDE, BH/Jet, and Theory/Inst
# before collapse to canonical slugs.
LEGACY_RAW_TO_KEY: dict[str, str] = {
    '河外快 x 射线暂现源（efxt / fxt）': 'efxt',
    'extragalactic fast x-ray transients (efxt / fxt)': 'efxt',
    'efxt': 'efxt',
    'fxt': 'efxt',
    '超新星、千新星与瞬变源（sne / kilonova / tde / fbot）': 'sne_tde',
    'supernovae, kilonovae & transients (sne / tde)': 'sne_tde',
    'sne/tde': 'sne_tde',
    'sne_tde': 'sne_tde',
    '活动星系核与耀变体（agn / blazar / quasar）': 'agn',
    'active galactic nuclei & blazars (agn)': 'agn',
    '致密双星合并与引力波多信使（gw / compact mergers）': 'gw',
    'compact binary mergers & gravitational waves (gw)': 'gw',
    'x 射线双星与吸积物理（xrb / accretion / disk-jet）': 'xrb',
    'x-ray binaries & accretion (xrb)': 'xrb',
    'xrb': 'xrb',
    '黑洞与相对论喷流（bh / jets / smbh）': 'bh_jet',
    'black holes & relativistic jets (bh / jet)': 'bh_jet',
    'bh/jet': 'bh_jet',
    'bh_jet': 'bh_jet',
    '宇宙射线与高能中微子（uhecr / galactic cr / he ν）': 'cr_nu',
    '甚高能与超高能伽马射线天文（vhe / uhe γ-ray）': 'vhe',
    'vhe / uhe gamma-ray astronomy (vhe)': 'vhe',
    '超新星遗迹、星际介质与星系团（snr / ism / icm）': 'snr_ism',
    'supernova remnants, ism & galaxy clusters (snr / ism)': 'snr_ism',
    'snr/ism': 'snr_ism',
    'snr_ism': 'snr_ism',
    '理论、数值方法与仪器（theory / numerical / instrumentation）': 'theory_inst',
    'theory, numerical methods & instrumentation': 'theory_inst',
    'theory/inst': 'theory_inst',
    'theory_inst': 'theory_inst',
}

_LEGACY_TO_CANONICAL_SLUG: dict[str, str] = {
    'efxt': 'efxt',
    'sne_tde': 'sne_kn',
    'agn': 'agn',
    'gw': 'gw',
    'xrb': 'xrb_wd',
    'bh_jet': 'bh_gr',
    'cr_nu': 'cr_nu',
    'vhe': 'vhe',
    'snr_ism': 'snr_icm',
    'theory_inst': 'inst_ml',
}

_RX_INST_ONLY = re.compile(
    r'\b(calibration|database design|onboard catalog|inner-core x-ray optic|detector|fabrication|pipeline|simulator)\b',
    re.I,
)
_RX_FXT = re.compile(
    r'\b(fast x-ray transients?|efxts?|fxts?|ep\s*2[456]\d{4}[a-z]?|wxt\s*2[456]\d{4}|lfbots?)\b',
    re.I,
)
_RX_EP = re.compile(r'\b(einstein probe|ep-wxt|ep-fxt)\b', re.I)
_RX_TDE = re.compile(
    r'\b(tidal disruption|\btdes?\b|quasi-periodic eruptions?|\bqpes?\b|changing-look agn|nuclear transients?|gsn 069)\b',
    re.I,
)
_RX_WD = re.compile(
    r'\b(cataclysmic variables?|classical novae?|recurrent novae?|symbiotic stars?|am cvn|accreting white dwarfs?|intermediate polars?)\b',
    re.I,
)
_RX_TYPE_IA = re.compile(r'\b(type ia|thermonuclear supernova)\b', re.I)
_RX_DM_TITLE = re.compile(
    r'\b(dark matter|axions?|alps?\b|primordial black holes?|\bpbhs?\b|wimps?|dark photons?|lorentz invariance|lorentz violation|sterile neutrinos?|ultralight bosons?|boson stars?|cosmic strings?|fifth force)\b',
    re.I,
)
_RX_DM = re.compile(
    r'\b(dark matter|axions?|alps?\b|primordial black holes?|\bpbhs?\b|wimps?|dark photons?|lorentz invariance|lorentz violation|sterile neutrinos?|ultralight|sub-gev|fifth force|phase transitions?|cosmic strings?|birefringence|hawking radiation|millicharged)\b',
    re.I,
)
_RX_INST = re.compile(
    r'\b(detectors?|calibrat\w*|instrument\w*|polarimeters?|spectrometers?|scintillators?|bolometers?|x-ray optics?|onboard|cube-?sats?|data release|pipelines?|software|\bgpus?\b|machine learning|neural networks?|deep learning|normalizing flows?|simulation-based inference|gaussian random field|image subtraction|undersampled)\b',
    re.I,
)
_RX_PLASMA = re.compile(
    r'\b(magnetic reconnection|particle-in-cell|pic simulations?|collisionless shocks?|shock polars?|oblique shocks?|beam-plasma|weibel|kelvin-helmholtz|turbulence|magnetohydrodynamic|\bmhd\b|grmhd|radiative transfer|pair-plasma|streaming plasma)\b',
    re.I,
)
_RX_STRONG_GR = re.compile(
    r'\b(event horizon telescope|\beht\b|black hole shadow|photon ring|sgr a\*|sagittarius a\*|m87\*|kerr|schwarzschild|geodesics?|quasinormal|cauchy horizon|regular black holes?|scalarized|braneworld|wormholes?|hawking|evaporating black hole|intermediate-mass black holes?|\bimbhs?\b|loss cone|white hole)\b',
    re.I,
)
_RX_AGN = re.compile(
    r'\b(blazars?|quasars?|seyfert|fanaroff-riley|radio galax\w*|active galactic|\bagns?\b)\b',
    re.I,
)
_RX_XRB = re.compile(
    r'\b(x-ray binar\w*|microquasars?|cyg x-1|gx 339|maxi j|ultraluminous x-ray|\bulxs?\b)\b',
    re.I,
)


def classify_legacy_key(raw: str) -> str:
    """Return an intermediate legacy or canonical slug key from a raw topic string.

    Preserves legacy bucket keys such as ``sne_tde``, ``bh_jet``, and
    ``theory_inst`` so offline migration can split them using paper text.

    Args:
        raw: Topic label, slug, or short badge label.

    Returns:
        Matched legacy key or canonical topic slug, or empty string if unknown.
    """
    cleaned = (raw or '').strip().rstrip('：:').strip()
    if not cleaned:
        return ''
    lower = cleaned.lower()
    if lower in LEGACY_RAW_TO_KEY:
        return LEGACY_RAW_TO_KEY[lower]
    spec = resolve_topic(cleaned)
    return spec.slug if spec else ''


def resolve_topic(raw: str) -> TopicSpec | None:
    """Resolve a label, slug, or short abbreviation into a ``TopicSpec``.

    Args:
        raw: Topic label (Chinese or English, current or legacy), slug, or short name.

    Returns:
        Matching ``TopicSpec``, or ``None`` if unrecognized.
    """
    cleaned = (raw or '').strip().rstrip('：:').strip()
    if not cleaned:
        return None
    if cleaned in TOPIC_BY_LABEL:
        return TOPIC_BY_LABEL[cleaned]
    lower = cleaned.lower()
    if lower in TOPIC_BY_SLUG:
        return TOPIC_BY_SLUG[lower]
    if lower in _TOPIC_BY_SHORT:
        return _TOPIC_BY_SHORT[lower]
    if lower in LEGACY_RAW_TO_KEY:
        canonical = _LEGACY_TO_CANONICAL_SLUG.get(LEGACY_RAW_TO_KEY[lower], '')
        if canonical in TOPIC_BY_SLUG:
            return TOPIC_BY_SLUG[canonical]
    for spec in TOPICS:
        if spec.label.lower() == lower or spec.label_en.lower() == lower:
            return spec
    return None


def reclassify_paper_topics(
    raw_topics: list[str],
    title: str = '',
    summary: str = '',
    categories: str = '',
    method_tag: str = '',
    zh_text: str = '',
) -> list[str]:
    """Remap legacy/raw topic tags into 1-2 orthogonal canonical topic slugs.

    Args:
        raw_topics: Existing raw labels, legacy slugs, or canonical slugs for the paper.
        title: English title of the paper.
        summary: English abstract of the paper.
        categories: Whitespace-separated arXiv categories (e.g. ``astro-ph.HE hep-ph``).
        method_tag: Extracted method tag such as ``Observation`` or ``Theory``.
        zh_text: Combined Chinese digest text used as secondary disambiguation signal.

    Returns:
        List of 1-2 canonical topic slugs ordered by relevance.
    """
    legacy_keys: list[str] = []
    for raw in raw_topics:
        k = classify_legacy_key(raw)
        if k and k not in legacy_keys:
            legacy_keys.append(k)

    text = f'{title} {summary}'
    cats = set((categories or '').split())
    zh = zh_text or ''

    out: list[str] = []

    def add(slug: str) -> None:
        if slug in TOPIC_BY_SLUG and slug not in out:
            out.append(slug)

    if _RX_FXT.search(text) or (_RX_EP.search(text) and not _RX_INST_ONLY.search(title or '')):
        add('efxt')

    if _RX_DM_TITLE.search(title or ''):
        add('dm_alp')

    other_non_bh = set(legacy_keys) - {'bh_jet', 'bh_gr', 'theory_inst'}

    for key in legacy_keys:
        if key == 'sne_tde':
            if _RX_TDE.search(text) or '潮汐瓦解' in zh or '准周期爆发' in zh:
                add('tde_qpe')
            elif _RX_WD.search(text) and not _RX_TYPE_IA.search(text):
                add('xrb_wd')
            else:
                add('sne_kn')
        elif key == 'xrb':
            add('xrb_wd')
        elif key == 'snr_ism':
            add('snr_icm')
        elif key == 'theory_inst':
            matched_ti = False
            if _RX_DM.search(text) or ('hep-ph' in cats and 'cr_nu' not in legacy_keys):
                add('dm_alp')
                matched_ti = True
            if (
                'astro-ph.IM' in cats
                or _RX_INST.search(title or '')
                or (_RX_INST.search(text) and not _RX_PLASMA.search(title or ''))
            ):
                add('inst_ml')
                matched_ti = True
            if 'physics.plasm-ph' in cats or _RX_PLASMA.search(text):
                add('plasma')
                matched_ti = True
            if not matched_ti:
                add('inst_ml' if method_tag == 'Observation' else 'plasma')
        elif key == 'bh_jet':
            has_host = bool(other_non_bh & {'agn', 'xrb', 'xrb_wd', 'gw', 'grb'}) or (
                'tde_qpe' in out
            )
            if has_host:
                if _RX_STRONG_GR.search(text):
                    add('bh_gr')
            else:
                if _RX_TDE.search(text):
                    add('tde_qpe')
                elif _RX_AGN.search(text):
                    add('agn')
                elif _RX_XRB.search(text):
                    add('xrb_wd')
                else:
                    add('bh_gr')
        else:
            add(key)

    if not out:
        if _RX_DM.search(text):
            add('dm_alp')
        elif _RX_TDE.search(text):
            add('tde_qpe')
        elif _RX_AGN.search(text):
            add('agn')
        elif _RX_XRB.search(text) or _RX_WD.search(text):
            add('xrb_wd')
        elif 'gr-qc' in cats:
            add('gw' if 'gravitational wave' in text.lower() else 'bh_gr')
        elif 'astro-ph.IM' in cats or _RX_INST.search(text):
            add('inst_ml')
        else:
            add('plasma')

    return out[:2]


def prompt_topic_lines() -> str:
    """Format numbered topic lines embedded into the LLM prompt template."""
    return '\n'.join(f'    {i}. {t.label}' for i, t in enumerate(TOPICS, start=1))


def topic_chip_css() -> str:
    """Generate CSS rules for topic filter chips and per-card topic pills."""
    light_rules = []
    dark_rules = []
    media_dark_rules = []
    for t in TOPICS:
        light_rules.append(
            f'.topic-chip[data-topic="{t.slug}"], .card-topic-pill[data-topic="{t.slug}"] {{ '
            f'--chip-fg: {t.light_fg}; --chip-bg: {t.light_bg}; }}'
        )
        dark_rules.append(
            f':root[data-theme="dark"] .topic-chip[data-topic="{t.slug}"], '
            f':root[data-theme="dark"] .card-topic-pill[data-topic="{t.slug}"] {{ '
            f'--chip-fg: {t.dark_fg}; --chip-bg: {t.dark_bg}; }}'
        )
        media_dark_rules.append(
            f'    :root:not([data-theme="light"]) .topic-chip[data-topic="{t.slug}"], '
            f':root:not([data-theme="light"]) .card-topic-pill[data-topic="{t.slug}"] {{ '
            f'--chip-fg: {t.dark_fg}; --chip-bg: {t.dark_bg}; }}'
        )
    return (
        '.topic-chips-bar {\n'
        '    display: flex;\n'
        '    flex-wrap: wrap;\n'
        '    gap: 6px;\n'
        '    padding-top: 9px;\n'
        '}\n'
        '.topic-chips-bar:empty { display: none; }\n'
        '.topic-chip, .card-topic-pill {\n'
        '    --chip-fg: var(--text-muted);\n'
        '    --chip-bg: var(--surface-alt);\n'
        '    display: inline-flex;\n'
        '    align-items: center;\n'
        '    gap: 4px;\n'
        '    padding: 2px 9px;\n'
        '    border: 1px solid color-mix(in srgb, var(--chip-fg) 22%, var(--border));\n'
        '    border-radius: 999px;\n'
        '    background: var(--chip-bg);\n'
        '    color: var(--chip-fg);\n'
        '    font: inherit;\n'
        '    font-size: 0.74em;\n'
        '    font-weight: 600;\n'
        '    line-height: 1.4;\n'
        '    cursor: pointer;\n'
        '    transition: transform 0.12s, box-shadow 0.12s, border-color 0.12s;\n'
        '}\n'
        '.topic-chip:hover, .card-topic-pill:hover {\n'
        '    border-color: var(--chip-fg);\n'
        '}\n'
        '.topic-chip.is-active {\n'
        '    background: var(--chip-fg);\n'
        '    color: #ffffff;\n'
        '    border-color: var(--chip-fg);\n'
        '}\n'
        '.topic-chip[data-topic="all"].is-active {\n'
        '    background: var(--primary);\n'
        '    color: #ffffff;\n'
        '    border-color: var(--primary);\n'
        '}\n'
        '.topic-chip .chip-count {\n'
        '    opacity: 0.78;\n'
        '    font-weight: 500;\n'
        '}\n'
        '.paper-tags {\n'
        '    display: flex;\n'
        '    flex-wrap: wrap;\n'
        '    align-items: center;\n'
        '    gap: 6px;\n'
        '    margin-top: 10px;\n'
        '    padding-top: 8px;\n'
        '    border-top: 1px dashed var(--border);\n'
        '}\n'
        '.obj-tag {\n'
        '    display: inline-flex;\n'
        '    align-items: center;\n'
        '    padding: 2px 8px;\n'
        '    border: 1px solid var(--border);\n'
        '    border-radius: 999px;\n'
        '    background: var(--surface-alt);\n'
        '    color: var(--text-muted);\n'
        '    font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;\n'
        '    font-size: 0.72em;\n'
        '    font-weight: 600;\n'
        '    text-decoration: none;\n'
        '}\n'
        '.obj-tag:hover {\n'
        '    border-color: var(--primary);\n'
        '    color: var(--primary);\n'
        '    background: var(--primary-soft);\n'
        '    text-decoration: none;\n'
        '}\n'
        '.scix-tag {\n'
        '    display: inline-flex;\n'
        '    align-items: center;\n'
        '    padding: 2px 8px;\n'
        '    border: 1px solid var(--border);\n'
        '    border-radius: 999px;\n'
        '    background: var(--surface-alt);\n'
        '    color: var(--text-muted);\n'
        '    font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;\n'
        '    font-size: 0.72em;\n'
        '    font-weight: 600;\n'
        '    text-decoration: none;\n'
        '}\n'
        '.scix-tag:hover {\n'
        '    border-color: var(--primary);\n'
        '    color: var(--primary);\n'
        '    background: var(--primary-soft);\n'
        '    text-decoration: none;\n'
        '}\n'
        '.scix-tag.is-cited {\n'
        '    background: #dbeafe;\n'
        '    color: #1858a8;\n'
        '    border-color: #bfdbfe;\n'
        '}\n'
        + '\n'.join(light_rules)
        + '\n:root[data-theme="dark"] .topic-chip.is-active,'
        + ' :root[data-theme="dark"] .topic-chip[data-topic="all"].is-active { color: #0d1014; }\n'
        + ':root[data-theme="dark"] .scix-tag.is-cited { background: rgba(104, 166, 248, 0.16); color: #93c5fd; border-color: rgba(104, 166, 248, 0.35); }\n'
        + '\n'.join(dark_rules)
        + '\n@media (prefers-color-scheme: dark) {\n'
        + '    :root:not([data-theme="light"]) .topic-chip.is-active,'
        + ' :root:not([data-theme="light"]) .topic-chip[data-topic="all"].is-active { color: #0d1014; }\n'
        + '    :root:not([data-theme="light"]) .scix-tag.is-cited { background: rgba(104, 166, 248, 0.16); color: #93c5fd; border-color: rgba(104, 166, 248, 0.35); }\n'
        + '\n'.join(media_dark_rules)
        + '\n}'
    )
