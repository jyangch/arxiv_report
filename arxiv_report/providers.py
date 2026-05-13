"""LLM provider clients, individual generators, and the fallback dispatcher."""

import json
import subprocess
import time

import anthropic
from google import genai
from openai import OpenAI

from arxiv_report.config import (
    CLAUDE_API_KEY,
    CLAUDE_BACKEND,
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


def _generate_with_claude_api(prompt: str) -> str:
    if not claude_client:
        raise RuntimeError('Claude API key is missing. Set CLAUDE_API_KEY.')
    response = claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        messages=[{'role': 'user', 'content': prompt}],
    )
    return next(block.text for block in response.content if block.type == 'text')


def _generate_with_claude_cli(prompt: str) -> str:
    """Headless Claude Code CLI: opus, streams progress, consumes Max quota.

    Uses ``--output-format stream-json --include-partial-messages`` so each
    text delta and ``api_retry`` event is surfaced live. Without streaming a
    buffered subprocess looks identical to a hang, and silent server-side
    retries (up to 10 attempts with backoff) eat the timeout invisibly.
    ``--tools ""`` disables built-in tools (no agent loops);
    ``--no-session-persistence`` skips an unused session record. ``--bare``
    is avoided: it forces ``ANTHROPIC_API_KEY`` and would break Max OAuth.

    Raises:
        subprocess.CalledProcessError: If the CLI exits non-zero.
        RuntimeError: If the stream ends without a ``result`` event.
    """
    proc = subprocess.Popen(
        [
            'claude', '-p',
            '--output-format', 'stream-json',
            '--include-partial-messages',
            '--verbose',
            '--model', 'opus',
            '--tools', '',
            '--no-session-persistence',
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    proc.stdin.write(prompt)
    proc.stdin.close()

    start = time.monotonic()
    output_chars = 0
    last_tick = start
    result_text: str | None = None

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev_type = event.get('type')
            if ev_type == 'system':
                subtype = event.get('subtype')
                if subtype == 'init':
                    print(f'   [claude] session started, model={event.get("model")}')
                elif subtype == 'api_retry':
                    attempt = event.get('attempt')
                    max_retries = event.get('max_retries')
                    err = event.get('error') or 'unknown'
                    delay_s = (event.get('retry_delay_ms') or 0) / 1000
                    print(
                        f'   [claude] api retry {attempt}/{max_retries} '
                        f'(error={err}, wait={delay_s:.1f}s)'
                    )
            elif ev_type == 'stream_event':
                inner = event.get('event', {})
                inner_type = inner.get('type')
                if inner_type == 'message_start':
                    ttft = event.get('ttft_ms')
                    if ttft is not None:
                        print(f'   [claude] first token at {ttft / 1000:.1f}s')
                elif inner_type == 'content_block_delta':
                    text = inner.get('delta', {}).get('text', '')
                    output_chars += len(text)
                    now = time.monotonic()
                    if now - last_tick >= 5:
                        print(
                            f'   [claude] streaming... {output_chars} chars, '
                            f'{now - start:.0f}s elapsed'
                        )
                        last_tick = now
            elif ev_type == 'result':
                result_text = event.get('result', '')
                cost = event.get('total_cost_usd')
                cost_str = f', cost ${cost:.4f}' if cost is not None else ''
                print(
                    f'   [claude] done in {time.monotonic() - start:.0f}s, '
                    f'{output_chars} chars{cost_str}'
                )
    except BaseException:
        proc.kill()
        raise

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    if proc.returncode != 0:
        stderr = proc.stderr.read() if proc.stderr else ''
        raise subprocess.CalledProcessError(proc.returncode, proc.args, stderr=stderr)
    if result_text is None:
        raise RuntimeError('Claude CLI finished without a result event.')
    return result_text.strip()


def _generate_with_claude(prompt: str) -> str:
    if CLAUDE_BACKEND == 'api':
        return _generate_with_claude_api(prompt)
    return _generate_with_claude_cli(prompt)


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
