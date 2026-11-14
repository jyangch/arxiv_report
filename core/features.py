"""TF-IDF + structured metadata feature matrix cache for similarity and SVM."""

from contextlib import closing
import json
import os
import pickle
import re

from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from core.corpus import connect, default_db_path, ensure_bootstrapped
from core.objects import canonical_object_token
from core.topics import TOPIC_BY_SLUG

FEATURE_SCHEMA_VERSION = 2

_TEXT_TOKEN_PATTERN = r'(?u)\b[a-zA-Z][a-zA-Z0-9_+-]{1,}\b'
_ET_AL_RE = re.compile(r'\bet\s+al\.?', re.IGNORECASE)
_NAME_WORD_RE = re.compile(r'[A-Za-z]+')


def _author_tokens(authors_str: str) -> str:
    cleaned = _ET_AL_RE.sub('', authors_str or '')
    tokens: list[str] = []
    for raw_author in cleaned.split(','):
        words = _NAME_WORD_RE.findall(raw_author.strip())
        if not words:
            continue
        surname = words[-1].lower()
        initial = words[0][0].lower()
        if len(words) >= 2:
            tokens.append(f'author_{surname}_{initial}')
        else:
            tokens.append(f'author_{surname}')
    return ' '.join(tokens)


def _structured_tokens(row: dict) -> str:
    tokens: list[str] = []
    try:
        slugs = json.loads(row['topic_slugs_json'] or '[]')
    except json.JSONDecodeError:
        slugs = []
    for slug in slugs:
        if slug:
            tokens.append(f'topic:{slug}')

    method_tag = (row['method_tag'] or '').strip().lower()
    if method_tag:
        tokens.append(f'method:{method_tag}')

    for cat in (row['categories'] or '').split():
        cat_l = cat.strip().lower()
        if cat_l and cat_l != 'astro-ph.he':
            tokens.append(f'cat:{cat_l}')

    try:
        objs = json.loads(row['objects_json'] or '[]')
    except json.JSONDecodeError:
        objs = []
    for obj in objs:
        tok = canonical_object_token(obj)
        if tok:
            tokens.append(f'obj:{tok}')
    return ' '.join(tokens)


def _pretty_feature_name(raw_name: str, obj_display_map: dict[str, str]) -> str:
    if raw_name.startswith('author_'):
        parts = raw_name[len('author_') :].split('_')
        if len(parts) >= 2:
            return f'Author: {parts[0].title()} {parts[1].upper()}.'
        return f'Author: {parts[0].title()}'
    if raw_name.startswith('topic:'):
        slug = raw_name[len('topic:') :]
        spec = TOPIC_BY_SLUG.get(slug)
        return f'Topic: {spec.short if spec else slug}'
    if raw_name.startswith('method:'):
        return f'Method: {raw_name[len("method:") :].title()}'
    if raw_name.startswith('cat:'):
        return f'Cat: {raw_name[len("cat:") :]}'
    if raw_name.startswith('obj:'):
        tok = raw_name[len('obj:') :]
        return obj_display_map.get(tok, tok)
    return raw_name


