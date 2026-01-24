# KERBERUS - Swiss Legal Intelligence Platform

**Sovereign, Secure, Swiss**

KERBERUS is a legal AI platform designed for Swiss lawyers and fiduciaries, featuring zero-knowledge encryption, dynamic context management, and trilingual support (DE/FR/IT).

## Architecture

### Three-Tier Data Storage Model

```
┌─────────────────────────────────────────────────────────────────────┐
│                         KERBERUS DATA ARCHITECTURE                  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐     │
│  │   PostgreSQL    │  │    SQLCipher    │  │     Qdrant      │     │
│  │  (Auth Layer)   │  │ (Dossier Layer) │  │ (Vector Layer)  │     │
│  ├─────────────────┤  ├─────────────────┤  ├─────────────────┤     │
│  │ • User accounts │  │ • user_{uuid}.db│  │ • codex         │     │
│  │ • Sessions      │  │ • firm_{uuid}.db│  │ • library       │     │
│  │ • Firm members  │  │                 │  │ • dossier_*     │     │
│  │ • Token usage   │  │ AES-256-GCM     │  │                 │     │
│  │                 │  │ Zero-Knowledge  │  │ 768-dim vectors │     │
│  │ NOT ENCRYPTED   │  │ ENCRYPTED       │  │ Namespaced      │     │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘     │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

1. **PostgreSQL** - User authentication and metadata
   - User accounts, sessions, firm memberships
   - NOT encrypted (contains no sensitive legal content)
   - Tracks token usage and costs for monitoring

2. **SQLCipher** - Encrypted document storage
   - Per-user databases: `user_{uuid}.db` (zero-knowledge encrypted)
   - Per-firm databases: `firm_{uuid}.db` (master key encrypted)
   - AES-256 encryption with password-derived keys
   - Zero-knowledge: we cannot decrypt user data

3. **Qdrant** - Vector embeddings for semantic search
   - Collection per user: `dossier_user_{uuid}`
   - Collection per firm: `dossier_firm_{uuid}`
   - Vectors alone reveal nothing (meaningless without content)

### Dynamic Context Swapping

KERBERUS uses "The Sliding Window of Truth" pattern to prevent token bloat:

**The Problem:**
Traditional RAG systems accumulate context, causing token costs to explode:
```
Turn 1:  5,000 tokens
Turn 5:  25,000 tokens
Turn 10: 50,000 tokens  <- 10x cost!
```

**The Solution:**
We split AI memory into two buckets:

```
┌───────────────────────────────────────────────────────────────────┐
│                    CONTEXT MANAGEMENT                             │
├───────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────────────────┐  ┌─────────────────────────────────┐│
│  │  BUCKET A: Chat History │  │  BUCKET B: Legal Context        ││
│  │  (Preserved)            │  │  (Replaced Each Turn)           ││
│  ├─────────────────────────┤  ├─────────────────────────────────┤│
│  │ • Last 5 turns          │  │ • Fresh law articles            ││
│  │ • ~500 tokens           │  │ • Relevant case law             ││
│  │ • Conversation flow     │  │ • User's past work (style)      ││
│  │                         │  │ • ~4,000 tokens                 ││
│  └─────────────────────────┘  └─────────────────────────────────┘│
│                                                                   │
│  Result: ~5,000 tokens per turn (FLAT, regardless of length)     │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
```

- **Bucket A (Chat History):** Keep conversation flow (small, ~500 tokens)
- **Bucket B (Legal Context):** Replace on every turn (large, ~4,000 tokens)

**Result:**
- Natural conversation (AI remembers what you discussed)
- Flat token costs (~5,000 tokens per turn regardless of conversation length)
- Fresh, relevant context on every query (no stale documents)

### Data Flow

```
User uploads document
         │
         ▼
┌─────────────────────┐
│  PII Scrubbing      │  Swiss-specific patterns
│  (Presidio)         │  (AHV, IBAN, CHE-UID, plates)
└─────────────────────┘
         │
         ▼
┌─────────────────────┐
│  SQLCipher          │  Encrypted with user's
│  (Zero-Knowledge)   │  password-derived key
└─────────────────────┘
         │
         ▼
┌─────────────────────┐
│  BGE-M3 Embedding   │  768-dimensional vector
│  (Local MPS)        │  Multilingual DE/FR/IT
└─────────────────────┘
         │
         ▼
┌─────────────────────┐
│  Qdrant Vector DB   │  Fast semantic search
│  (Namespaced)       │  User isolation
└─────────────────────┘
```

### Search Flow (Triad Search with Context Swapping)

```
User query: "Can I terminate for cause?"
                    │
    ┌───────────────┼───────────────┐
    ▼               ▼               ▼
┌────────┐    ┌──────────┐    ┌──────────┐
│ Lane 1 │    │  Lane 2  │    │  Lane 3  │
│ Codex  │    │ Library  │    │ Dossier  │
│(Laws)  │    │(Cases)   │    │(User)    │
└────────┘    └──────────┘    └──────────┘
    │               │               │
    └───────────────┼───────────────┘
                    ▼
         ┌─────────────────────┐
         │  Reranker BGE-v2    │
         │  Score & merge      │
         └─────────────────────┘
                    │
                    ▼
         ┌─────────────────────┐
         │  CONTEXT SWAP       │
         │  Discard old Bucket B│
         │  Insert fresh docs  │
         └─────────────────────┘
                    │
                    ▼
         ┌─────────────────────┐
         │  Qwen3-VL API       │
         │  (Infomaniak Swiss) │
         └─────────────────────┘
                    │
                    ▼
         Response: Legally compliant,
         case-aligned, style-matched
