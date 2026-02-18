# Codex (Fedlex) Embedding Pipeline

This document describes the embedding pipeline for Swiss federal laws and ordinances (Codex).

## Overview

The codex collection contains Swiss federal laws scraped from Fedlex. It needs to be **refreshed weekly** to keep laws up to date:

1. Delete existing codex embeddings
2. Run Fedlex scraper
3. Generate embeddings (using Modal or local GPU)
4. Import to Qdrant

## Pipeline Options

### Option 1: Modal (Recommended for most users)

Uses Modal's A10G GPUs (~$0.50/hr). Best for users without local GPU.

```bash
# 1. Scrape fresh Fedlex data
python scripts/scrapers/fedlex_scraper.py

# 2. Upload scraped data to Modal
modal run scripts/modal_embed.py --upload

# 3. Generate embeddings on Modal GPU
modal run scripts/modal_embed.py --embed --collection=codex

# 4. Download embeddings locally
modal run scripts/modal_embed.py --download

# 5. Import to Qdrant (run on server)
QDRANT_HOST=127.0.0.1 python scripts/import_embeddings_local.py --collection codex
```

**Cost estimate:** ~$2-5 for codex (depending on article count)

### Option 2: Local GPU

For users with NVIDIA GPUs (requires 8GB+ VRAM, 16GB+ recommended).

```bash
# 1. Scrape fresh Fedlex data
python scripts/scrapers/fedlex_scraper.py

# 2. Generate embeddings locally
python scripts/embed_local.py --collection codex --device cuda

# 3. Import to Qdrant
QDRANT_HOST=localhost python scripts/import_embeddings_local.py --collection codex
```

**Requirements:**
- NVIDIA GPU with 8GB+ VRAM (16GB+ recommended)
- CUDA toolkit installed
- ~30GB disk space for model and embeddings

## Weekly Refresh Script

For automated weekly refresh, use the provided script:

```bash
# Full pipeline (Modal version)
./scripts/refresh_codex.sh --modal

# Full pipeline (Local GPU version)
./scripts/refresh_codex.sh --local
```

## Data Flow

```
Fedlex Website
    │
    ▼
fedlex_scraper.py
    │
    ▼
data/parsed/fedlex/*.json (parsed articles)
    │
    ▼
modal_embed.py OR embed_local.py
    │
    ▼
data/embeddings/codex/*.json (BGE-M3 embeddings)
    │
    ▼
import_embeddings_local.py
    │
    ▼
Qdrant (codex collection)
```

## Embedding Model

We use **BGE-M3** for embeddings:
- Dense vectors: 1024 dimensions (semantic similarity)
- Sparse vectors: Lexical matching (keyword search)
- Multilingual: German, French, Italian, Romansh support

## File Formats

### Parsed Article (input)
```json
{
  "id": "SR-101-art-1",
  "title": "Bundesverfassung Art. 1",
  "text": "Die Schweizerische Eidgenossenschaft...",
  "language": "de",
  "sr_number": "101",
  "article_number": "1",
  "law_title": "Bundesverfassung der Schweizerischen Eidgenossenschaft",
  "enacted_date": "1999-04-18",
  "source": "fedlex"
}
```

### Embedding (output)
```json
{
  "id": "SR-101-art-1_chunk_0",
  "vector": {
    "dense": [0.123, -0.456, ...],  // 1024 floats
    "sparse": {"12345": 0.89, "67890": 0.45, ...}  // token_id: weight
  },
  "payload": {
    "doc_id": "SR-101-art-1",
    "chunk_index": 0,
    "language": "de",
    "sr_number": "101",
    "law_title": "Bundesverfassung",
    "text_preview": "Die Schweizerische Eidgenossenschaft..."
  }
}
```

## Troubleshooting

### Out of Memory (Local GPU)
Reduce batch size in `embed_local.py`:
```bash
python scripts/embed_local.py --collection codex --batch-size 8
```

### Qdrant Connection Issues
Ensure Qdrant is running and the port is accessible:
```bash
curl http://localhost:6333/collections
```

### Modal Timeout
Large embedding jobs may timeout. Use the `--resume` flag:
```bash
modal run scripts/modal_embed.py --embed --collection=codex --resume
```
