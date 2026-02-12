#!/usr/bin/env python3
"""
Modal Serverless GPU Embedding for KERBERUS.

Runs BGE-M3 embedding on Modal's A10G GPUs (~$0.50/hr).
Processes 385k documents in ~2-4 hours for ~$2-3 total.

Setup:
    1. pip install modal
    2. modal setup  # Login with GitHub/Google
    3. modal run scripts/modal_embed.py --upload  # Upload parsed data
    4. modal run scripts/modal_embed.py --embed   # Run embedding
    5. modal run scripts/modal_embed.py --download  # Download results

Usage:
    modal run scripts/modal_embed.py --upload     # Upload parsed JSONs to Modal
    modal run scripts/modal_embed.py --embed      # Run GPU embedding
    modal run scripts/modal_embed.py --embed --collection=codex  # Just Fedlex
    modal run scripts/modal_embed.py --download   # Download embeddings
"""

import modal
import json
import os
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

# Modal app definition
app = modal.App("kerberus-embedder")

# Persistent volume for data storage
volume = modal.Volume.from_name("kerberus-data", create_if_missing=True)

# Container image with all dependencies
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch>=2.0",
        "transformers>=4.36",
        "FlagEmbedding>=1.2",
        "numpy",
        "tqdm",
        "orjson",  # Fast JSON
    )
    .env({"TOKENIZERS_PARALLELISM": "false"})
)