```

## Quick Start

### Prerequisites

- macOS with Apple Silicon (M1/M2/M3)
- Docker Desktop for Mac
- Python 3.11+
- 16GB RAM recommended

### Installation

```bash
# 1. Clone repository
git clone <repository-url>
cd kerberus

# 2. Setup environment
make setup

# 3. Edit .env file (set your passwords, keys, etc.)
nano .env

# 4. Start services
make start

# 5. Initialize databases
make db-init

# 6. Activate Python environment
source venv/bin/activate

# 7. Ready to develop!
```

### Verify Installation

```bash
# Check all services are running
docker ps

# Should show: kerberus-qdrant, kerberus-redis, kerberus-postgres

# Access Qdrant dashboard
open http://localhost:6333/dashboard

# Test database connections
make test-quick
```

## Development Workflow

### Running Tests

```bash
# All tests with coverage
make test

# Quick tests (no coverage)
make test-quick

# Test SQLCipher encryption
make test-sqlcipher
```

### Viewing Logs

```bash
# All services
make logs

# Specific service
make logs-qdrant
make logs-postgres
make logs-redis
```

### Database Management

```bash
# Open PostgreSQL shell
make db-shell

# Open Redis CLI
make redis-cli

# Run migrations
make db-migrate
```

## Project Structure

```
kerberus/
├── src/
│   ├── database/           # Database connection managers
│   │   ├── auth_db.py         # PostgreSQL (auth/metadata)
│   │   ├── dossier_db.py      # SQLCipher (encrypted docs)
│   │   └── vector_db.py       # Qdrant (vectors)
│   ├── embedder/           # BGE-M3 embedding generation
│   ├── reranker/           # BGE-Reranker-v2-M3
│   ├── search/             # Triad search implementation
│   ├── scrapers/           # Web scrapers (Entscheidsuche, Fedlex)
│   ├── parsers/            # Legal document parsers
│   ├── security/           # PII scrubbing, encryption
│   ├── auth/               # Authentication, MFA, rate limiting
│   ├── ai/                 # Qwen3-VL integration, context management
│   │   ├── prompt_builder.py      # Hybrid prompt system
│   │   └── conversation_manager.py # Dynamic context swapping
│   ├── validation/         # Citation validator, JCD
│   ├── ui/                 # Chainlit interface
│   └── utils/              # Shared utilities
├── tests/                  # Test suite
├── data/                   # Data storage (gitignored)
├── config/                 # Configuration files
├── logs/                   # Application logs (gitignored)
├── scripts/                # Utility scripts
└── assets/                 # Static assets
```

## Security Features

- **Zero-Knowledge Encryption**: We cannot decrypt user dossiers
- **Swiss PII Scrubbing**: Removes Swiss-specific PII (postal codes, IBANs, CHE-UIDs, license plates)
- **MFA Required**: TOTP-based two-factor authentication
- **Rate Limiting**: 300 queries/day, 50/hour (anti-abuse)
- **Token Cost Tracking**: Monitor and alert on excessive usage
- **Audit Logging**: All access tracked (compliance)

## Cost Monitoring

KERBERUS tracks token usage in real-time:

```sql
-- Token usage is logged to PostgreSQL
SELECT
    DATE(timestamp) as date,
    COUNT(*) as queries,
    SUM(total_tokens) as tokens,
    SUM(total_cost_chf) as cost_chf
FROM token_usage
WHERE user_id = 'user-uuid'
GROUP BY DATE(timestamp)
ORDER BY date DESC;
```

**Cost Alerts:**
- User exceeds CHF 50/month -> Email notification
- System-wide costs spike -> Admin dashboard alert
- Context swapping ensures predictable costs (~CHF 0.0057/query)

## Data Ingestion

### Ticino Court Scraper

Scrapes cantonal court decisions from entscheidsuche.ch.

**Usage:**
```bash
# Incremental update (daily)
python scripts/scrape_ticino.py

# Full re-scrape (first run or reset)
python scripts/scrape_ticino.py --full

# Specific year (for testing)
python scripts/scrape_ticino.py --year 1993

# Verbose logging
python scripts/scrape_ticino.py --verbose
```

**Scheduling (cron):**
```bash
# Daily at 2am (production on Infomaniak)
0 2 * * * cd /path/to/kerberus && python scripts/scrape_ticino.py >> logs/scrapers/cron.log 2>&1
```

**State tracking:**
- State saved to: `data/state/ticino_scraper.json`
- Tracks last run date and file counts
- Enables efficient incremental updates

## Swiss Legal Coverage

- **Federal Law**: OR, ZGB, StGB (DE/FR/IT)
- **Cantonal Law**: Ticino (with expansion planned)
- **Case Law**: Federal Supreme Court + Cantonal courts
- **Multilingual**: Native DE/FR/IT support with Swiss-German dialect understanding

## Troubleshooting

### Docker Services Won't Start

```bash
# Check Docker Desktop is running
docker ps

# If services are unhealthy, check logs
make logs

# Reset everything (destroys data)
make clean && make setup && make start
```

### Python Import Errors

```bash
# Ensure virtual environment is activated
source venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt
```

### SQLCipher Errors

```bash
# Test encryption separately
make test-sqlcipher

# Check pysqlcipher3 is installed
pip list | grep sqlcipher
```

### High Token Costs

```bash
# Check token usage logs
cat logs/token_usage.jsonl | tail -100

# Verify context swapping is enabled
grep ENABLE_CONTEXT_SWAPPING .env

# Should show: ENABLE_CONTEXT_SWAPPING=true
```

## License

Proprietary - All Rights Reserved
