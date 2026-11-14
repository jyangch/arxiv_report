# arXiv High-Energy Astrophysics Daily Digest

Automatically fetches new papers from arXiv `astro-ph.HE` (High Energy Astrophysical Phenomena), synthesizes structured **bilingual (Chinese + English)** digests using a large language model, indexes every paper into a persistent local corpus for cross-day search and personalized recommendations, and serves an interactive **FastAPI + HTMX** web workspace alongside CLI tools and a privacy-safe static site exporter.

## Features

- **Resilient arXiv Ingestion**: Fetches today's submissions via the `astro-ph.HE` pastweek listing, supports arbitrary historical dates via arXiv's `submittedDate` range query, falls back to RSS when needed, detects US holidays / weekend deferrals, and enforces HTTP 429 cooldown guards.
- **Bilingual Structured Digests (`中 / EN`)**: Generates Today's Highlights, bilingual titles, Research Question, Method tags (`Observation` / `Simulation` / `Theory` / `Modeling`), Findings, and Limitations & Future Directions, with an instant topbar `中 / EN` language switch while keeping UI chrome in English.
- **16-Topic Taxonomy & Object Badges**: Classifies papers across a canonical 16-topic ontology (`GRB`, `FXT/EP`, `FRB`, `SNe/KN`, `TDE/QPE`, `NS/PSR`, `XRB/WD`, `GW`, `BH/GR`, `AGN`, `SNR/ICM`, `VHE`, `CR/ν`, `DM/ALP`, `Plasma`, `Inst/ML`) with light/dark topic chips and deterministic astrophysical designation badges (`GRB`, `EP`, `FRB`, `GW`, `PSR`, `4FGL`, `AT`, `SN`, ...).
- **Multi-Provider LLM Dispatch**: Defaults to the Antigravity CLI (`agy`, `gemini-4-hydrogen-high`) with automatic chunking (`MAX_PAPERS_PER_CHUNK=25`) and bisection recovery on oversized prompts, plus optional Gemini API SDK, Claude (Code CLI or API), and OpenAI (Codex CLI or API) backends.
- **Unified Topbar Web UI (FastAPI + HTMX)**:
  - Single unified header across `/r/{date}`, `/summary`, `/recommend`, `/search`, `/similar/{arxiv_id}`, and `/starred` with a synced Light / Dark theme toggle.
  - `Day / Week / Month / Year` dimension switcher, interactive calendar popover, and `+ Generate` popover with live SSE progress and existing-report overwrite guard.
  - Global search input (`/` keyboard shortcut), `Recommend`, `Starred`, and bilingual `中 / EN` toggle.
- **Cross-Day Corpus, Similarity & Recommendations**: Stores bilingual digests in SQLite (`./reports/.data/papers.db`) with cached TF-IDF + metadata features (`features.pkl`) powering `/search`, `/similar/{arxiv_id}`, and `LinearSVC` personalized ranking (`/recommend`) over `Starred` papers and local Obsidian notes (`7d / 14d / 30d / 90d / All` windows and source filters).
- **Citations & Period Rollups**: Queries NASA SciX / ADS (`SCIX_API_TOKEN`) with automatic zero-token INSPIRE-HEP fallback (`citations.py`), adds `Cited N` / `SciX` badges and `Most cited` sorting, and renders Week / Month / Year rollups (`/summary`) with Topic × Time heatmaps and top highlights.
- **Obsidian Integration & Zero-LLM Re-rendering**: Per-paper `Save to Obsidian` writes clean Markdown notes into your local vault without overwriting existing bodies or deleting files on unmark, persists state server-side in `reports/paper_state.json`, and keeps HTML fragments decoupled under `reports/fragments/` for zero-LLM full-history layout re-rendering.
- **LAN Group Sharing & Static Export**: Supports read-only LAN visitors with per-browser `localStorage` stars/recommendations and owner `/unlock` (`ALLOWED_NETS`, `UI_TOKEN`), or exporting a privacy-safe static SPA (`build_site.py`) ready for GitHub Pages, Cloudflare Pages, or Nginx.