# ============================================
# BGE-M3 Embedder (runs on GPU)
# ============================================
@app.cls(
    image=image,
    gpu="A10G",  # 24GB VRAM, ~$0.50/hr
    timeout=3600 * 6,  # 6 hour max
    volumes={"/data": volume},
)
class BGEEmbedder:
    """BGE-M3 embedder running on Modal GPU."""

    @modal.enter()
    def load_model(self):
        """Load model once when container starts."""
        from FlagEmbedding import BGEM3FlagModel
        import torch

        print(f"Loading BGE-M3 on {torch.cuda.get_device_name(0)}...")
        self.model = BGEM3FlagModel(
            "BAAI/bge-m3",
            use_fp16=True,
            device="cuda"
        )

        # Warmup
        _ = self.model.encode("Warmup", max_length=512)
        print("BGE-M3 loaded and ready!")

    @modal.method()
    def embed_batch(self, texts: List[str], batch_size: int = 64) -> List[Dict]:
        """
        Embed a batch of texts.

        Returns list of {'dense': [...], 'sparse': {...}}
        """
        import torch

        embeddings = self.model.encode(
            texts,
            batch_size=batch_size,
            max_length=2048,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False
        )

        results = []
        dense_vecs = embeddings['dense_vecs']
        sparse_vecs = embeddings['lexical_weights']

        if isinstance(dense_vecs, torch.Tensor):
            dense_vecs = dense_vecs.cpu().numpy()

        for d, s in zip(dense_vecs.tolist(), sparse_vecs):
            results.append({
                "dense": d,
                "sparse": {str(k): float(v) for k, v in s.items()}
            })

        return results

    @modal.method()
    def process_collection(
        self,
        collection: str,
        batch_size: int = 64,
        file_batch_size: int = 1000
    ) -> Dict:
        """
        Process entire collection (codex or library).

        Reads from /data/parsed/{collection}/
        Writes to /data/embeddings/{collection}/
        """
        import orjson
        from tqdm import tqdm

        # Determine input/output paths
        if collection == "codex":
            input_dir = Path("/data/parsed/fedlex")
            pattern = "SR_*.json"
        else:
            input_dir = Path("/data/parsed")
            pattern = None  # Multiple subdirs

        output_dir = Path(f"/data/embeddings/{collection}")
        output_dir.mkdir(parents=True, exist_ok=True)

        stats = {"processed": 0, "embedded": 0, "errors": 0, "files_written": 0}

        if collection == "codex":
            # Fedlex: each file contains a list of articles
            stats = self._process_fedlex(input_dir, output_dir, batch_size, file_batch_size)
        else:
            # Library: federal courts + ticino
            courts = ["CH_BGer", "CH_BVGer", "CH_BGE", "CH_BStGer", "CH_EDOEB", "CH_BPatG"]

            for court in courts:
                court_dir = input_dir / "federal" / court
                if court_dir.exists():
                    print(f"\nProcessing {court}...")
                    court_stats = self._process_decisions(court_dir, output_dir / court, batch_size, file_batch_size)
                    for k in stats:
                        stats[k] += court_stats.get(k, 0)

            # Ticino
            ticino_dir = input_dir / "ticino"
            if ticino_dir.exists():
                print(f"\nProcessing Ticino...")
                ticino_stats = self._process_decisions(ticino_dir, output_dir / "ticino", batch_size, file_batch_size)
                for k in stats:
                    stats[k] += ticino_stats.get(k, 0)

        # Commit volume changes
        volume.commit()

        return stats

    def _process_fedlex(
        self,
        input_dir: Path,
        output_dir: Path,
        batch_size: int,
        file_batch_size: int
    ) -> Dict:
        """Process Fedlex articles."""
        import orjson
        from tqdm import tqdm

        stats = {"processed": 0, "embedded": 0, "errors": 0, "files_written": 0}

        # Collect all articles
        all_articles = []
        json_files = sorted(input_dir.glob("SR_*.json"))

        print(f"Loading {len(json_files)} Fedlex files...")
        for json_file in tqdm(json_files, desc="Loading"):
            try:
                with open(json_file, 'rb') as f:
                    articles = orjson.loads(f.read())
                if isinstance(articles, list):
                    all_articles.extend(articles)
                else:
                    all_articles.append(articles)
            except Exception as e:
                print(f"Error loading {json_file}: {e}")
                stats["errors"] += 1

        print(f"Loaded {len(all_articles)} articles total")
        stats["processed"] = len(all_articles)

        # Process in batches
        output_batch = []
        for i in tqdm(range(0, len(all_articles), batch_size), desc="Embedding"):
            batch = all_articles[i:i + batch_size]
            texts = [a.get("article_text", "") for a in batch]

            try:
                embeddings = self.embed_batch.local(texts, batch_size)

                for article, embedding in zip(batch, embeddings):
                    output_batch.append({
                        "id": article.get("id"),
                        "payload": self._build_fedlex_payload(article),
                        "vector": embedding
                    })
                    stats["embedded"] += 1

                # Write batch to file
                if len(output_batch) >= file_batch_size:
                    self._write_batch(output_dir, output_batch, stats["files_written"])
                    stats["files_written"] += 1
                    output_batch = []

            except Exception as e:
                print(f"Batch error: {e}")
                stats["errors"] += len(batch)

        # Write remaining
        if output_batch:
            self._write_batch(output_dir, output_batch, stats["files_written"])
            stats["files_written"] += 1

        return stats

    def _process_decisions(
        self,
        input_dir: Path,
        output_dir: Path,
        batch_size: int,
        file_batch_size: int
    ) -> Dict:
        """Process court decisions."""
        import orjson
        from tqdm import tqdm
        import re

        stats = {"processed": 0, "embedded": 0, "errors": 0, "files_written": 0}
        output_dir.mkdir(parents=True, exist_ok=True)

        json_files = sorted(input_dir.glob("*.json"))
        print(f"Found {len(json_files)} decision files")

        output_batch = []

        for json_file in tqdm(json_files, desc="Processing"):
            try:
                with open(json_file, 'rb') as f:
                    decision = orjson.loads(f.read())

                # Chunk decision
                chunks = self._chunk_decision(decision)
                stats["processed"] += 1

                if not chunks:
                    continue

                # Embed chunks
                texts = [c["text"] for c in chunks]
                embeddings = self.embed_batch.local(texts, min(batch_size, len(texts)))

                for chunk, embedding in zip(chunks, embeddings):
                    output_batch.append({
                        "id": chunk["chunk_id"],
                        "payload": self._build_decision_payload(chunk),
                        "vector": embedding
                    })
                    stats["embedded"] += 1

                # Write batch to file
                if len(output_batch) >= file_batch_size:
                    self._write_batch(output_dir, output_batch, stats["files_written"])
                    stats["files_written"] += 1
                    output_batch = []

            except Exception as e:
                print(f"Error processing {json_file.name}: {e}")
                stats["errors"] += 1

        # Write remaining
        if output_batch:
            self._write_batch(output_dir, output_batch, stats["files_written"])
            stats["files_written"] += 1

        return stats

    def _chunk_decision(self, decision: dict, max_words: int = 1000) -> List[Dict]:
        """Chunk a decision into sections."""
        import re

        chunks = []
        decision_id = decision.get("id", "unknown")
        content = decision.get("content", {})

        sections = [
            ("regeste", content.get("regeste")),
            ("facts", content.get("facts")),
            ("reasoning", content.get("reasoning")),
            ("decision", content.get("decision"))
        ]

        chunk_index = 0
        for section_type, section_text in sections:
            if not section_text:
                continue

            # Split long sections
            words = section_text.split()
            if len(words) <= max_words:
                text_parts = [section_text]
            else:
                # Split at paragraph boundaries
                paragraphs = re.split(r'\n\n+|\n(?=\d+\.?\s)', section_text)
                text_parts = []
                current = []
                current_count = 0

                for para in paragraphs:
                    para = para.strip()
                    if not para:
                        continue
                    para_words = len(para.split())

                    if current_count + para_words > max_words and current:
                        text_parts.append('\n\n'.join(current))
                        current = [para]
                        current_count = para_words
                    else:
                        current.append(para)
                        current_count += para_words

                if current:
                    text_parts.append('\n\n'.join(current))

            for text in text_parts:
                chunks.append({
                    "chunk_id": f"{decision_id}_chunk_{chunk_index}",
                    "decision_id": decision_id,
                    "chunk_type": section_type,
                    "chunk_index": chunk_index,
                    "text": text,
                    "decision": decision
                })
                chunk_index += 1

        return chunks

    def _build_fedlex_payload(self, article: dict) -> dict:
        """Build Qdrant payload for Fedlex article."""
        text = article.get("article_text", "")
        text_preview = ' '.join(text.split())[:200]
        if len(text) > 200:
            text_preview += "..."

        return {
            "id": article.get("id"),
            "base_id": article.get("base_id"),
            "sr_number": article.get("sr_number"),
            "sr_name": article.get("sr_name"),
            "abbreviation": article.get("abbreviation"),
            "abbreviations_all": article.get("abbreviations_all", {}),
            "article_number": article.get("article_number"),
            "article_title": article.get("article_title"),
            "hierarchy_path": article.get("hierarchy_path"),
            "part": article.get("part"),
            "title": article.get("title"),
            "chapter": article.get("chapter"),
            "section": article.get("section"),
            "law_type": article.get("law_type"),
            "domain": article.get("domain"),
            "subdomain": article.get("subdomain"),
            "language": article.get("language"),
            "source": article.get("source", "fedlex"),
            "is_partial": article.get("is_partial", False),
            "paragraph_number": article.get("paragraph_number"),
            "text_preview": text_preview,
            "article_text": article.get("article_text")  # Keep full text for retrieval
        }

    def _build_decision_payload(self, chunk: dict) -> dict:
        """Build Qdrant payload for decision chunk."""
        decision = chunk.get("decision", {})
        metadata = decision.get("metadata", {})
        citations = metadata.get("citations", {})

        court = decision.get("court", "")
        source = "ticino" if court.startswith("CH_TI") or "TI_" in decision.get("id", "") else "federal"

        text = chunk.get("text", "")
        text_preview = ' '.join(text.split())[:200]
        if len(text) > 200:
            text_preview += "..."

        return {
            "doc_id": chunk.get("decision_id"),
            "id": chunk.get("chunk_id"),
            "decision_id": chunk.get("decision_id"),
            "chunk_type": chunk.get("chunk_type"),
            "chunk_index": chunk.get("chunk_index"),
            "court": court,
            "date": decision.get("date"),
            "year": decision.get("year"),
            "language": decision.get("language"),
            "outcome": decision.get("outcome"),
            "judges": metadata.get("judges", []),
            "citations_laws": citations.get("laws", []) if isinstance(citations, dict) else [],
            "citations_cases": citations.get("cases", []) if isinstance(citations, dict) else [],
            "lower_court": metadata.get("lower_court"),
            "source": source,
            "text_preview": text_preview
        }

    def _write_batch(self, output_dir: Path, batch: List[Dict], batch_num: int):
        """Write embedding batch to file."""
        import orjson

        output_file = output_dir / f"embeddings_{batch_num:05d}.json"
        with open(output_file, 'wb') as f:
            f.write(orjson.dumps(batch))


