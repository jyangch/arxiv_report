"""Provider keys, model defaults, and dispatch order.

All values come from environment variables; defaults are baked in for offline runs
(though API keys must be supplied — empty key disables the provider).
"""

import os

PREFERRED_PROVIDER = 'claude'  # 'claude' | 'gemini' | 'openai'
FALLBACK_ORDER = ('claude', 'gemini', 'openai')

CLAUDE_BACKEND = os.getenv('CLAUDE_BACKEND', 'cli')

CLAUDE_API_KEY = os.getenv('CLAUDE_API_KEY', '')
CLAUDE_MODEL = os.getenv('CLAUDE_MODEL', 'claude-opus-4-6')

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.1-flash-lite-preview')

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-5.4')
