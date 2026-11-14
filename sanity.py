"""CLI tool for bootstrapping, searching, and ranking the local paper corpus."""

import argparse

from core import corpus, render, sanity
from core.features import get_feature_index


def _print_cards(items: list[dict], show_score: bool = True) -> None:
    if not items:
        print('No matching papers found.')
        return
    for i, item in enumerate(items, start=1):
        score_s = f' [{item["score"]}]' if show_score and 'score' in item else ''
        date_s = item['report_date'] or 'unlisted'
        title = item['title_zh'] or item['title']
        print(f'{i:2d}. [{date_s}] {item["arxiv_id"]}{score_s}  {title}')
        if item.get('title_zh') and item.get('title'):
            print(f'    EN: {item["title"]}')
        if item.get('matched_terms'):
            print(f'    Why: {", ".join(item["matched_terms"])}')
        if item.get('shared_terms'):
            print(f'    Shared: {", ".join(item["shared_terms"])}')
        if item.get('nearest_positive'):
            np_info = item['nearest_positive']
            print(
                f'    Nearest saved: [{np_info["pid"]}] {np_info["title"]} '
                f'(sim={np_info["similarity"]:.2f})'
            )


def main() -> int:
    """Dispatch CLI subcommands for corpus management and exploration."""
    parser = argparse.ArgumentParser(description='arXiv astro-ph.HE corpus & recommender CLI')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_boot = sub.add_parser(
        'bootstrap', help='Import caches, extract fragments, build TF-IDF index'
    )
    p_boot.add_argument(
        '--rerender',
        action='store_true',
        help='Also re-render all daily reports and starred.html in place',
    )

    sub.add_parser('stats', help='Show indexed corpus and owner positive stats')

    p_search = sub.add_parser('search', help='Bilingual full-text search across the corpus')
    p_search.add_argument('query', help='Query string (English, Chinese, or object name)')
    p_search.add_argument('--days', type=int, default=0)
    p_search.add_argument('--topic', default='')
    p_search.add_argument('--method', default='')
    p_search.add_argument('--status', default='')
    p_search.add_argument('--limit', type=int, default=20)

    p_sim = sub.add_parser('similar', help='Find corpus papers similar to a given arXiv ID')
    p_sim.add_argument('arxiv_id')
    p_sim.add_argument('--days', type=int, default=0)
    p_sim.add_argument('--limit', type=int, default=15)

    p_rec = sub.add_parser('recommend', help='Personalized LinearSVC recommendations')
    p_rec.add_argument('--source', choices=['all', 'starred', 'obsidian'], default='all')
    p_rec.add_argument('--days', type=int, default=30)
    p_rec.add_argument('--include-positives', action='store_true')
    p_rec.add_argument('--limit', type=int, default=20)

    p_ren = sub.add_parser('rerender', help='Re-render daily HTML reports from fragments')
    p_ren.add_argument('--date', default='', help='Single YYYY-MM-DD report date')

    sub.add_parser(
        'migrate-topics',
        help='Offline-remap stored fragments to 16 topics, rebuild index, and rerender HTML',
    )

    args = parser.parse_args()

    if args.cmd == 'bootstrap':
        summary = corpus.bootstrap_corpus()
        idx = get_feature_index(force_rebuild=True)
        print(
            f'Bootstrapped: {summary["cache_files"]} cache files, '
            f'{summary["reports"]} reports ({summary["fragments_extracted"]} fragments extracted), '
            f'{summary["total_papers"]} total papers ({summary["digest_papers"]} with digests), '
            f'feature shape={idx["matrix"].shape}'
        )
        if args.rerender:
            n_ren = render.rerender_all_reports()
            print(f'Re-rendered {n_ren} daily report HTML files + starred.html')
        return 0

    if args.cmd == 'stats':
        st = corpus.corpus_stats()
        pos = sanity.collect_owner_positives()
        print(
            f'Corpus: {st["total_papers"]} papers ({st["digest_papers"]} digested across '
            f'{st["report_days"]} report days, {st["min_date"]} .. {st["max_date"]})'
        )
        print(
            f'Owner positives: {pos["combined_count"]} unique '
            f'(starred={pos["starred_count"]}, obsidian={pos["obsidian_count"]})'
        )
        print('Methods:', st['method_counts'])
        return 0

    if args.cmd == 'search':
        res = sanity.search_papers(
            args.query,
            days=args.days,
            topic=args.topic,
            method=args.method,
            status=args.status,
            limit=args.limit,
        )
        print(f'Found {res["total"]} matches in {res["elapsed_ms"]} ms:')
        _print_cards(res['items'])
        return 0

    if args.cmd == 'similar':
        res = sanity.similar_papers(args.arxiv_id, days=args.days, limit=args.limit)
        if not res['seed']:
            print(f'Seed paper {args.arxiv_id} not found in corpus.')
            return 1
        print(
            f'Seed: [{res["seed"]["arxiv_id"]}] {res["seed"]["title_zh"] or res["seed"]["title"]}'
        )
        _print_cards(res['items'])
        return 0

    if args.cmd == 'recommend':
        res = sanity.recommend_papers(
            source=args.source,
            days=args.days,
            exclude_positives=not args.include_positives,
            limit=args.limit,
        )
        print(
            f'Recommender mode={res["mode"]} '
            f'({res["matched_positives"]}/{res["total_positives"]} positives matched) '
            f'in {res["elapsed_ms"]} ms'
        )
        if res['top_positive_features']:
            top_w = ', '.join(
                f'{f["term"]}({f["weight"]:+.2f})' for f in res['top_positive_features'][:8]
            )
            print(f'Top interest terms: {top_w}')
        _print_cards(res['items'])
        return 0

    if args.cmd == 'rerender':
        if args.date:
            out = render.rerender_report(args.date)
            print(f'Re-rendered {out}')
        else:
            n_ren = render.rerender_all_reports()
            print(f'Re-rendered {n_ren} daily report HTML files + starred.html')
        return 0

    if args.cmd == 'migrate-topics':
        mig = corpus.migrate_all_fragments(reports_dir=render.REPORTS_DIR)
        idx = get_feature_index(force_rebuild=True)
        n_ren = render.rerender_all_reports()
        print(
            f'Migrated {mig["fragments_migrated"]} fragments '
            f'({mig["fragments_changed"]} changed, {mig["papers_updated"]} papers), '
            f'feature shape={idx["matrix"].shape}, '
            f're-rendered {n_ren} daily report HTML files + starred.html'
        )
        return 0

    return 1


if __name__ == '__main__':
    raise SystemExit(main())