## Project Layout

```text
.
├── server.py              FastAPI web UI, LAN auth guard, /search, /similar, /recommend, /summary, SSE
├── report.py              CLI entry point for single-day or cron daily digest generation
├── sanity.py              CLI for corpus bootstrap, stats, search, similarity, recommendations, and rerender
├── citations.py           CLI for SciX / INSPIRE-HEP citation refresh, status, and top cited papers
├── build_site.py          CLI for privacy-safe static HTML + client search bundle (./dist/site)
├── core/                  Domain package (`__version__ = '0.3.0'`)
│   ├── citations.py       NASA SciX / ADS + INSPIRE-HEP batch citation client and SQLite cache
│   ├── config.py          Environment variable defaults and runtime configuration
│   ├── corpus.py          Persistent bilingual SQLite corpus & topic migration
│   ├── features.py        Cached TF-IDF + author + topic/object feature matrix
│   ├── fetcher.py         arXiv pastweek/API + RSS fetcher with holiday/cooldown tracking
│   ├── fragments.py       Decoupled HTML body fragment parser, sanitizer, and validator
│   ├── objects.py         Deterministic astrophysical transient/source designation extractor
│   ├── obsidian.py        Obsidian Markdown renderer and safe vault note writer
│   ├── paper_state.py     Server-side Starred / Obsidian mark store (`reports/paper_state.json`)
│   ├── prompt.py          Bilingual structured digest prompt builder
│   ├── providers.py       Gemini (`agy`/API), Claude (CLI/API), OpenAI (Codex/API) dispatcher
│   ├── pub_status.py      Accepted / Published / Submitted status classifier
│   ├── render.py          Standalone HTML digest + Starred page renderer & historical re-renderer
│   ├── rollups.py         Week / Month / Year rollup statistics and Topic × Time matrices
│   ├── sanity.py          Cross-day search, similarity engine, and LinearSVC recommender
│   ├── site_builder.py    Static HTML exporter + client-side `search_index.json` SPA builder
│   └── topics.py          16-topic canonical ontology + light/dark chip palette
├── templates/             Jinja2 base shell (`base.html`, `home.html`) and HTMX partials
├── static/                `style.css`, `ui_shell.js` (topbar/calendar/generate/lang), `paper_state.js`
└── reports/               Generated HTML digests, `fragments/`, `paper_state.json`, `.data/` (gitignored)
```

## Installation

```bash
pip install -r requirements.txt
```

For the default Gemini CLI backend, install the Antigravity CLI (`agy`) and authenticate once. Optional fallback backends use Claude Code (`claude`) or Codex CLI (`codex login`), or standard SDK API keys (`GEMINI_API_KEY`, `CLAUDE_API_KEY`, `OPENAI_API_KEY`).

## Configuration

All settings are configured via environment variables (or a gitignored `.env` file in the project root; defaults are provided in `core/config.py`):

