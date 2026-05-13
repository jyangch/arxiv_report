"""CLI entry point for the arXiv daily report generator."""

import argparse
import datetime

from arxiv_report.fetcher import ARXIV_TZ, fetch_arxiv_papers
from arxiv_report.providers import generate_report
from arxiv_report.render import save_html


def _parse_as_of(date_str: str | None) -> datetime.datetime | None:
    """Parse a ``YYYY-MM-DD`` string into noon ET; exit cleanly on bad format."""
    if not date_str:
        return None
    try:
        naive = datetime.datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError as e:
        raise SystemExit(f'❌ Invalid --date format: {date_str!r}. Use YYYY-MM-DD.') from e
    return ARXIV_TZ.localize(naive.replace(hour=12))


def main() -> int:
    """Run the report generation flow once and exit.

    Returns:
        ``0`` on success, ``1`` if every LLM provider failed.
    """
    parser = argparse.ArgumentParser(description='Generate arXiv astro-ph.HE daily report.')
    parser.add_argument(
        '--date',
        default=None,
        help='Target date YYYY-MM-DD (ET). Defaults to today.',
    )
    args = parser.parse_args()
    as_of = _parse_as_of(args.date)

    papers = fetch_arxiv_papers(as_of=as_of)
    try:
        report, provider = generate_report(papers)
    except Exception as error:
        print(f'❌ Report generation failed: {error}')
        return 1
    save_html(papers, report, provider, as_of=as_of)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
