"""Server-side store for per-paper starred / crafted state.

Report pages keep their state in browser ``localStorage`` under keys like
``arxiv-report:<date>:<arxiv id>:starred``. ``static/paper_state.js`` mirrors
those keys here so the collection survives cleared browser data. Only the
``starred`` / ``crafted`` flags are kept, plus the ``paper`` snapshot of any
paper that still carries a flag (the starred list renders from it).
"""

import json
import os
import threading

from core.render import REPORTS_DIR

STATE_PATH = os.path.join(REPORTS_DIR, 'paper_state.json')

KEY_PREFIX = 'arxiv-report:'
FLAG_KINDS = ('starred', 'crafted')
MAX_VALUE_CHARS = 64_000

_lock = threading.Lock()


def _split_key(key: str) -> tuple[str, str] | None:
    """Return ``(base, kind)`` for a valid state key, else None."""
    if not isinstance(key, str) or not key.startswith(KEY_PREFIX):
        return None
    base, sep, kind = key.rpartition(':')
    if not sep or base == KEY_PREFIX.rstrip(':'):
        return None
    if kind not in (*FLAG_KINDS, 'paper'):
        return None
    return base, kind


def _is_flagged(state: dict[str, str], base: str) -> bool:
    return any(state.get(f'{base}:{kind}') == '1' for kind in FLAG_KINDS)


def load_state(path: str = STATE_PATH) -> dict[str, str]:
    """Read the stored key -> value map; a missing or corrupt file reads as empty."""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and _split_key(k)}


def _save_state(state: dict[str, str], path: str) -> None:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=4, sort_keys=True)
    os.replace(tmp, path)


def apply_changes(
    set_items: dict[str, str], remove_keys: list[str], path: str = STATE_PATH
) -> dict[str, str]:
    """Apply one batch of writes and return the resulting state.

    Flags are applied before snapshots so a batch that stars a paper and
    sends its snapshot together keeps both. Invalid keys are ignored, and
    snapshots of papers with no remaining flag are dropped.
    """
    with _lock:
        state = load_state(path)
        for key in remove_keys:
            if _split_key(key):
                state.pop(key, None)
        papers = {}
        for key, value in set_items.items():
            parts = _split_key(key)
            if not parts or not isinstance(value, str) or len(value) > MAX_VALUE_CHARS:
                continue
            if parts[1] == 'paper':
                papers[key] = value
            elif value == '1':
                state[key] = value
            else:
                state.pop(key, None)
        for key, value in papers.items():
            if _is_flagged(state, key.rpartition(':')[0]):
                state[key] = value
        state = {
            k: v
            for k, v in state.items()
            if not k.endswith(':paper') or _is_flagged(state, k.rpartition(':')[0])
        }
        _save_state(state, path)

    return state
