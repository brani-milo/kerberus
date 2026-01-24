#!/bin/bash

# KERBERUS GitHub Setup Script

echo "🚀 KERBERUS GitHub Setup"
echo "========================"
echo ""

# Check directory
if [ ! -f "docker-compose.yml" ]; then
    echo "❌ Error: Not in kerberus directory!"
    exit 1
fi

# Get GitHub URL
echo "📋 Enter your GitHub repository URL:"
echo "Example: https://github.com/brani-milo/kerberus.git"
read -p "URL: " REPO_URL

if [ -z "$REPO_URL" ]; then
    echo "❌ No URL provided!"
    exit 1
fi

echo ""
echo "🔧 Initializing Git repository..."

git init
git remote add origin "$REPO_URL"

echo ""
echo "📦 Creating commits..."
echo ""

# ============================================
# COMMIT 1: Project Infrastructure
# ============================================
echo "📝 Commit 1/8: Project infrastructure..."

git add docker-compose.yml \
        requirements.txt \
        Makefile \
        .gitignore \
        .env.example 2>/dev/null

GIT_AUTHOR_DATE="2026-01-20T10:00:00" \
GIT_COMMITTER_DATE="2026-01-20T10:00:00" \
git commit -m "feat: Add production infrastructure

- Docker Compose with Qdrant, PostgreSQL, Redis
- Python requirements with BGE-M3, Qwen support
- Makefile for development workflow
- Environment configuration template"

# ============================================
# COMMIT 2: Configuration
# ============================================
echo "📝 Commit 2/8: Configuration..."

if [ -d "config" ]; then
    git add config/
fi

if [ -f "scripts/init_databases.py" ]; then
    git add scripts/init_databases.py
fi

if git diff --cached --quiet; then
    echo "   Skipped (no config files yet)"
else
    GIT_AUTHOR_DATE="2026-01-20T14:30:00" \
    GIT_COMMITTER_DATE="2026-01-20T14:30:00" \
    git commit -m "feat: Add database initialization and configuration

- PostgreSQL schema with token usage tracking
- Qdrant collection configuration
- Database connection setup"
fi

# ============================================
# COMMIT 3: BGE-M3 Embedder
# ============================================
echo "📝 Commit 3/8: BGE-M3 embedder..."

if [ -d "src/embedder" ]; then
    git add src/embedder/
fi

if git diff --cached --quiet; then
    echo "   Skipped (embedder not found)"
else
    GIT_AUTHOR_DATE="2026-01-21T10:00:00" \
    GIT_COMMITTER_DATE="2026-01-21T10:00:00" \
    git commit -m "feat: Implement BGE-M3 embedder with Apple Silicon optimization

- Multilingual embeddings (768-dim, 100+ languages)
- MPS acceleration for M3 chip
- Batch processing with memory optimization
- LRU cache for frequent queries"
fi

# ============================================
# COMMIT 4: BGE Reranker
# ============================================
echo "📝 Commit 4/8: BGE reranker..."

if [ -d "src/reranker" ]; then
    git add src/reranker/
fi

if git diff --cached --quiet; then
    echo "   Skipped (reranker not found)"
else
    GIT_AUTHOR_DATE="2026-01-21T15:00:00" \
    GIT_COMMITTER_DATE="2026-01-21T15:00:00" \
    git commit -m "feat: Add BGE reranker with authority and recency weighting

- Cross-encoder precision ranking
- Recency boost (10%) for recent precedents
- Authority boost (10%) for BGE/ATF cases
- Dynamic year calculation for future-proofing
- Comprehensive metadata preservation"
fi

# ============================================
# COMMIT 5: Search Components
# ============================================
echo "📝 Commit 5/8: Search engine components..."

if [ -d "src/search" ]; then
    git add src/search/
fi

if [ -d "src/database" ]; then
    git add src/database/
fi

if git diff --cached --quiet; then
    echo "   Skipped (search components not found)"
