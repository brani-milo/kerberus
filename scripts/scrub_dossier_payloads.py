#!/usr/bin/env python3
"""
Remove plaintext from dossier vectors written before payloads became zero-knowledge.

Older uploads stored the document title and a 200-character text preview in the
Qdrant payload of every dossier_* collection. This script, for every point:
  - sets payload.embedding_id (doc_id + chunk_index) so search can resolve the
    decrypted chunk from SQLCipher, and
  - deletes payload.title and payload.text_preview.
No re-embedding; the vectors are untouched.

    QDRANT_HOST=localhost python scripts/scrub_dossier_payloads.py --dry-run
    QDRANT_HOST=localhost python scripts/scrub_dossier_payloads.py
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.vector_db import QdrantManager  # noqa: E402

PLAINTEXT_KEYS = ["title", "text_preview"]
logger = logging.getLogger("scrub_dossier_payloads")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    client = QdrantManager().client
    collections = [c.name for c in client.get_collections().collections if c.name.startswith("dossier_")]
    logger.info(f"{len(collections)} dossier collections")

    total_scrubbed = 0
    for name in collections:
        offset, scrubbed = None, 0
        while True:
            points, offset = client.scroll(collection_name=name, limit=args.batch_size, offset=offset,
                                           with_payload=True, with_vectors=False)
            for point in points:
                payload = point.payload or {}
                leaking = [k for k in PLAINTEXT_KEYS if k in payload]
                needs_id = "embedding_id" not in payload and payload.get("doc_id") is not None \
                    and payload.get("chunk_index") is not None
                if not leaking and not needs_id:
                    continue
                scrubbed += 1
                if args.dry_run:
                    continue
                if needs_id:
                    client.set_payload(collection_name=name, points=[point.id],
                                       payload={"embedding_id": f"{payload['doc_id']}_chunk_{payload['chunk_index']}"})
                if leaking:
                    client.delete_payload(collection_name=name, points=[point.id], keys=leaking)
            if offset is None:
                break
        logger.info(f"{name}: {scrubbed} points {'would be ' if args.dry_run else ''}scrubbed")
        total_scrubbed += scrubbed

    logger.info(f"Done: {total_scrubbed} points {'would be ' if args.dry_run else ''}scrubbed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
