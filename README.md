# arXiv High-Energy Astrophysics Daily Report Generator

Automatically fetches the latest papers from the arXiv `astro-ph.HE` (High Energy Astrophysical Phenomena) category, uses a large language model to generate a structured academic digest, and exports it as an HTML report.

## Features

- Fetches newly submitted papers based on arXiv's submission sync window (US Eastern Time)
- Classifies papers by research topic (e.g., GRBs, black holes, supernovae, cosmic rays)
- Generates Chinese-language summaries for each paper, including research method tags and key physical findings
- Supports Claude, Gemini, and OpenAI backends with automatic fallback on quota errors
- Outputs a styled, self-contained HTML report

## Installation

```bash
pip install arxiv pytz anthropic google-genai openai
```

## Configuration

Set API keys and models via environment variables (all optional, defaults provided):

| Variable | Description | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic Claude API key | — |
| `CLAUDE_MODEL` | Claude model name | `claude-opus-4-6` |
| `GEMINI_API_KEY` | Google Gemini API key | — |
| `GEMINI_MODEL` | Gemini model name | `xxx` |
| `OPENAI_API_KEY` | OpenAI API key | — |
| `OPENAI_MODEL` | OpenAI model name | `xxx` |

The preferred LLM provider can be set via `PREFERRED_PROVIDER` at the top of `report.py` (`"claude"`, `"gemini"`, or `"openai"`). Defaults to `"claude"`.

## Usage

```bash
export ANTHROPIC_API_KEY="your_api_key_here"
python report.py
```

An HTML report will be generated in the current directory.

## Output Structure

The HTML report contains two sections:

1. **Topic Index** — papers grouped by research area, with reference numbers
2. **Paper Details** — for each paper: bilingual title (English + Chinese), authors, method tag (`Observation` / `Simulation` / `Theory` / `Modeling`), and a concise Chinese description of the physical results

## Notes

- arXiv does not publish new submissions on weekends; running on Saturday or Sunday will return no results
- On Mondays, the script automatically retrieves papers from the preceding Friday to account for the weekend gap
