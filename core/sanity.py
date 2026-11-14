"""Bilingual corpus search, similar-paper retrieval, and SVM recommendations."""

from contextlib import closing
import datetime
import json
import math
import re
import time

import numpy as np
from sklearn.svm import LinearSVC

from core import obsidian, paper_state
from core.citations import get_citation_map, scix_abs_url
from core.config import OBSIDIAN_ARXIV_FOLDER, OBSIDIAN_VAULT_PATH
from core.corpus import connect, ensure_bootstrapped
from core.features import get_feature_index
from core.fragments import bare_arxiv_id
from core.objects import canonical_object_token, extract_objects
from core.pub_status import normalize_status_label
from core.topics import TOPIC_BY_SLUG, resolve_topic

_QUOTED_RE = re.compile(r'"([^"]+)"')


def _parse_json_list(raw: str | None) -> list[str]:
    try:
        val = json.loads(raw or '[]')
        return [str(x) for x in val] if isinstance(val, list) else []
    except json.JSONDecodeError:
        return []


def _col_str(row, key: str) -> str:
    try:
        return str(row[key] or '')
    except (KeyError, IndexError):
        return ''


def _row_to_card(row: dict, cite_info: dict | None = None) -> dict:
    slugs = _parse_json_list(row['topic_slugs_json'])
    topics = _parse_json_list(row['topics_json'])
    objects = _parse_json_list(row['objects_json'])
    topic_chips = []
    for slug in slugs:
        spec = TOPIC_BY_SLUG.get(slug)
        if spec:
            topic_chips.append(
                {
                    'slug': spec.slug,
                    'short': spec.short,
                    'label': spec.label,
                    'label_en': spec.label_en,
                }
            )
    disp_id = row['arxiv_id'] or row['pid']
    report_date = row['report_date'] or ''
    has_report = bool(row['has_report'])
    number = int(row['number'] or 0)
    report_href = f'/r/{report_date}#p{number}' if has_report and report_date and number else ''
    return {
        'pid': row['pid'],
        'arxiv_id': disp_id,
        'title': row['title'] or '',
        'title_zh': row['title_zh'] or '',
        'authors': row['authors'] or '',
        'summary': row['summary'] or '',
        'question_zh': row['question_zh'] or '',
        'question_en': _col_str(row, 'question_en'),
        'method_zh': row['method_zh'] or '',
        'method_en': _col_str(row, 'method_en'),
        'method_tag': row['method_tag'] or '',
        'result_zh': row['result_zh'] or '',
        'result_en': _col_str(row, 'result_en'),
        'caveat_zh': row['caveat_zh'] or '',
        'caveat_en': _col_str(row, 'caveat_en'),
        'topics': topics,
        'topic_slugs': slugs,
        'topic_chips': topic_chips,
        'objects': objects,
        'categories': row['categories'] or '',
        'pub_status': row['pub_status'] or 'submitted',
        'status_label': normalize_status_label(row['status_label'] or ''),
        'is_highlight': bool(row['is_highlight']),
        'highlight_zh': row['highlight_zh'] or '',
        'highlight_en': _col_str(row, 'highlight_en'),
        'url': row['url'] or f'https://arxiv.org/abs/{disp_id}',
        'pdf_url': row['pdf_url'] or f'https://arxiv.org/pdf/{disp_id}',
        'report_date': report_date,
        'number': number,
        'has_report': has_report,
        'report_href': report_href,
        'scix_url': cite_info['scix_url'] if cite_info else scix_abs_url(row['pid'], ''),
        'citation_count': cite_info['citation_count'] if cite_info else None,
        'read_count': cite_info['read_count'] if cite_info else 0,
        'bibcode': cite_info['bibcode'] if cite_info else '',
        'cite_source': cite_info['source'] if cite_info else '',
    }


def enrich_cards_with_citations(cards: list[dict], db_path: str | None = None) -> list[dict]:
    """Attach cached citation metadata and SciX URLs to rendered card dicts in place."""
    pids = [c['pid'] for c in cards if c.get('pid')]
    if not pids:
        return cards
    cite_map = get_citation_map(pids, db_path=db_path)
    if not cite_map:
        return cards
    for card in cards:
        pid = card.get('pid') or ''
        info = cite_map.get(pid)
        if not info:
            continue
        card['scix_url'] = info['scix_url']
        card['citation_count'] = info['citation_count']
        card['read_count'] = info['read_count']
        card['bibcode'] = info['bibcode']
        card['cite_source'] = info['source']
    return cards


