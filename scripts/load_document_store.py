#!/usr/bin/env python3
"""
Load parsed documents (decisions + laws) into the PostgreSQL document store.

    python scripts/load_document_store.py                 # everything under data/parsed
    python scripts/load_document_store.py --only fedlex   # federal | ticino | fedlex
    python scripts/load_document_store.py --data-dir /app/data/parsed

Idempotent: re-running upserts. Run it after every scrape/parse cycle.
"""
import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.document_store import DocumentStore, decision_record, law_record  # noqa: E402

logger = logging.getLogger("load_document_store")
FEDLEX_FILE = re.compile(r"^SR_(?P<sr>[0-9A-Za-z.\-]+)_(?P<lang>de|fr|it|en|rm)$")


def iter_decisions(directory: Path, source: str) -> Iterator:
    for path in sorted(directory.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                yield decision_record(json.load(f), file_stem=path.stem, source=source)
        except Exception as e:
            logger.error(f"Skipping {path.name}: {e}")


def iter_laws(directory: Path) -> Iterator:
    for path in sorted(directory.glob("*.json")):
        m = FEDLEX_FILE.match(path.stem)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Skipping {path.name}: {e}")
            continue
        articles = data if isinstance(data, list) else data.get("articles", [])
        if m:
            groups = {(m.group("sr"), m.group("lang")): articles}
        else:  # fall back to the metadata inside the articles
            groups = defaultdict(list)
            for a in articles:
                groups[(str(a.get("sr_number")), a.get("language", "de"))].append(a)
        for (sr, lang), arts in groups.items():
            if arts:
                yield law_record(sr, lang, arts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=str(Path(__file__).parent.parent / "data" / "parsed"))
    parser.add_argument("--only", choices=["federal", "ticino", "fedlex"], action="append")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    data_dir = Path(args.data_dir)
    store = DocumentStore()
    store.init_schema()

    targets = args.only or ["federal", "ticino", "fedlex"]
    total = 0
    for name in targets:
        directory = data_dir / name
        if not directory.exists():
            logger.warning(f"{directory} does not exist, skipping")
            continue
        records = iter_laws(directory) if name == "fedlex" else iter_decisions(directory, source=name)
        n = store.upsert_documents(records, batch_size=args.batch_size)
        logger.info(f"{name}: {n} documents upserted")
        total += n

    logger.info(f"Done. library={store.count('library')} codex={store.count('codex')} (this run: {total})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
