# arXiv High-Energy Astrophysics Daily Report Generator

Automatically fetches the latest papers from the arXiv `astro-ph.HE` (High Energy Astrophysical Phenomena) category, uses a large language model to generate a structured academic digest, and exports it as an HTML report.

## Features

- Fetches newly submitted papers based on arXiv's submission sync window (US Eastern Time)
- Supports arbitrary historical dates via arXiv's `submittedDate` range query
- Classifies papers by research topic (e.g., GRBs, black holes, supernovae, cosmic rays)
- Generates Chinese-language summaries for each paper, including research method tags and key physical findings
- Supports Claude (CLI or API), Gemini, and OpenAI backends with automatic fallback on quota errors
- Outputs a styled, self-contained HTML report (light/dark theme adaptive, internal anchor scrolling)
- FastAPI + HTMX web UI with date picker, per-date URLs, and live progress

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

The CLI backend always uses the `opus` model alias (hardcoded in `providers.py`). The preferred LLM provider can be set via `PREFERRED_PROVIDER` in `arxiv_report/config.py` (`"claude"`, `"gemini"`, or `"openai"`). Defaults to `"claude"`.

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

Then open http://localhost:8080.

For running the web UI as a background macOS service, see [docs/launchagent-setup.md](docs/launchagent-setup.md). (Note: that doc references the old Streamlit command; update its command from `streamlit run app.py` to `uvicorn server:app --port 8080` if you use it.)

## Output Structure

The HTML report contains two sections:

1. **Topic Index** — papers grouped by research area, with reference numbers
2. **Paper Details** — for each paper: bilingual title (English + Chinese), authors, method tag (`Observation` / `Simulation` / `Theory` / `Modeling`), and a concise Chinese description of the physical results

## Notes

- arXiv does not publish new submissions on weekends; running on Saturday or Sunday will return no results
- On Mondays, the script automatically retrieves papers from the preceding Friday to account for the weekend gap