def _cutoff_date_str(conn, days: int) -> str:
    if days <= 0:
        return ''
    row = conn.execute('SELECT MAX(NULLIF(report_date, "")) AS max_d FROM papers').fetchone()
    anchor_str = row['max_d'] if row and row['max_d'] else ''
    try:
        anchor = datetime.date.fromisoformat(anchor_str) if anchor_str else datetime.date.today()
    except ValueError:
        anchor = datetime.date.today()
    return (anchor - datetime.timedelta(days=days)).isoformat()


def parse_search_query(query: str) -> list[str]:
    """Tokenize a bilingual query string while preserving phrases and object names."""
    q = (query or '').strip()
    if not q:
        return []
    tokens: list[str] = []
    for phrase in _QUOTED_RE.findall(q):
        cleaned = phrase.strip()
        if cleaned:
            tokens.append(cleaned)
    remainder = _QUOTED_RE.sub(' ', q)

    for obj in extract_objects(remainder):
        tokens.append(obj)
        remainder = re.sub(re.escape(obj), ' ', remainder, flags=re.IGNORECASE)

    for tok in remainder.split():
        cleaned = tok.strip()
        if cleaned and cleaned.lower() not in {t.lower() for t in tokens}:
            tokens.append(cleaned)
    return tokens


def _count_hits(haystack_lower: str, needle_lower: str, cap: int = 3) -> int:
    if not needle_lower or needle_lower not in haystack_lower:
        return 0
    return min(haystack_lower.count(needle_lower), cap)


def _score_row_against_query(
    row: dict,
    tokens: list[str],
    full_phrase_lower: str,
    anchor_date: datetime.date,
) -> tuple[int, float]:
    pid_l = (row['pid'] or '').lower()
    arxiv_l = (row['arxiv_id'] or '').lower()
    title_l = (row['title'] or '').lower()
    title_zh_l = (row['title_zh'] or '').lower()
    authors_l = (row['authors'] or '').lower()
    question_l = ' '.join(
        s for s in (row['question_zh'] or '', _col_str(row, 'question_en')) if s
    ).lower()
    method_l = ' '.join(
        s for s in (row['method_zh'] or '', _col_str(row, 'method_en')) if s
    ).lower()
    result_l = ' '.join(
        s for s in (row['result_zh'] or '', _col_str(row, 'result_en')) if s
    ).lower()
    caveat_l = ' '.join(
        s for s in (row['caveat_zh'] or '', _col_str(row, 'caveat_en')) if s
    ).lower()
    summary_l = (row['summary'] or '').lower()
    highlight_l = ' '.join(
        s for s in (row['highlight_zh'] or '', _col_str(row, 'highlight_en')) if s
    ).lower()
    topics_l = (row['topics_json'] or '').lower()
    objs = _parse_json_list(row['objects_json'])
    obj_canon_set = {canonical_object_token(o) for o in objs}
    obj_str_l = ' '.join(o.lower() for o in objs)

    matched_tokens = 0
    score = 0.0
    for tok in tokens:
        tok_l = tok.lower()
        tok_canon = canonical_object_token(tok)
        tok_score = 0.0

        if tok_l in (pid_l, arxiv_l) or (
            tok_l.startswith('arxiv:') and tok_l[6:] in (pid_l, arxiv_l)
        ):
            tok_score += 100.0
        if tok_canon and tok_canon in obj_canon_set:
            tok_score += 45.0
        elif tok_l in obj_str_l:
            tok_score += 25.0

        if tok_l in title_l or tok_l in title_zh_l:
            tok_score += 20.0
        if tok_l in authors_l:
            tok_score += 14.0
        if tok_l in topics_l:
            tok_score += 12.0
        if tok_l in highlight_l:
            tok_score += 10.0

        q_hits = _count_hits(question_l, tok_l)
        r_hits = _count_hits(result_l, tok_l)
        tok_score += 6.0 * (q_hits + r_hits)

        body_hits = (
            _count_hits(summary_l, tok_l)
            + _count_hits(method_l, tok_l)
            + _count_hits(caveat_l, tok_l)
        )
        tok_score += 2.5 * body_hits

        if tok_score > 0:
            matched_tokens += 1
            score += tok_score

    if matched_tokens == 0:
        return 0, 0.0

    if (
        full_phrase_lower
        and len(tokens) > 1
        and (
            full_phrase_lower in title_l
            or full_phrase_lower in title_zh_l
            or full_phrase_lower in result_l
        )
    ):
        score += 12.0

    try:
        row_d = datetime.date.fromisoformat(row['report_date'] or '')
        age_days = max(0, (anchor_date - row_d).days)
    except ValueError:
        age_days = 365
    score += 0.02 * max(0, 180 - age_days)
    return matched_tokens, score


