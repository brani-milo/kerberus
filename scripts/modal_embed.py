#!/usr/bin/env python3
"""
Modal Serverless GPU Embedding for KERBERUS.

Runs BGE-M3 embedding on Modal's A10G GPUs (~$0.50/hr per GPU).
PARALLEL MODE: Spins up 8 GPUs simultaneously to process courts in parallel.
  - ~4-5 hours total instead of ~38 hours sequential
  - Cost: ~$20 (8 GPUs × ~$0.50/hr × ~5 hours)

Setup:
    1. pip install modal
    2. modal setup  # Login with GitHub/Google
    3. modal run scripts/modal_embed.py --upload  # Upload parsed data
    4. modal run scripts/modal_embed.py --embed   # Run embedding (parallel by default)
    5. modal run scripts/modal_embed.py --download  # Download results

Usage:
    modal run scripts/modal_embed.py --upload                      # Upload parsed JSONs
    modal run scripts/modal_embed.py --embed                       # Parallel (8 GPUs)
    modal run scripts/modal_embed.py --embed --sequential          # Single GPU
    modal run scripts/modal_embed.py --embed --collection=codex    # Just Fedlex
    modal run scripts/modal_embed.py --download                    # Download embeddings
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
        "torch>=2.6",  # Required for security (CVE-2025-32434)
        "transformers>=4.44,<4.50",  # Pin to stable 4.x for FlagEmbedding compat
        "FlagEmbedding==1.2.11",  # Specific stable version
        "peft",  # Required by FlagEmbedding
        "sentence-transformers",  # Required by FlagEmbedding
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
    timeout=3600 * 24,  # 24 hour max (resume capability allows restart if needed)
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
    def ensure_extracted(self) -> bool:
        """Ensure tar archives are extracted (called once before parallel processing)."""
        import subprocess

        extracted = False

        # Extract main parsed.tar.gz (court decisions)
        tar_path = Path("/data/parsed.tar.gz")
        parsed_dir = Path("/data/parsed")

        if tar_path.exists() and not parsed_dir.exists():
            print(f"Extracting {tar_path}...")
            subprocess.run(["tar", "-xzf", str(tar_path), "-C", "/data"], check=True)
            print("Extraction complete!")
            extracted = True

        # Extract Fedlex tar (laws/ordinances) - separate file for 309k articles
        # Volume mounted at /data, check multiple possible locations
        fedlex_tar_paths = [
            Path("/data/parsed_fedlex.tar.gz"),        # Volume root: /parsed_fedlex.tar.gz
            Path("/data/data/parsed_fedlex.tar.gz"),  # If uploaded to /data/ in volume
        ]
        fedlex_dir = parsed_dir / "fedlex"

        # Debug: list what exists
        print(f"Looking for Fedlex tar... fedlex_dir exists: {fedlex_dir.exists()}")
        if fedlex_dir.exists():
            json_count = len(list(fedlex_dir.glob("*.json")))
            print(f"  fedlex_dir has {json_count} JSON files")

        for fedlex_tar in fedlex_tar_paths:
            print(f"  Checking {fedlex_tar}: exists={fedlex_tar.exists()}")
            if fedlex_tar.exists() and (not fedlex_dir.exists() or len(list(fedlex_dir.glob("*.json"))) < 100):
                print(f"Extracting {fedlex_tar}...")
                # List what tar contains first
                result = subprocess.run(["tar", "-tzf", str(fedlex_tar)], capture_output=True, text=True)
                print(f"  Tar contents (first 5): {result.stdout.split(chr(10))[:5]}")
                # Extract to /data, creates /data/parsed/fedlex/
                subprocess.run(["tar", "-xzf", str(fedlex_tar), "-C", "/data"], check=True)
                print("Fedlex extraction complete!")
                # List what's in /data/parsed now
                result2 = subprocess.run(["ls", "-la", "/data/parsed/"], capture_output=True, text=True)
                print(f"  /data/parsed/ contents: {result2.stdout[:500]}")
                # Verify extraction
                print(f"  fedlex_dir after extract: {fedlex_dir}, exists={fedlex_dir.exists()}")
                if fedlex_dir.exists():
                    json_count = len(list(fedlex_dir.glob("*.json")))
                    print(f"  After extraction: {json_count} JSON files in {fedlex_dir}")
                extracted = True
                break

        if extracted:
            volume.commit()

        return parsed_dir.exists()

    @modal.method()
    def process_court(
        self,
        court: str,
        batch_size: int = 64,
        file_batch_size: int = 1000
    ) -> Dict:
        """
        Process a single court (for parallel processing).

        Args:
            court: Court identifier (e.g., 'CH_BGer', 'ticino', 'codex')
        """
        import orjson
        from tqdm import tqdm

        parsed_dir = Path("/data/parsed")

        stats = {"court": court, "processed": 0, "embedded": 0, "errors": 0, "files_written": 0}

        if court == "codex":
            # Fedlex
            input_dir = parsed_dir / "fedlex"
            output_dir = Path("/data/embeddings/codex")
            if input_dir.exists():
                stats = self._process_fedlex(input_dir, output_dir, batch_size, file_batch_size)
                stats["court"] = court
        elif court == "ticino":
            # Ticino cantonal court
            input_dir = parsed_dir / "ticino"
            output_dir = Path("/data/embeddings/library/ticino")
            if input_dir.exists():
                stats = self._process_decisions(input_dir, output_dir, batch_size, file_batch_size)
                stats["court"] = court
        else:
            # Federal courts
            input_dir = parsed_dir / "federal" / court
            output_dir = Path(f"/data/embeddings/library/{court}")
            if input_dir.exists():
                stats = self._process_decisions(input_dir, output_dir, batch_size, file_batch_size)
                stats["court"] = court

        # Commit after each court
        volume.commit()

        print(f"\n✓ {court} complete: {stats}")
        return stats

    @modal.method()
    def process_collection(
        self,
        collection: str,
        batch_size: int = 64,
        file_batch_size: int = 1000
    ) -> Dict:
        """
        Process entire collection (codex or library) - SEQUENTIAL version.
        Use process_court() with .map() for parallel processing.

        Reads from /data/parsed/{collection}/
        Writes to /data/embeddings/{collection}/
        """
        import orjson
        import tarfile
        import subprocess
        from tqdm import tqdm

        # Extract tar archive if it exists and parsed dir doesn't
        tar_path = Path("/data/parsed.tar.gz")
        parsed_dir = Path("/data/parsed")

        if tar_path.exists() and not parsed_dir.exists():
            print(f"Extracting {tar_path}...")
            subprocess.run(["tar", "-xzf", str(tar_path), "-C", "/data"], check=True)
            print("Extraction complete!")
            volume.commit()

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

        # Create output directory
        output_dir.mkdir(parents=True, exist_ok=True)

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
        """Process court decisions with resume capability."""
        import orjson
        from tqdm import tqdm
        import re

        stats = {"processed": 0, "embedded": 0, "errors": 0, "files_written": 0, "skipped": 0}
        output_dir.mkdir(parents=True, exist_ok=True)

        # Load checkpoint for fast resume
        checkpoint_file = output_dir / "checkpoint.json"
        start_file_idx = 0
        next_file_num = 0

        if checkpoint_file.exists():
            try:
                with open(checkpoint_file, 'rb') as f:
                    checkpoint = orjson.loads(f.read())
                start_file_idx = checkpoint.get("last_file_idx", 0) + 1
                next_file_num = checkpoint.get("next_file_num", 0)
                print(f"FAST RESUME: Skipping to file index {start_file_idx}, output file {next_file_num}")
            except Exception as e:
                print(f"Warning: Could not read checkpoint: {e}")

        stats["files_written"] = next_file_num

        json_files = sorted(input_dir.glob("*.json"))
        total_files = len(json_files)
        print(f"Found {total_files} decision files, starting from index {start_file_idx}")

        # Skip to checkpoint position
        json_files = json_files[start_file_idx:]

        output_batch = []

        for file_idx, json_file in enumerate(tqdm(json_files, desc="Processing", initial=start_file_idx, total=total_files), start=start_file_idx):
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

                    # Save checkpoint for fast resume
                    with open(checkpoint_file, 'wb') as f:
                        f.write(orjson.dumps({
                            "last_file_idx": file_idx,
                            "next_file_num": stats["files_written"]
                        }))

            except Exception as e:
                print(f"Error processing {json_file.name}: {e}")
                stats["errors"] += 1

        # Write remaining
        if output_batch:
            self._write_batch(output_dir, output_batch, stats["files_written"])
            stats["files_written"] += 1

        # Final checkpoint
        with open(checkpoint_file, 'wb') as f:
            f.write(orjson.dumps({
                "last_file_idx": total_files - 1,
                "next_file_num": stats["files_written"],
                "completed": True
            }))

        # Log summary
        print(f"Completed: processed {stats['processed']} decisions, "
              f"wrote {stats['files_written']} embedding files")

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

    def _load_processed_ids(self, output_dir: Path) -> tuple:
        """Load already-processed decision IDs from existing embedding files.

        Returns:
            tuple: (set of processed decision IDs, next file number to use)
        """
        import orjson

        processed_ids = set()
        max_file_num = -1

        if not output_dir.exists():
            return processed_ids, 0

        embedding_files = sorted(output_dir.glob("embeddings_*.json"))

        for emb_file in embedding_files:
            # Extract file number
            try:
                file_num = int(emb_file.stem.split("_")[1])
                max_file_num = max(max_file_num, file_num)
            except (IndexError, ValueError):
                continue

            # Load and extract decision IDs (without chunk suffix)
            try:
                with open(emb_file, 'rb') as f:
                    chunks = orjson.loads(f.read())
                for chunk in chunks:
                    chunk_id = chunk.get("id", "")
                    # Extract decision ID (remove _chunk_N suffix)
                    if "_chunk_" in chunk_id:
                        decision_id = chunk_id.rsplit("_chunk_", 1)[0]
                    else:
                        decision_id = chunk_id
                    processed_ids.add(decision_id)
            except Exception as e:
                print(f"Warning: Could not read {emb_file}: {e}")

        next_file_num = max_file_num + 1 if max_file_num >= 0 else 0
        return processed_ids, next_file_num

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
    batch_size: int = 64,
    parallel: bool = True,
    sequential: bool = False
):
    """
    Main entrypoint for Modal CLI.

    Args:
        upload: Upload parsed data to Modal volume
        embed: Run GPU embedding
        download: Download embeddings from Modal
        collection: 'codex', 'library', or 'all'
        parallel: Run with multiple GPUs in parallel (default: True)
        sequential: Force sequential processing (single GPU)
    """
    if upload:
        upload_data()

    if embed:
        use_parallel = parallel and not sequential
        run_embedding(collection, batch_size, parallel=use_parallel)

    if download:
        download_embeddings(collection)

    if not (upload or embed or download):
        print("Usage:")
        print("  modal run scripts/modal_embed.py --upload")
        print("  modal run scripts/modal_embed.py --embed                    # Parallel (default)")
        print("  modal run scripts/modal_embed.py --embed --sequential       # Single GPU")
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


def run_embedding(collection: str, batch_size: int, parallel: bool = True):
    """Run GPU embedding on Modal.

    Args:
        collection: 'codex', 'library', or 'all'
        batch_size: Embedding batch size
        parallel: If True, process courts in parallel (multiple GPUs)
    """
    embedder = BGEEmbedder()

    # First ensure data is extracted
    print("Ensuring data is extracted...")
    embedder.ensure_extracted.remote()

    if parallel:
        run_embedding_parallel(embedder, collection, batch_size)
    else:
        run_embedding_sequential(embedder, collection, batch_size)


def run_embedding_parallel(embedder, collection: str, batch_size: int):
    """Run embedding with multiple GPUs in parallel."""

    # Determine which courts to process
    courts_to_process = []

    # Support comma-separated court names (e.g., "CH_BGer,ticino")
    valid_courts = ["CH_BGer", "CH_BVGer", "ticino", "CH_BGE", "CH_BStGer", "CH_EDOEB", "CH_BPatG", "codex"]

    if "," in collection:
        # Comma-separated list of specific courts
        for court in collection.split(","):
            court = court.strip()
            if court in valid_courts:
                courts_to_process.append(court)
            else:
                print(f"Warning: Unknown court '{court}', skipping")
    elif collection in ["all", "codex"]:
        courts_to_process.append("codex")
        if collection == "all":
            courts_to_process.extend([
                "CH_BGer", "CH_BVGer", "ticino", "CH_BGE", "CH_BStGer", "CH_EDOEB", "CH_BPatG"
            ])
    elif collection == "library":
        # Federal courts + Ticino
        courts_to_process.extend([
            "CH_BGer",      # ~182k files - largest
            "CH_BVGer",     # ~91k files
            "ticino",       # ~57k files
            "CH_BGE",       # smaller
            "CH_BStGer",    # smaller
            "CH_EDOEB",     # smaller
            "CH_BPatG",     # smaller
        ])
    elif collection in valid_courts:
        # Single court specified
        courts_to_process.append(collection)

    print(f"\n{'='*60}")
    print(f"PARALLEL EMBEDDING - {len(courts_to_process)} workers")
    print(f"Courts: {', '.join(courts_to_process)}")
    print(f"{'='*60}\n")

    # Process all courts in parallel using .map()
    # This spawns one GPU container per court
    results = list(embedder.process_court.map(
        courts_to_process,
        kwargs={"batch_size": batch_size}
    ))

    # Aggregate stats
    total_stats = {"processed": 0, "embedded": 0, "errors": 0, "files_written": 0, "skipped": 0}

    print(f"\n{'='*60}")
    print("RESULTS BY COURT")
    print(f"{'='*60}")

    for stats in results:
        court = stats.get("court", "unknown")
        print(f"\n{court}:")
        for k, v in stats.items():
            if k != "court":
                print(f"  {k}: {v}")
                total_stats[k] = total_stats.get(k, 0) + v

    print(f"\n{'='*60}")
    print("TOTAL STATS")
    print(f"{'='*60}")
    for k, v in total_stats.items():
        print(f"  {k}: {v}")


def run_embedding_sequential(embedder, collection: str, batch_size: int):
    """Run embedding sequentially (single GPU, original behavior)."""
    collections_to_process = []
    if collection == "all":
        collections_to_process = ["codex", "library"]
    else:
        collections_to_process = [collection]

    total_stats = {"processed": 0, "embedded": 0, "errors": 0, "files_written": 0, "skipped": 0}

    for coll in collections_to_process:
        print(f"\n{'='*60}")
        print(f"Processing collection: {coll}")
        print(f"{'='*60}")

        stats = embedder.process_collection.remote(coll, batch_size)

        print(f"\n{coll} stats:")
        for k, v in stats.items():
            print(f"  {k}: {v}")
            total_stats[k] = total_stats.get(k, 0) + v

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
