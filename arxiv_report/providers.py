"""LLM provider clients, individual generators, and the fallback dispatcher."""

import anthropic
from google import genai
from openai import OpenAI

from arxiv_report.config import (
    CLAUDE_API_KEY,
    CLAUDE_MODEL,
    FALLBACK_ORDER,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    PREFERRED_PROVIDER,
)
from arxiv_report.prompt import build_prompt

claude_client = anthropic.Anthropic(api_key=CLAUDE_API_KEY) if CLAUDE_API_KEY else None
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def _is_quota_error(error: Exception) -> bool:
    msg = str(error).lower()
    return ('429' in msg) or ('resource_exhausted' in msg) or ('quota exceeded' in msg)


def _generate_with_claude(prompt: str) -> str:
    if not claude_client:
        raise RuntimeError('Claude API key is missing. Set CLAUDE_API_KEY.')
    response = claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        messages=[{'role': 'user', 'content': prompt}],
    )
    return next(block.text for block in response.content if block.type == 'text')


def _generate_with_gemini(prompt: str) -> str:
    if not gemini_client:
        raise RuntimeError('Gemini API key is missing. Set GEMINI_API_KEY.')
    response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return response.text


def _generate_with_openai(prompt: str) -> str:
    if not openai_client:
        raise RuntimeError('OpenAI API key is missing. Set OPENAI_API_KEY.')
    response = openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{'role': 'user', 'content': prompt}],
    )
    return response.choices[0].message.content


_GENERATORS = {
    'claude': ('Claude', _generate_with_claude),
    'gemini': ('Gemini', _generate_with_gemini),
    'openai': ('OpenAI', _generate_with_openai),
}


def generate_report(papers: list[dict]) -> tuple[str, str]:
    """Build the prompt, dispatch to providers in fallback order, return (report_html, provider)."""
    if not papers:
        return 'No new papers today.</p>', ''

    prompt = build_prompt(papers)

    providers = [PREFERRED_PROVIDER] + [p for p in FALLBACK_ORDER if p != PREFERRED_PROVIDER]
    print(f'🧠 Preferred provider: {providers[0]}. Generating report...')

    last_error: Exception | None = None
    for i, provider in enumerate(providers):
        label, generate = _GENERATORS[provider]
        try:
            print(f'   Trying {label}...')
            return generate(prompt), provider
        except Exception as e:
            last_error = e
            next_provider = providers[i + 1] if i + 1 < len(providers) else None
            if _is_quota_error(e) and next_provider:
                print(
                    f'⚠️ {provider} quota/rate limit hit, falling back to {next_provider}. Details: {e}'
                )
                continue
            print(f'⚠️ {provider} failed: {e}')

    raise RuntimeError(f'All providers failed. Last error: {last_error}')