def search_papers(
    query: str = '',
    *,
    days: int = 0,
    topic: str = '',
    method: str = '',
    status: str = '',
    sort: str = 'relevance',
    limit: int = 100,
    db_path: str | None = None,
) -> dict:
    """Search the corpus using bilingual weighted matching and structured filters.

    Args:
        query: Search string across English/Chinese titles, authors, objects,
            and digest/abstract fields.
        days: Restrict to papers within the last ``N`` days relative to the
            latest report date (``0`` disables the window filter).
        topic: Topic slug, short name, or Chinese label filter.
        method: Method tag filter (e.g., ``Observation``, ``Theory``).
        status: Publication status filter (``published``, ``accepted``, ``submitted``).
        sort: ``relevance`` or ``date``.
        limit: Maximum number of results returned in ``items``.
        db_path: Optional override path for SQLite corpus.

    Returns:
        Dict with ``query``, ``tokens``, ``total``, ``elapsed_ms``, and ``items``.
    """
    t0 = time.perf_counter()
    ensure_bootstrapped(db_path=db_path)

    resolved_spec = resolve_topic(topic) if topic and topic != 'all' else None
    topic_slug = (
        resolved_spec.slug if resolved_spec else (topic.strip() if topic and topic != 'all' else '')
    )

    with closing(connect(db_path)) as conn:
        cutoff = _cutoff_date_str(conn, days)
        where = ['1=1']
        params: list = []
        if cutoff:
            where.append('report_date >= ?')
            params.append(cutoff)
        if method and method != 'all':
            where.append('LOWER(method_tag) = LOWER(?)')
            params.append(method.strip())
        if status and status != 'all':
            where.append('LOWER(pub_status) = LOWER(?)')
            params.append(status.strip())
        sql = f"""
            SELECT * FROM papers
            WHERE {' AND '.join(where)}
            ORDER BY report_date DESC, has_report DESC, number ASC, pid DESC
        """
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    if topic_slug:
        rows = [
            r
            for r in rows
            if topic_slug in _parse_json_list(r['topic_slugs_json'])
            or (resolved_spec and resolved_spec.label in _parse_json_list(r['topics_json']))
        ]

    tokens = parse_search_query(query)
    if not tokens:
        if sort == 'citations':
            cite_map = get_citation_map([r['pid'] for r in rows], db_path=db_path)
            rows.sort(
                key=lambda r: (
                    cite_map.get(r['pid'], {}).get('citation_count', -1),
                    int(r.get('has_report') or 0),
                    r.get('report_date') or '',
                ),
                reverse=True,
            )
        cards = enrich_cards_with_citations(
            [_row_to_card(r) for r in rows[:limit]], db_path=db_path
        )
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            'query': query,
            'tokens': [],
            'total': len(rows),
            'elapsed_ms': elapsed_ms,
            'items': cards,
        }

    try:
        anchor_date = max(
            datetime.date.fromisoformat(r['report_date']) for r in rows if r.get('report_date')
        )
    except ValueError:
        anchor_date = datetime.date.today()

    full_phrase_l = query.strip().lower()
    scored_strict: list[tuple[float, dict]] = []
    scored_partial: list[tuple[int, float, dict]] = []
    min_partial = max(1, math.ceil(0.6 * len(tokens)))

    for r in rows:
        matched_cnt, score = _score_row_against_query(r, tokens, full_phrase_l, anchor_date)
        if matched_cnt == len(tokens):
            scored_strict.append((score, r))
        elif matched_cnt >= min_partial:
            scored_partial.append((matched_cnt, score, r))

    if scored_strict:
        winners = scored_strict
    else:
        winners = [(score + matched_cnt * 15.0, r) for matched_cnt, score, r in scored_partial]

    if sort == 'citations':
        cite_map = get_citation_map([pair[1]['pid'] for pair in winners], db_path=db_path)
        winners.sort(
            key=lambda pair: (
                cite_map.get(pair[1]['pid'], {}).get('citation_count', -1),
                pair[0],
                pair[1]['report_date'],
            ),
            reverse=True,
        )
    elif sort == 'date':
        winners.sort(key=lambda pair: (pair[1]['report_date'], pair[0]), reverse=True)
    else:
        winners.sort(key=lambda pair: (pair[0], pair[1]['report_date']), reverse=True)

    cards = []
    for score, r in winners[:limit]:
        card = _row_to_card(r)
        card['score'] = round(score, 1)
        cards.append(card)
    enrich_cards_with_citations(cards, db_path=db_path)

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    return {
        'query': query,
        'tokens': tokens,
        'total': len(winners),
        'elapsed_ms': elapsed_ms,
        'items': cards,
    }