def get_feature_index(
    db_path: str | None = None,
    cache_path: str | None = None,
    force_rebuild: bool = False,
) -> dict:
    """Build or load the normalized sparse TF-IDF + metadata feature matrix.

    Args:
        db_path: Optional override path for the SQLite corpus file.
        cache_path: Optional override path for ``features.pkl``.
        force_rebuild: Bypass cache check and recompute features from SQLite.

    Returns:
        Dict with keys ``fingerprint``, ``pids``, ``pid_to_idx``, ``matrix``,
        and ``feature_names``.
    """
    ensure_bootstrapped(db_path=db_path)
    resolved_db = db_path or default_db_path()
    resolved_cache = cache_path or os.path.join(os.path.dirname(resolved_db) or '.', 'features.pkl')

    with closing(connect(resolved_db)) as conn:
        fp_row = conn.execute(
            'SELECT COUNT(*) AS cnt, COALESCE(MAX(updated_ts), 0.0) AS max_ts FROM papers'
        ).fetchone()
        fingerprint = (
            int(fp_row['cnt'] or 0),
            round(float(fp_row['max_ts'] or 0.0), 3),
            FEATURE_SCHEMA_VERSION,
        )
        if not force_rebuild and os.path.exists(resolved_cache):
            try:
                with open(resolved_cache, 'rb') as f:
                    cached = pickle.load(f)
                if isinstance(cached, dict) and cached.get('fingerprint') == fingerprint:
                    return cached
            except (OSError, pickle.UnpicklingError, EOFError, AttributeError):
                pass

        rows = conn.execute(
            """
            SELECT pid, title, title_zh, authors, summary, topic_slugs_json,
                   objects_json, method_tag, categories
            FROM papers
            ORDER BY pid
            """
        ).fetchall()

    pids = [r['pid'] for r in rows]
    pid_to_idx = {pid: i for i, pid in enumerate(pids)}
    n_docs = len(rows)
    if n_docs == 0:
        from scipy.sparse import csr_matrix

        empty_index = {
            'fingerprint': fingerprint,
            'pids': [],
            'pid_to_idx': {},
            'matrix': csr_matrix((0, 0)),
            'feature_names': [],
        }
        return empty_index

    obj_display_map: dict[str, str] = {}
    texts: list[str] = []
    author_docs: list[str] = []
    struct_docs: list[str] = []
    for r in rows:
        title = r['title'] or r['title_zh'] or ''
        summary = r['summary'] or ''
        texts.append(f'{title} {title} {summary}')
        author_docs.append(_author_tokens(r['authors'] or ''))
        struct_docs.append(_structured_tokens(dict(r)))
        try:
            for obj in json.loads(r['objects_json'] or '[]'):
                tok = canonical_object_token(obj)
                if tok and tok not in obj_display_map:
                    obj_display_map[tok] = obj
        except json.JSONDecodeError:
            pass

    min_df = 2 if n_docs >= 10 else 1
    max_df = 0.65 if n_docs >= 10 else 1.0

    blocks = []
    raw_names: list[str] = []

    text_vec = TfidfVectorizer(
        ngram_range=(1, 2),
        max_features=30_000,
        min_df=min_df,
        max_df=max_df,
        stop_words='english',
        sublinear_tf=True,
        token_pattern=_TEXT_TOKEN_PATTERN,
    )
    try:
        x_text = text_vec.fit_transform(texts)
        if x_text.shape[1] > 0:
            blocks.append(x_text)
            raw_names.extend(text_vec.get_feature_names_out().tolist())
    except ValueError:
        pass

    if any(doc.strip() for doc in author_docs):
        auth_vec = TfidfVectorizer(
            ngram_range=(1, 1),
            max_features=8_000,
            min_df=1,
            sublinear_tf=True,
            token_pattern=r'author_[a-z0-9_]+',
        )
        try:
            x_auth = auth_vec.fit_transform(author_docs) * 0.35
            if x_auth.shape[1] > 0:
                blocks.append(x_auth)
                raw_names.extend(auth_vec.get_feature_names_out().tolist())
        except ValueError:
            pass

    if any(doc.strip() for doc in struct_docs):
        struct_vec = TfidfVectorizer(
            ngram_range=(1, 1),
            min_df=1,
            sublinear_tf=False,
            lowercase=False,
            token_pattern=r'\S+',
        )
        try:
            x_struct = struct_vec.fit_transform(struct_docs) * 0.55
            if x_struct.shape[1] > 0:
                blocks.append(x_struct)
                raw_names.extend(struct_vec.get_feature_names_out().tolist())
        except ValueError:
            pass

    if blocks:
        combined = hstack(blocks).tocsr()
        matrix = normalize(combined, norm='l2', copy=False).tocsr()
    else:
        from scipy.sparse import csr_matrix

        matrix = csr_matrix((n_docs, 0))

    pretty_names = [_pretty_feature_name(name, obj_display_map) for name in raw_names]
    index = {
        'fingerprint': fingerprint,
        'pids': pids,
        'pid_to_idx': pid_to_idx,
        'matrix': matrix,
        'feature_names': pretty_names,
    }

    os.makedirs(os.path.dirname(resolved_cache) or '.', exist_ok=True)
    tmp_path = f'{resolved_cache}.tmp'
    with open(tmp_path, 'wb') as f:
        pickle.dump(index, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp_path, resolved_cache)
    return index