# ============================================
# Upload/Download Functions (run locally)
# ============================================
@app.local_entrypoint()
def main(
    upload: bool = False,
    embed: bool = False,
    download: bool = False,
    collection: str = "all",
    batch_size: int = 64
):
    """
    Main entrypoint for Modal CLI.

    Args:
        upload: Upload parsed data to Modal volume
        embed: Run GPU embedding
        download: Download embeddings from Modal
        collection: 'codex', 'library', or 'all'
    """
    if upload:
        upload_data()

    if embed:
        run_embedding(collection, batch_size)

    if download:
        download_embeddings(collection)

    if not (upload or embed or download):
        print("Usage:")
        print("  modal run scripts/modal_embed.py --upload")
        print("  modal run scripts/modal_embed.py --embed")
        print("  modal run scripts/modal_embed.py --embed --collection=codex")
        print("  modal run scripts/modal_embed.py --download")


def upload_data():
    """Upload parsed JSON data to Modal volume."""
    import subprocess

    project_root = Path(__file__).parent.parent
    parsed_dir = project_root / "data" / "parsed"

    if not parsed_dir.exists():
        print(f"Error: {parsed_dir} does not exist")
        print("Make sure you have parsed data locally, or run this on the Infomaniak server")
        return

    print("Uploading parsed data to Modal volume...")
    print("This may take a while for large datasets...")

    # Use modal volume put command
    # This uploads the entire parsed directory
    cmd = f"modal volume put kerberus-data {parsed_dir} /parsed"
    print(f"Running: {cmd}")
    subprocess.run(cmd.split(), check=True)

    print("Upload complete!")


