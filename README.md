# arXiv High-Energy Astrophysics Daily Report Generator

Automatically fetches the latest papers from the arXiv `astro-ph.HE` (High Energy Astrophysical Phenomena) category, uses a large language model to generate a structured academic digest, and exports it as an HTML report. Ships with a CLI for cron / one-off runs and a FastAPI + HTMX web UI for browsing past reports and triggering new ones interactively.

## Features

- Fetches newly submitted papers based on arXiv's submission sync window (US Eastern Time)
- Supports arbitrary historical dates via arXiv's `submittedDate` range query
- Classifies papers by research topic (GRBs, black holes, supernovae, cosmic rays, ...)
- Generates Chinese-language summaries with method tags and key physical findings
- Multi-provider LLM dispatch: Claude (CLI or API), Gemini, OpenAI -- auto-fallback on quota errors
- Self-contained styled HTML reports (light / dark adaptive, internal anchor scrolling)
- FastAPI + HTMX web UI: per-date URLs, sidebar history, live SSE progress
- Per-paper "Save to Craft" button: hands off a new Craft document (title, authors, method/results/caveats, optional personal note) via the `craftdocs://` URL scheme

## Project layout

```
.
├── server.py              FastAPI app, routes, background worker, SSE stream
├── report.py              CLI entry (cron / one-off)
├── core/                  domain package
│   ├── fetcher.py           arXiv API + RSS fallback
│   ├── providers.py         Claude / Gemini / OpenAI clients + fallback dispatcher
│   ├── prompt.py            prompt builder
│   ├── pub_status.py        publication-status classifier
│   ├── render.py            standalone HTML wrapper
│   └── config.py            env vars + defaults
├── templates/             Jinja2 base + partials for the web UI
├── static/style.css       sidebar + main chrome
├── tests/                 pytest suite (31 cases)
└── reports/               generated daily reports (gitignored)
```

## Installation

```bash
pip install -r requirements.txt
```

For the default Claude CLI backend, also install [Claude Code](https://claude.com/claude-code) and log in once with `claude /login` (choose the Anthropic Console / Pro / Max path).

## Configuration

Set API keys, models, and the Claude backend via environment variables (all optional, defaults provided):

| Variable | Description | Default |
|---|---|---|
| `CLAUDE_BACKEND` | `cli` (consumes Max quota via Claude Code) or `api` (consumes API credits) | `cli` |
| `CLAUDE_API_KEY` | Anthropic Claude API key -- only needed when `CLAUDE_BACKEND=api` | — |
| `CLAUDE_MODEL` | Claude model name (API backend only) | `claude-opus-4-6` |
| `GEMINI_API_KEY` | Google Gemini API key | — |
| `GEMINI_MODEL` | Gemini model name | `gemini-3.1-flash-lite-preview` |
| `OPENAI_API_KEY` | OpenAI API key | — |
| `OPENAI_MODEL` | OpenAI model name | `gpt-5.4` |
| `CRAFT_SPACE_ID` | Craft space ID for the "Save to Craft" button target folder | maintainer's own space |
| `CRAFT_ARXIV_FOLDER_ID` | Craft folder ID new saved documents are created in | maintainer's own "arxiv Notes" folder |

The CLI backend always uses the `opus` model alias (hardcoded in `core/providers.py`). The preferred LLM provider can be set via `PREFERRED_PROVIDER` in `core/config.py` (`"claude"`, `"gemini"`, or `"openai"`). Defaults to `"claude"`.

## Usage

### CLI

```bash
# Default: today's report via Claude Code CLI (Max subscription)
python report.py

# Generate a report for any historical date (uses arXiv's submittedDate range query)
python report.py --date 2026-03-15

# Switch to the API SDK:
export CLAUDE_BACKEND=api
export CLAUDE_API_KEY="your_api_key_here"
python report.py
```

### Web UI (FastAPI + HTMX)

```bash
pip install -r requirements.txt
uvicorn server:app --reload --port 8080
```

Then open http://localhost:8080. The home page redirects to the most recent report on disk; click a date in the sidebar to switch, or pick a fresh date and click **Generate report** to spawn a background generation task with live SSE progress.

For running the web UI as a background macOS service, see [docs/launchagent-setup.md](docs/launchagent-setup.md).

## Development

```bash
pytest tests/        # 31 route + helper tests, < 2 s
ruff check .         # lint
ruff format .        # format in place
```

Tests cover the FastAPI shim layer (route handlers, the background `_worker`, the SSE generator). The `core/` package is exercised by the production runs that have built up `reports/`.

## Output Structure

The HTML report contains two sections:

1. **Topic Index** — papers grouped by research area, with reference numbers
2. **Paper Details** — for each paper: bilingual title (English + Chinese), authors, method tag (`Observation` / `Simulation` / `Theory` / `Modeling`), and a concise Chinese description of the physical results

## Notes

- arXiv does not publish new submissions on weekends; running on Saturday or Sunday will return no results
- On Mondays, the script automatically retrieves papers from the preceding Friday to account for the weekend gap
- The "Save to Craft" button is client-side only and requires the Craft desktop app on macOS; it opens a native "open Craft?" confirmation the first time each browser session

## License

GPL-3.0. See [LICENSE](LICENSE).
