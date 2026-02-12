# Modal GPU Embedding Guide

Run BGE-M3 embedding on Modal's serverless GPUs for ~$2-3 total.

## Prerequisites

- Modal account (free $30 credits): https://modal.com
- SSH access to Infomaniak server

## Quick Start (from Infomaniak Server)

### 1. Install Modal on Infomaniak

```bash
ssh -i ~/Downloads/unt.txt ubuntu@84.234.29.217

# Install Modal CLI
pip install modal

# Login to Modal (opens browser on your local machine)
modal setup
# If no browser available, use: modal token new
```

### 2. Upload Parsed Data to Modal

```bash
cd /home/ubuntu/kerberus

# Upload parsed JSONs (~1-2GB)
modal volume put kerberus-data ./data/parsed /parsed
```

### 3. Run GPU Embedding

```bash
# Embed all collections (codex + library)
modal run scripts/modal_embed.py --embed

# Or embed one at a time
modal run scripts/modal_embed.py --embed --collection=codex    # Fedlex laws
modal run scripts/modal_embed.py --embed --collection=library  # Decisions
```

**Estimated time:**
- Codex (laws): ~30 min
- Library (385k decisions): ~2-4 hours
- Total cost: ~$2-3

### 4. Download Embeddings

```bash
# Download to Infomaniak
modal volume get kerberus-data /embeddings ./data/embeddings
```

### 5. Import to Qdrant

```bash
# Run inside Docker container (where Qdrant is accessible)
docker exec -it kerberus-app python scripts/modal_embed.py --import-local
```

## Alternative: Direct Qdrant Connection

If you want Modal to write directly to Qdrant:

### 1. Temporarily Expose Qdrant

Add to docker-compose.prod.yml:
```yaml
qdrant:
  ports:
    - "6333:6333"  # Temporary!
```

Then:
```bash
docker stack deploy -c docker-compose.prod.yml kerberus
```

### 2. Set Qdrant URL in Modal

The script would need modification to connect to:
`http://84.234.29.217:6333`

### 3. Close Port After

Remove the port mapping and redeploy.

## Monitoring Progress

```bash
# Check Modal logs
modal app logs kerberus-embedder

# Check volume contents
modal volume ls kerberus-data /embeddings
```

## Troubleshooting

### "Volume not found"
```bash
modal volume create kerberus-data
```

### "CUDA out of memory"
Reduce batch size:
```bash
modal run scripts/modal_embed.py --embed --batch-size=32
```

### "Connection refused to Qdrant"
Make sure you're running --import-local inside the Docker container or expose Qdrant port.

## Cost Breakdown

| Resource | Rate | Time | Cost |
|----------|------|------|------|
| A10G GPU | $0.50/hr | ~4hr | ~$2.00 |
| Volume | $0.30/GB/mo | 5GB | ~$0.05 |
| **Total** | | | **~$2.05** |

Free tier includes $30 credits, more than enough.