def run_embedding(collection: str, batch_size: int):
    """Run GPU embedding on Modal."""
    embedder = BGEEmbedder()

    collections_to_process = []
    if collection == "all":
        collections_to_process = ["codex", "library"]
    else:
        collections_to_process = [collection]

    total_stats = {"processed": 0, "embedded": 0, "errors": 0, "files_written": 0}

    for coll in collections_to_process:
        print(f"\n{'='*60}")
        print(f"Processing collection: {coll}")
        print(f"{'='*60}")

        stats = embedder.process_collection.remote(coll, batch_size)

        print(f"\n{coll} stats:")
        for k, v in stats.items():
            print(f"  {k}: {v}")
            total_stats[k] += v

    print(f"\n{'='*60}")
    print("TOTAL STATS")
    print(f"{'='*60}")
    for k, v in total_stats.items():
        print(f"  {k}: {v}")


def download_embeddings(collection: str):
    """Download embeddings from Modal volume."""
    import subprocess

    project_root = Path(__file__).parent.parent
    output_dir = project_root / "data" / "embeddings"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Downloading embeddings to {output_dir}...")

    if collection == "all":
        cmd = f"modal volume get kerberus-data /embeddings {output_dir}"
    else:
        cmd = f"modal volume get kerberus-data /embeddings/{collection} {output_dir}/{collection}"

    print(f"Running: {cmd}")
    subprocess.run(cmd.split(), check=True)

    print("Download complete!")
    print(f"Embeddings saved to: {output_dir}")


# ============================================
# Import Script (runs on Infomaniak)
# ============================================
def import_embeddings_to_qdrant(collection: str = "all"):
    """
    Import downloaded embeddings into local Qdrant.

    Run this on the Infomaniak server after downloading embeddings:
        python scripts/modal_embed.py --import-local
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from src.database.vector_db import QdrantManager
    import orjson
    from tqdm import tqdm

    project_root = Path(__file__).parent.parent
    embeddings_dir = project_root / "data" / "embeddings"

    qdrant = QdrantManager()

    collections_to_import = []
    if collection == "all":
        collections_to_import = ["codex", "library"]
    else:
        collections_to_import = [collection]

    for coll in collections_to_import:
        print(f"\n{'='*60}")
        print(f"Importing {coll} to Qdrant...")
        print(f"{'='*60}")

        # Ensure collection exists
        qdrant.create_collection(coll, vector_size=1024)

        # Find all embedding files
        if coll == "codex":
            coll_dir = embeddings_dir / "codex"
        else:
            coll_dir = embeddings_dir / "library"

        if not coll_dir.exists():
            print(f"No embeddings found at {coll_dir}")
            continue

        # Process all subdirectories and files
        json_files = list(coll_dir.rglob("embeddings_*.json"))
        print(f"Found {len(json_files)} embedding files")

        total_imported = 0
        for json_file in tqdm(json_files, desc=f"Importing {coll}"):
            try:
                with open(json_file, 'rb') as f:
                    points = orjson.loads(f.read())

                qdrant.upsert_points(coll, points)
                total_imported += len(points)

            except Exception as e:
                print(f"Error importing {json_file}: {e}")

        print(f"Imported {total_imported} points to {coll}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--import-local", action="store_true", help="Import embeddings to local Qdrant")
    parser.add_argument("--collection", default="all", help="Collection to import")
    args = parser.parse_args()

    if args.import_local:
        import_embeddings_to_qdrant(args.collection)