def similar_papers(
    pid: str,
    *,
    days: int = 0,
    limit: int = 40,
    db_path: str | None = None,
    cache_path: str | None = None,
) -> dict:
    """Find the most similar papers in the corpus using cosine similarity.

    Args:
        pid: Seed paper arXiv identifier or URL (version optional).
        days: Optional window cutoff in days relative to the corpus max date.
        limit: Maximum number of similar papers to return.
        db_path: Optional SQLite corpus path override.
        cache_path: Optional ``features.pkl`` path override.

    Returns:
        Dict with ``seed`` card (or ``None`` if absent), ``items``, and
        ``elapsed_ms``.
    """
    t0 = time.perf_counter()
    clean_pid = bare_arxiv_id(pid)
    if not clean_pid:
        return {'seed': None, 'items': [], 'elapsed_ms': 0.0}

    index = get_feature_index(db_path=db_path, cache_path=cache_path)
    pid_to_idx = index['pid_to_idx']
    if clean_pid not in pid_to_idx:
        return {
            'seed': None,
            'items': [],
            'elapsed_ms': round((time.perf_counter() - t0) * 1000, 1),
        }

    matrix = index['matrix']
    pids = index['pids']
    feature_names = index['feature_names']
    seed_idx = pid_to_idx[clean_pid]
    seed_vec = matrix[seed_idx]
    sims = (matrix @ seed_vec.T).toarray().ravel()

    with closing(connect(db_path)) as conn:
        cutoff = _cutoff_date_str(conn, days)
        seed_row = conn.execute('SELECT * FROM papers WHERE pid = ?', (clean_pid,)).fetchone()
        date_rows = conn.execute('SELECT pid, report_date FROM papers').fetchall()
        date_by_pid = {r['pid']: r['report_date'] for r in date_rows}

    seed_card = _row_to_card(dict(seed_row)) if seed_row else None

    ranked_idxs = np.argsort(-sims)
    candidate_idxs: list[int] = []
    for idx in ranked_idxs:
        if int(idx) == seed_idx:
            continue
        sim = float(sims[idx])
        if sim <= 0.01:
            break
        cand_pid = pids[idx]
        if cutoff and (date_by_pid.get(cand_pid, '') < cutoff):
            continue
        candidate_idxs.append(int(idx))
        if len(candidate_idxs) >= limit:
            break

    items: list[dict] = []
    if candidate_idxs:
        target_pids = [pids[i] for i in candidate_idxs]
        placeholders = ','.join('?' for _ in target_pids)
        with closing(connect(db_path)) as conn:
            rows = conn.execute(
                f'SELECT * FROM papers WHERE pid IN ({placeholders})',
                target_pids,
            ).fetchall()
        row_map = {r['pid']: dict(r) for r in rows}

        for idx in candidate_idxs:
            cand_pid = pids[idx]
            row = row_map.get(cand_pid)
            if not row:
                continue
            card = _row_to_card(row)
            card['score'] = round(float(sims[idx]), 4)
            prod = seed_vec.multiply(matrix[idx])
            if prod.nnz > 0:
                top_order = np.argsort(-prod.data)[:4]
                card['shared_terms'] = [feature_names[int(prod.indices[k])] for k in top_order]
            else:
                card['shared_terms'] = []
            items.append(card)

    to_enrich = ([seed_card] if seed_card else []) + items
    enrich_cards_with_citations(to_enrich, db_path=db_path)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    return {'seed': seed_card, 'items': items, 'elapsed_ms': elapsed_ms}