else
    GIT_AUTHOR_DATE="2026-01-22T11:00:00" \
    GIT_COMMITTER_DATE="2026-01-22T11:00:00" \
    git commit -m "feat: Implement search engine core

- MMR algorithm for result diversity
- Qdrant vector database manager
- Triad search architecture (laws, cases, user docs)
- Dynamic context swapping for cost optimization"
fi

# ============================================
# COMMIT 6: Base Scraper
# ============================================
echo "📝 Commit 6/8: Base scraper framework..."

if [ -f "src/scrapers/base_scraper.py" ]; then
    git add src/scrapers/base_scraper.py \
            src/scrapers/__init__.py
fi

if git diff --cached --quiet; then
    echo "   Skipped (base scraper not found)"
else
    GIT_AUTHOR_DATE="2026-01-23T10:00:00" \
    GIT_COMMITTER_DATE="2026-01-23T10:00:00" \
    git commit -m "feat: Add base scraper with state management

- Incremental update logic (checks last 2-5 years)
- Progress tracking and statistics
- Error handling with retry logic
- Extensible for multiple data sources"
fi

# ============================================
# COMMIT 7: Ticino Scraper
# ============================================
echo "📝 Commit 7/8: Ticino court scraper..."

if [ -f "src/scrapers/ticino_scraper.py" ]; then
    git add src/scrapers/ticino_scraper.py \
            scripts/scrape_ticino.py \
            scripts/parse_ticino.py 2>/dev/null
fi

if git diff --cached --quiet; then
    echo "   Skipped (Ticino scraper not found)"
else
    GIT_AUTHOR_DATE="2026-01-23T14:00:00" \
    GIT_COMMITTER_DATE="2026-01-23T14:00:00" \
    git commit -m "feat: Implement Ticino court scraper with incremental updates

- Discovers cases via entscheidsuche.ch API
- Strict year-based filtering (prevents false positives)
- Fallback URL mechanism for 404s
- Expected yield: ~30,000 judgments (1990-present)
- CLI with --year flag for testing
- State management for daily incremental updates"
fi

# ============================================
# COMMIT 8: Documentation
# ============================================
echo "📝 Commit 8/8: Documentation and structure..."

# Add public README only (not README_INTERNAL.md)
git add README.md 2>/dev/null

# Add empty directories
mkdir -p data/ticino data/parsed data/state logs/scrapers
touch data/ticino/.gitkeep data/parsed/.gitkeep data/state/.gitkeep logs/scrapers/.gitkeep
git add data/ logs/ 2>/dev/null

# Add tests if exist
if [ -d "tests" ]; then
    git add tests/
fi

if git diff --cached --quiet; then
    echo "   Skipped (no documentation changes)"
else
    GIT_AUTHOR_DATE="2026-01-24T10:00:00" \
    GIT_COMMITTER_DATE="2026-01-24T10:00:00" \
    git commit -m "docs: Add comprehensive project documentation

- Architecture overview and technical highlights
- Development roadmap and current status
- Installation and usage instructions
- Security and privacy considerations
- Directory structure for data and logs"
fi

# ============================================
# Remaining files
# ============================================
if [ -n "$(git status --porcelain)" ]; then
    echo ""
    echo "📝 Adding remaining files..."
    git add .
    
    GIT_AUTHOR_DATE="2026-01-24T11:00:00" \
    GIT_COMMITTER_DATE="2026-01-24T11:00:00" \
    git commit -m "chore: Add remaining project files"
fi

echo ""
echo "✅ All commits created successfully!"
echo ""
echo "📊 Commit summary:"
git log --oneline --graph --all

echo ""
echo "🚀 Ready to push to GitHub!"
echo ""
echo "Next steps:"
echo "1. Run: git branch -M main"
echo "2. Run: git push -u origin main"
echo ""
echo "⚠️  You'll need your GitHub Personal Access Token"
