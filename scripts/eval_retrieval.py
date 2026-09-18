#!/usr/bin/env python3
"""
Run the golden retrieval set against the live index and gate on recall.

    python scripts/eval_retrieval.py                       # report only
    python scripts/eval_retrieval.py --min-recall 0.7      # exit 1 if required recall < 70%
    python scripts/eval_retrieval.py --json out.json       # machine-readable results

Needs Qdrant (QDRANT_HOST) and the models (local or MODEL_SERVICE_URL).
"""
import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.eval.retrieval_eval import load_golden, score_all, DEFAULT_GOLDEN  # noqa: E402


async def run(queries, k):
    from src.search.triad_search import TriadSearch
    triad = TriadSearch()
    results = {}
    for q in queries:
        out = await triad.search(query=q.query, top_k=k)
        results[q.id] = {"codex": out.get("codex", {}).get("results", []),
                         "library": out.get("library", {}).get("results", [])}
        logging.info(f"{q.id}: {len(results[q.id]['codex'])} laws, {len(results[q.id]['library'])} decisions")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument("--min-recall", type=float, default=None, help="fail when required recall is below this")
    parser.add_argument("--min-mrr", type=float, default=None)
    parser.add_argument("--json", help="write per-query results to this file")
    parser.add_argument("--ids", help="comma-separated query ids to run (default: all)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    k, queries = load_golden(Path(args.golden))
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",")}
        queries = [q for q in queries if q.id in wanted]
    results = asyncio.run(run(queries, k))
    report = score_all(queries, results, k)
    print(report.summary())

    if args.json:
        Path(args.json).write_text(json.dumps({
            "k": k, "required_recall": report.law_recall, "mrr": report.mrr,
            "optional_recall": report.optional_recall,
            "queries": [{"id": s.id, "hits": s.hits, "optional": s.optional} for s in report.scores],
        }, indent=2, ensure_ascii=False), encoding="utf-8")

    failed = (args.min_recall is not None and report.law_recall < args.min_recall) or \
             (args.min_mrr is not None and report.mrr < args.min_mrr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
