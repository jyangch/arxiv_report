"""CLI for refreshing and inspecting NASA SciX / INSPIRE-HEP paper citations."""

import argparse

from core import citations, render


def main() -> int:
    """Dispatch citation status, refresh, and leaderboard CLI commands."""
    parser = argparse.ArgumentParser(description='Manage citation counts for the arXiv corpus')
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('status', help='Show citation coverage and provider status')

    p_ref = sub.add_parser('refresh', help='Fetch/refresh citations via SciX or INSPIRE-HEP')
    p_ref.add_argument('--provider', choices=['auto', 'scix', 'inspire'], default='auto')
    p_ref.add_argument('--days', type=int, default=0)
    p_ref.add_argument('--since', default='')
    p_ref.add_argument('--until', default='')
    p_ref.add_argument('--stale-days', type=float, default=7.0)
    p_ref.add_argument('--max-requests', type=int, default=40)
    p_ref.add_argument('--all', action='store_true', help='Ignore staleness window')
    p_ref.add_argument(
        '--rerender',
        action='store_true',
        help='Re-render daily HTML reports after refreshing citations',
    )

    p_top = sub.add_parser('top', help='Show top cited papers in the corpus')
    p_top.add_argument('-n', '--limit', type=int, default=20)
    p_top.add_argument('--days', type=int, default=0)
    p_top.add_argument('--since', default='')
    p_top.add_argument('--until', default='')

    args = parser.parse_args()

    if args.cmd == 'status':
        st = citations.citation_stats()
        stale = citations.stale_pids(stale_days=7.0)
        tok_s = 'configured' if st['token_configured'] else 'not set (INSPIRE fallback active)'
        print(f'SciX/ADS token: {tok_s}')
        print(
            f'Citations: {st["found_papers"]}/{st["total_papers"]} found '
            f'({st["cited_papers"]} cited, max={st["max_citations"]})'
        )
        print(
            f'Providers: scix={st["scix_records"]}, inspire={st["inspire_records"]} '
            f'| latest={st["latest_fetched"] or "never"} | stale(>7d)={len(stale)}'
        )
        return 0

    if args.cmd == 'refresh':
        try:
            res = citations.refresh_citations(
                provider=args.provider,
                stale_days=0.0 if args.all else args.stale_days,
                days=args.days,
                since=args.since,
                until=args.until,
                max_requests=args.max_requests,
            )
        except citations.CitationError as exc:
            print(f'Citation refresh failed: {exc}')
            return 1
        print(
            f'Refreshed ({res["provider"]}): {res["fetched"]}/{res["requested"]} queried '
            f'in {res["requests"]} reqs, found={res["found"]}, '
            f'remaining={res["remaining"]}' + (' (rate limited)' if res['rate_limited'] else '')
        )
        if args.rerender:
            n_ren = render.rerender_all_reports()
            print(f'Re-rendered {n_ren} daily report HTML files + starred.html')
        return 0

    if args.cmd == 'top':
        items = citations.top_cited_papers(
            limit=args.limit,
            days=args.days,
            since=args.since,
            until=args.until,
        )
        if not items:
            print('No cited papers recorded yet. Run `python citations.py refresh` first.')
            return 0
        for i, item in enumerate(items, start=1):
            cites = item.get('citation_count') or 0
            title = item.get('title_zh') or item.get('title')
            print(f'{i:2d}. [{cites:3d} cites] {item["arxiv_id"]} ({item["report_date"]})  {title}')
            if item.get('title_zh') and item.get('title'):
                print(f'    EN: {item["title"]}')
        return 0

    return 1


if __name__ == '__main__':
    raise SystemExit(main())
