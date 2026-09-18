#!/usr/bin/env python3
"""
Backfill full text into existing Qdrant points (no re-embedding).

- library: payload["text"] = the chunk recomputed from the parsed decision
- codex:   payload["article_text"] = the article text from the parsed Fedlex JSON

Points embedded before chunk text was stored carry only a 200-character
`text_preview`, so the reranker scored snippets. This recomputes each chunk
from the parsed decision (document store first, then data/parsed files) with
the SAME chunking rules used at embedding time and writes payload["text"]
via set_payload. No re-embedding, no vector changes.

    python scripts/backfill_chunk_text.py            # all points missing "text"
    python scripts/backfill_chunk_text.py --dry-run
    python scripts/backfill_chunk_text.py --collection library --batch-size 256
    python scripts/backfill_chunk_text.py --collection codex
"""
import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from qdrant_client import models  # noqa: E402

from src.database.vector_db import QdrantManager  # noqa: E402
from src.embedder.chunking import chunk_decision  # noqa: E402
from src.search.document_fetcher import fetch_full_decision, fetch_full_law  # noqa: E402

logger = logging.getLogger("backfill_chunk_text")


def chunk_texts_for(decision_id: str, cache: Dict[str, Dict[int, str]]) -> Dict[int, str]:
    """chunk_index -> text for one decision (memoised)."""
    if decision_id in cache:
        return cache[decision_id]
    doc = fetch_full_decision(decision_id)
    mapping: Dict[int, str] = {}
    if doc:
        for chunk in chunk_decision(doc):
            mapping[chunk["chunk_index"]] = chunk["text"]
    cache[decision_id] = mapping
    return mapping


def article_fields_for(payload: Dict, cache: Dict[str, Dict[str, Dict]]) -> Dict:
    """{article_text, article_title} for a codex point, from the parsed law (memoised per law/language)."""
    sr, lang = str(payload.get("sr_number") or ""), payload.get("language") or "de"
    key = f"{sr}:{lang}"
    if key not in cache:
        law = fetch_full_law(sr, lang) if sr else None
        mapping: Dict[str, Dict] = {}
        if law and law.get("language") == lang:
            for a in law.get("articles", []):
                fields = {"article_text": a.get("article_text", ""), "article_title": a.get("article_title")}
                if a.get("id"):
                    mapping[str(a["id"])] = fields
                mapping.setdefault(f"art:{a.get('article_number')}", fields)
        cache[key] = mapping
    mapping = cache[key]
    return mapping.get(str(payload.get("id"))) or mapping.get(f"art:{payload.get('article_number')}") or {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collection", default="library")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true", help="rewrite the field on ALL points, not only where it is missing (use after a parser fix)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    client = QdrantManager().client
    is_codex = args.collection == "codex"
    field = "article_text" if is_codex else "text"
    missing_text = None if args.overwrite else models.Filter(must=[models.IsEmptyCondition(is_empty=models.PayloadField(key=field))])

    stats = {"scanned": 0, "updated": 0, "no_source": 0, "no_chunk": 0}
    cache: Dict[str, Dict[int, str]] = {}
    offset = None

    while True:
        points, offset = client.scroll(
            collection_name=args.collection,
            scroll_filter=missing_text,
            limit=args.batch_size,
            offset=offset,
            with_payload=["decision_id", "chunk_index", "text_preview", "id", "sr_number", "language", "article_number"],
            with_vectors=False,
        )
        if not points:
            break

        updates: List[tuple] = []
        for point in points:
            stats["scanned"] += 1
            payload = point.payload or {}
            if is_codex:
                fields = article_fields_for(payload, cache)
                if fields.get("article_text"):
                    updates.append((point.id, {**fields, "parse_missing": False}))
                else:
                    # No such article in the corrected parse: the point is a parser artefact
                    # (e.g. table-of-contents paragraph ids). Mark it so search skips it.
                    updates.append((point.id, {"parse_missing": True}))
                    stats["no_source"] += 1
                continue
            decision_id = payload.get("decision_id")
            idx = payload.get("chunk_index")
            texts = chunk_texts_for(decision_id, cache) if decision_id else {}
            if not texts:
                stats["no_source"] += 1
                continue
            text = texts.get(int(idx)) if idx is not None else None
            if not text:
                stats["no_chunk"] += 1
                continue
            preview = (payload.get("text_preview") or "").rstrip(".")[:40]
            if preview and preview not in " ".join(text.split()):
                logger.debug(f"Preview mismatch for {decision_id}#{idx}; writing recomputed text anyway")
            updates.append((point.id, text))

        if updates and not args.dry_run:
            for point_id, value in updates:
                payload = value if isinstance(value, dict) else {field: value}
                client.set_payload(collection_name=args.collection, payload=payload, points=[point_id])
        stats["updated"] += len(updates)
        logger.info(f"scanned={stats['scanned']} updated={stats['updated']} no_source={stats['no_source']} no_chunk={stats['no_chunk']}")

        if offset is None or (args.dry_run and stats["scanned"] >= 5000):
            break

    logger.info(f"Done: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