def collect_owner_positives(
    source: str = 'all',
    state_path: str | None = None,
    vault_path: str | None = None,
    folder_name: str | None = None,
) -> dict:
    """Gather the owner's positive arXiv IDs from Starred state and Obsidian vault.

    Args:
        source: One of ``'all'``, ``'starred'``, or ``'obsidian'``.
        state_path: Optional override for ``paper_state.json``.
        vault_path: Optional override for ``OBSIDIAN_VAULT_PATH``.
        folder_name: Optional override for ``OBSIDIAN_ARXIV_FOLDER``.

    Returns:
        Dict with ``selected`` list of bare arXiv IDs and source counts.
    """
    state = paper_state.load_state(state_path or paper_state.STATE_PATH)
    starred: set[str] = set()
    state_obsidian: set[str] = set()

    for key, val in state.items():
        if val != '1':
            continue
        base, _, kind = key.rpartition(':')
        pid = ''
        paper_raw = state.get(f'{base}:paper')
        if paper_raw:
            try:
                pid = bare_arxiv_id(json.loads(paper_raw).get('arxivId', ''))
            except json.JSONDecodeError:
                pid = ''
        if not pid:
            pid = bare_arxiv_id(base)
        if not pid:
            continue
        if kind == 'starred':
            starred.add(pid)
        elif kind in ('obsidian', 'crafted'):
            state_obsidian.add(pid)

    vault_ids = obsidian.list_saved_arxiv_ids(
        vault_path or OBSIDIAN_VAULT_PATH,
        folder_name or OBSIDIAN_ARXIV_FOLDER,
    )
    obsidian_all = state_obsidian | {bare_arxiv_id(x) for x in vault_ids if bare_arxiv_id(x)}
    combined = starred | obsidian_all

    src_norm = (source or 'all').strip().lower()
    if src_norm == 'starred':
        selected = starred
    elif src_norm == 'obsidian':
        selected = obsidian_all
    else:
        selected = combined

    return {
        'source': src_norm if src_norm in ('starred', 'obsidian') else 'all',
        'selected': sorted(selected),
        'starred_count': len(starred),
        'obsidian_count': len(obsidian_all),
        'combined_count': len(combined),
    }


