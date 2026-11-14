"""Provider keys, model defaults, and dispatch order.

All values come from environment variables; defaults are baked in for offline runs
(though API keys must be supplied -- an empty key disables the provider).
"""

import os

PREFERRED_PROVIDER = os.getenv('PREFERRED_PROVIDER', 'gemini')  # 'claude' | 'gemini' | 'openai'
FALLBACK_ORDER = tuple(
    p.strip() for p in os.getenv('FALLBACK_ORDER', 'gemini').split(',') if p.strip()
)

CLAUDE_BACKEND = os.getenv('CLAUDE_BACKEND', 'cli')
CLAUDE_API_KEY = os.getenv('CLAUDE_API_KEY', '')
CLAUDE_MODEL = os.getenv('CLAUDE_MODEL', 'claude-opus-4-6')

GEMINI_BACKEND = os.getenv('GEMINI_BACKEND', 'cli')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-4-hydrogen-high')
AGY_CLI = os.getenv('AGY_CLI', '')
MAX_PAPERS_PER_CHUNK = int(os.getenv('MAX_PAPERS_PER_CHUNK', '25'))

OPENAI_BACKEND = os.getenv('OPENAI_BACKEND', 'codex')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-5.5')
CODEX_CLI = os.getenv('CODEX_CLI', '')

OBSIDIAN_VAULT_PATH = os.getenv('OBSIDIAN_VAULT_PATH', '/Users/junyang/Documents/Obsidian/astro')
OBSIDIAN_ARXIV_FOLDER = os.getenv('OBSIDIAN_ARXIV_FOLDER', 'Arxiv-Notes')

ARXIV_DATA_DIR = os.getenv('ARXIV_DATA_DIR', './reports/.data')
ALLOWED_NETS = tuple(n.strip() for n in os.getenv('ALLOWED_NETS', '').split(',') if n.strip())
UI_TOKEN = os.getenv('UI_TOKEN', '')
SCIX_API_URL = os.getenv('SCIX_API_URL', 'https://api.adsabs.harvard.edu/v1/search/query')
SCIX_API_TOKEN = os.getenv('SCIX_API_TOKEN', '')
SITE_OUTPUT_DIR = os.getenv('SITE_OUTPUT_DIR', './dist/site')
SITE_TITLE = os.getenv('SITE_TITLE', 'Daily Digest')
