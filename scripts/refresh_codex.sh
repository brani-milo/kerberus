#!/bin/bash
#
# Codex (Fedlex) Weekly Refresh Script
#
# Deletes existing codex collection and regenerates embeddings from fresh scrape.
# Run weekly to keep laws up to date.
#
# Usage:
#   ./scripts/refresh_codex.sh --modal    # Use Modal GPUs (recommended)
#   ./scripts/refresh_codex.sh --local    # Use local GPU
#   ./scripts/refresh_codex.sh --help     # Show help
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    echo "Usage: $0 [--modal|--local] [--skip-scrape] [--skip-delete]"
    echo ""
    echo "Options:"
    echo "  --modal       Use Modal cloud GPUs for embedding (default)"
    echo "  --local       Use local GPU for embedding"
    echo "  --skip-scrape Skip the scraping step (use existing data)"
    echo "  --skip-delete Don't delete existing collection first"
    echo "  --help        Show this help message"
    exit 1
}

# Parse arguments
USE_MODAL=true
SKIP_SCRAPE=false
SKIP_DELETE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --modal)
            USE_MODAL=true
            shift
            ;;
        --local)
            USE_MODAL=false
            shift
            ;;
        --skip-scrape)
            SKIP_SCRAPE=true
            shift
            ;;
        --skip-delete)
            SKIP_DELETE=true
            shift
            ;;
        --help|-h)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

cd "$PROJECT_ROOT"

echo -e "${GREEN}=== Codex Refresh Pipeline ===${NC}"
echo "Mode: $([ "$USE_MODAL" = true ] && echo "Modal (cloud GPU)" || echo "Local GPU")"
echo ""

# Step 1: Delete existing codex collection
if [ "$SKIP_DELETE" = false ]; then
    echo -e "${YELLOW}Step 1: Deleting existing codex collection...${NC}"

    QDRANT_HOST="${QDRANT_HOST:-localhost}"
    curl -s -X DELETE "http://${QDRANT_HOST}:6333/collections/codex" > /dev/null 2>&1 || true
    echo "  Codex collection deleted (or didn't exist)"

    # Also clean up old embedding files
    rm -rf data/embeddings/codex/*
    echo "  Old embedding files cleaned"
else
    echo -e "${YELLOW}Step 1: Skipping collection delete${NC}"
fi

# Step 2: Scrape fresh Fedlex data
if [ "$SKIP_SCRAPE" = false ]; then
    echo -e "${YELLOW}Step 2: Scraping fresh Fedlex data...${NC}"

    # Activate virtual environment if exists
    if [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
    elif [ -f "scraper_env/bin/activate" ]; then
        source scraper_env/bin/activate
    fi

    python scripts/scrapers/fedlex_scraper.py
    echo "  Fedlex scraping complete"
else
    echo -e "${YELLOW}Step 2: Skipping scrape (using existing data)${NC}"
fi

# Step 3: Generate embeddings
echo -e "${YELLOW}Step 3: Generating embeddings...${NC}"

if [ "$USE_MODAL" = true ]; then
    # Modal path
    echo "  Uploading data to Modal..."
    modal run scripts/modal_embed.py --upload

    echo "  Running embedding on Modal GPU..."
    modal run scripts/modal_embed.py --embed --collection=codex

    echo "  Downloading embeddings..."
    modal run scripts/modal_embed.py --download
else
    # Local GPU path
    echo "  Running embedding on local GPU..."
    python scripts/embed_local.py --collection codex
fi

echo "  Embeddings generated"

# Step 4: Import to Qdrant
echo -e "${YELLOW}Step 4: Importing to Qdrant...${NC}"

QDRANT_HOST="${QDRANT_HOST:-localhost}" python scripts/import_embeddings_local.py --collection codex

echo ""
echo -e "${GREEN}=== Codex Refresh Complete ===${NC}"
echo "New embeddings are now available in Qdrant."