def recommend_papers(
    positive_pids: list[str] | set[str] | None = None,
    *,
    source: str = 'all',
    days: int = 30,
    exclude_positives: bool = True,
    c_value: float = 0.05,
    limit: int = 60,
    db_path: str | None = None,
    cache_path: str | None = None,
) -> dict:
    """Rank papers by personalized interest using LinearSVC over positive examples.

    Falls back cleanly to cosine centroid scoring when only 1 positive paper
    matches the corpus. Attaches per-paper explainability: matched SVM/centroid
    features and the closest positive paper.

    Args:
        positive_pids: Explicit collection of arXiv IDs to treat as positives;
            if ``None``, uses ``collect_owner_positives(source=source)``.
        source: Positive source selector passed to ``collect_owner_positives``.
        days: Candidate window in days relative to the corpus max date (``0`` = all).
        exclude_positives: Whether to omit already-starred/saved papers from results.
        c_value: LinearSVC regularization strength ``C``.
        limit: Maximum number of recommended items to return.
        db_path: Optional SQLite corpus override.
        cache_path: Optional ``features.pkl`` override.

    Returns:
        Dict containing ``mode``, ``matched_positives``, ``total_positives``,
        ``top_positive_features``, ``top_negative_features``, ``elapsed_ms``,
        and ranked ``items``.
    """
    t0 = time.perf_counter()
    if positive_pids is None:
        pos_list = collect_owner_positives(source=source)['selected']
    else:
        pos_list = sorted({bare_arxiv_id(p) for p in positive_pids if bare_arxiv_id(p)})

    index = get_feature_index(db_path=db_path, cache_path=cache_path)
    pids = index['pids']
    pid_to_idx = index['pid_to_idx']
    matrix = index['matrix']
    feature_names = index['feature_names']
    n_docs = len(pids)

    pos_indices = [pid_to_idx[p] for p in pos_list if p in pid_to_idx]
    pos_idx_set = set(pos_indices)

    if not pos_indices or n_docs == 0 or matrix.shape[1] == 0:
        return {
            'mode': 'empty',
            'total_positives': len(pos_list),
            'matched_positives': 0,
            'top_positive_features': [],
            'top_negative_features': [],
            'elapsed_ms': round((time.perf_counter() - t0) * 1000, 1),
            'items': [],
        }

    with closing(connect(db_path)) as conn:
        cutoff = _cutoff_date_str(conn, days)
        meta_rows = conn.execute('SELECT pid, title, title_zh, report_date FROM papers').fetchall()
        date_by_pid = {r['pid']: r['report_date'] for r in meta_rows}
        title_en_by_pid = {r['pid']: (r['title'] or r['title_zh'] or r['pid']) for r in meta_rows}
        title_zh_by_pid = {r['pid']: (r['title_zh'] or '') for r in meta_rows}

    if len(pos_indices) == 1 or len(pos_indices) >= n_docs:
        mode = 'centroid'
        centroid = np.asarray(matrix[pos_indices].mean(axis=0)).ravel()
        norm = float(np.linalg.norm(centroid))
        if norm > 0:
            centroid = centroid / norm
        scores = matrix.dot(centroid)
        weights = centroid
    else:
        mode = 'svm'
        y = np.zeros(n_docs, dtype=np.int32)
        y[pos_indices] = 1
        clf = LinearSVC(
            class_weight='balanced',
            C=c_value,
            max_iter=10_000,
            random_state=42,
        )
        clf.fit(matrix, y)
        scores = clf.decision_function(matrix)
        weights = np.asarray(clf.coef_).ravel()

    pos_order = np.argsort(-weights)
    top_positive_features = [
        {'term': feature_names[int(k)], 'weight': round(float(weights[k]), 3)}
        for k in pos_order[:14]
        if weights[k] > 0
    ]
    neg_order = np.argsort(weights)
    top_negative_features = [
        {'term': feature_names[int(k)], 'weight': round(float(weights[k]), 3)}
        for k in neg_order[:8]
        if weights[k] < 0
    ]

    ranked_idxs = np.argsort(-scores)
    selected_idxs: list[int] = []
    for idx in ranked_idxs:
        idx_i = int(idx)
        if exclude_positives and idx_i in pos_idx_set:
            continue
        cand_pid = pids[idx_i]
        if cutoff and date_by_pid.get(cand_pid, '') < cutoff:
            continue
        selected_idxs.append(idx_i)
        if len(selected_idxs) >= limit:
            break

    items: list[dict] = []
    if selected_idxs:
        pos_submatrix = matrix[pos_indices]
        target_pids = [pids[i] for i in selected_idxs]
        placeholders = ','.join('?' for _ in target_pids)
        with closing(connect(db_path)) as conn:
            rows = conn.execute(
                f'SELECT * FROM papers WHERE pid IN ({placeholders})',
                target_pids,
            ).fetchall()
        row_map = {r['pid']: dict(r) for r in rows}

        for idx_i in selected_idxs:
            cand_pid = pids[idx_i]
            row = row_map.get(cand_pid)
            if not row:
                continue
            card = _row_to_card(row)
            card['score'] = round(float(scores[idx_i]), 3)
            card['is_positive'] = idx_i in pos_idx_set

            row_vec = matrix[idx_i]
            matched_terms: list[str] = []
            if row_vec.nnz > 0:
                contribs = row_vec.data * weights[row_vec.indices]
                pos_mask = contribs > 0
                if np.any(pos_mask):
                    best = np.argsort(-contribs)[:4]
                    for b in best:
                        if contribs[b] > 0:
                            matched_terms.append(feature_names[int(row_vec.indices[b])])
            card['matched_terms'] = matched_terms

            sims_to_pos = (pos_submatrix @ row_vec.T).toarray().ravel()
            best_pos_local = int(np.argmax(sims_to_pos))
            best_pos_sim = float(sims_to_pos[best_pos_local])
            best_pos_pid = pids[pos_indices[best_pos_local]]
            card['nearest_positive'] = (
                {
                    'pid': best_pos_pid,
                    'title': title_en_by_pid.get(best_pos_pid, best_pos_pid),
                    'title_zh': title_zh_by_pid.get(best_pos_pid, ''),
                    'similarity': round(best_pos_sim, 3),
                }
                if best_pos_sim > 0.02
                else None
            )
            items.append(card)

    enrich_cards_with_citations(items, db_path=db_path)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    return {
        'mode': mode,
        'total_positives': len(pos_list),
        'matched_positives': len(pos_indices),
        'top_positive_features': top_positive_features,
        'top_negative_features': top_negative_features,
        'elapsed_ms': elapsed_ms,
        'items': items,
    }
