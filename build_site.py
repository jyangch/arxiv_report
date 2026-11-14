"""CLI tool to export a privacy-safe static site bundle under ./dist/site."""

import argparse

from core import citations, config, site_builder


def main() -> int:
    """Parse CLI options, optionally refresh citations, and build the static site."""
    parser = argparse.ArgumentParser(description='Build privacy-safe static HTML site')
    parser.add_argument('--out', default=config.SITE_OUTPUT_DIR, help='Output directory')
    parser.add_argument('--title', default=config.SITE_TITLE, help='Site header title')
    parser.add_argument(
        '--refresh-citations',
        action='store_true',
        help='Refresh recent 30d citations before exporting',
    )
    args = parser.parse_args()

    if args.refresh_citations:
        res = citations.refresh_citations(days=30, max_requests=20)
        print(
            f'Citations refreshed ({res["provider"]}): '
            f'{res["fetched"]}/{res["requested"]} queried, found={res["found"]}'
        )

    summary = site_builder.build_static_site(out_dir=args.out, site_title=args.title)
    print(
        f'Static site built at {summary["out_dir"]}: '
        f'{summary["days"]} days, {summary["weeks"]} weeks, '
        f'{summary["months"]} months, {summary["years"]} years, '
        f'{summary["indexed_papers"]} indexed papers.'
    )
    print(f'Preview: python -m http.server -d {summary["out_dir"]} 8090')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