| Variable | Description | Default |
|---|---|---|
| `PREFERRED_PROVIDER` | Preferred LLM provider: `gemini`, `claude`, or `openai` | `gemini` |
| `FALLBACK_ORDER` | Comma-separated provider fallback order | `gemini` |
| `GEMINI_BACKEND` | `cli` (via `agy`) or `api` (`google-genai` SDK) | `cli` |
| `GEMINI_API_KEY` | Google Gemini API key (when `GEMINI_BACKEND=api`) | `—` |
| `GEMINI_MODEL` | Gemini / `agy` model name | `gemini-4-hydrogen-high` |
| `AGY_CLI` | Optional absolute path to `agy` CLI | auto-detected |
| `MAX_PAPERS_PER_CHUNK` | Max papers per chunk during CLI/SDK generation | `25` |
| `OPENAI_BACKEND` | `codex` (Codex CLI) or `api` (OpenAI SDK) | `codex` |
| `OPENAI_API_KEY` | OpenAI API key (when `OPENAI_BACKEND=api`) | `—` |
| `OPENAI_MODEL` | OpenAI / Codex model name | `gpt-5.5` |
| `CODEX_CLI` | Optional absolute path to Codex CLI | auto-detected |
| `CLAUDE_BACKEND` | `cli` (Claude Code) or `api` (Anthropic SDK) | `cli` |
| `CLAUDE_API_KEY` | Anthropic Claude API key (when `CLAUDE_BACKEND=api`) | `—` |
| `CLAUDE_MODEL` | Claude model name (API backend) | `claude-opus-4-6` |
| `OBSIDIAN_VAULT_PATH` | Local Obsidian vault root path for `Save to Obsidian` | `—` |
| `OBSIDIAN_ARXIV_FOLDER` | Target subfolder inside Obsidian vault | `Arxiv-Notes` |
| `ARXIV_DATA_DIR` | Directory for `papers.db` and `features.pkl` | `./reports/.data` |
| `ALLOWED_NETS` | Comma-separated IP/CIDR allowlist for incoming requests | `—` |
| `UI_TOKEN` | Owner unlock token enabling write operations from non-local clients | `—` |
| `SCIX_API_TOKEN` | Optional NASA SciX / ADS bearer token (INSPIRE-HEP fallback when unset) | `—` |
| `SCIX_API_URL` | NASA SciX / ADS query endpoint | `https://api.adsabs.harvard.edu/v1/search/query` |
| `SITE_OUTPUT_DIR` | Output directory for static site exports (`build_site.py`) | `./dist/site` |
| `SITE_TITLE` | Title displayed on exported static site pages | `Daily Digest` |

## Usage

### Web UI (FastAPI + HTMX)

```bash
uvicorn server:app --port 8080
```

Open <http://localhost:8080> to jump to the latest digest on disk. Use the top bar to switch between `Day / Week / Month / Year` views, open the calendar picker, run full-corpus search (`/`), inspect personalized recommendations (`/recommend`), browse `Starred` papers (`/starred`), toggle `中 / EN` or Light/Dark mode, or generate a new daily report (`+ Generate`).

### CLI Tools

```bash
# Generate today's digest (or a specific historical date)
python report.py
python report.py --date 2026-11-13

# Corpus bootstrap, search, similarity, recommendations & zero-LLM re-render
python sanity.py bootstrap --rerender
python sanity.py stats
python sanity.py search "EP240414a"
python sanity.py search "磁星" --topic grb --days 90
python sanity.py similar 2611.12945
python sanity.py recommend --days 30
python sanity.py rerender
python sanity.py migrate-topics

# Citation counts (NASA SciX / ADS or INSPIRE-HEP)
python citations.py status
python citations.py refresh --days 30 --rerender
python citations.py top -n 10
```

### Sharing & Static Publishing

Two built-in modes let collaborators browse reports safely without exposing your Obsidian vault or overwriting your personal stars:

1. **LAN Group Server (Read-only Visitors + Owner Unlock)**:
   ```bash
   UI_TOKEN="your-owner-token" ALLOWED_NETS="127.0.0.0/8,192.168.0.0/16" \
     uvicorn server:app --host 0.0.0.0 --port 8080
   ```
   Non-loopback visitors automatically get read-only browsing plus isolated browser-local `localStorage` stars and recommendations; you can unlock owner controls (`+ Generate`, server-side `Starred`, `Save to Obsidian`) via `/unlock`.
2. **Privacy-Safe Static Site (`./dist/site`)**:
   ```bash
   python build_site.py --out ./dist/site
   python -m http.server -d ./dist/site 8090
   ```
   Exports standalone daily digests, rollups, `starred.html`, and a client-side `search_index.json` SPA with zero server dependency and without including `paper_state.json`, SQLite DBs, or Obsidian notes.

## Development

```bash
ruff format .
ruff check .
pytest -v
```

## Notes

- arXiv does not announce new papers on weekends; Monday runs automatically cover the weekend window starting Friday.
- `Save to Obsidian` writes formatted `.md` files into `OBSIDIAN_VAULT_PATH/OBSIDIAN_ARXIV_FOLDER`, preserves existing note bodies, and never deletes files when unmarked in the UI.

## License

GPL-3.0. See [LICENSE](LICENSE).
