"""CLI entry point for the arXiv daily report generator."""

from arxiv_report.fetcher import fetch_arxiv_papers
from arxiv_report.providers import generate_report
from arxiv_report.render import save_html


def main() -> int:
    papers = fetch_arxiv_papers()
    try:
        report, provider = generate_report(papers)
    except Exception as error:
        print(f'❌ Report generation failed: {error}')
        return 1
    save_html(papers, report, provider)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
