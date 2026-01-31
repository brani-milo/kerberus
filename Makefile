# ============================================
# KERBERUS - Development Makefile
# ============================================
# Quick start: make setup && make start
# ============================================

.PHONY: setup start stop restart logs test clean init-dossier scrape-ticino scrape-ticino-full scrape-ticino-test scrape-federal scrape-federal-test scrape-fedlex scrape-fedlex-test build-abbrev-registry parse-federal parse-ticino parse-ticino-test parse-fedlex parse-fedlex-test embed-fedlex embed-fedlex-test embed-decisions embed-decisions-test embed-all embed-status help

# ============================================
# SETUP & INSTALLATION
# ============================================

setup: ## Initial project setup
	@echo "Setting up KERBERUS development environment..."
	python3 -m venv venv
	. venv/bin/activate && pip install --upgrade pip
	. venv/bin/activate && pip install -r requirements.txt
	cp .env.example .env
	$(MAKE) init-dossier
	@echo "Setup complete!"
	@echo "Next steps:"
	@echo "   1. Edit .env with your configuration"
	@echo "   2. Run 'make start' to start services"
	@echo "   3. Run 'source venv/bin/activate' to activate Python environment"

init-dossier: ## Initialize encrypted dossier directory
	@echo "Initializing dossier directory..."
	mkdir -p data/dossier
	chmod 700 data/dossier
	@echo "Dossier directory initialized with restricted permissions (700)"

# ============================================
# DOCKER SERVICES
# ============================================

start: ## Start all Docker services
	@echo "Starting KERBERUS services..."
	docker compose up -d
	@echo "Waiting for services to be healthy..."
	@sleep 5
	@echo "Services started!"
	@echo "   - Qdrant UI: http://localhost:6333/dashboard"
	@echo "   - PostgreSQL: localhost:5432"
	@echo "   - Redis: localhost:6379"

stop: ## Stop all Docker services
	@echo "Stopping services..."
	docker compose down
	@echo "Services stopped"

restart: ## Restart all Docker services
	@echo "Restarting services..."
	docker compose restart
	@echo "Services restarted"

logs: ## View logs from all services
	docker compose logs -f

logs-qdrant: ## View Qdrant logs
	docker compose logs -f qdrant

logs-postgres: ## View PostgreSQL logs
	docker compose logs -f postgres

logs-redis: ## View Redis logs
	docker docker composecompose logs -f redis

# ============================================
# TESTING
# ============================================

test: ## Run all tests with coverage
	@echo "Running tests..."
	. venv/bin/activate && pytest tests/ -v --cov=src --cov-report=html
	@echo "Tests complete. Coverage report: htmlcov/index.html"

test-quick: ## Run tests without coverage
	. venv/bin/activate && pytest tests/ -v

test-sqlcipher: ## Test SQLCipher encryption
	@echo "Testing SQLCipher encryption..."
	. venv/bin/activate && python scripts/test_encryption.py

# ============================================
# DATABASE MANAGEMENT
# ============================================

db-init: ## Initialize database schemas
	@echo "Initializing databases..."
	. venv/bin/activate && python scripts/init_databases.py
	@echo "Database schemas created"

db-migrate: ## Run database migrations
	. venv/bin/activate && alembic upgrade head

db-shell: ## Open PostgreSQL shell
	docker exec -it kerberus-postgres psql -U kerberus_user -d kerberus_dev

redis-cli: ## Open Redis CLI
	docker exec -it kerberus-redis redis-cli

# ============================================
# DATA INGESTION
# ============================================

scrape-ticino: ## Scrape Ticino court decisions (incremental)
	@echo "🔍 Scraping Ticino court decisions (incremental)..."
	. venv/bin/activate && python scripts/scrape_ticino.py

scrape-ticino-full: ## Scrape Ticino court decisions (full re-scrape)
	@echo "🔍 Scraping Ticino court decisions (full)..."
	. venv/bin/activate && python scripts/scrape_ticino.py --full

scrape-ticino-test: ## Test Ticino scraper (1993 only)
	@echo "🔍 Testing Ticino scraper (1993 only)..."
	. venv/bin/activate && python scripts/scrape_ticino.py --year 1993 --verbose

scrape-federal: ## Scrape Federal court decisions (all courts)
	@echo "🔍 Scraping Federal court decisions..."
	. venv/bin/activate && python scripts/scrape_federal.py

scrape-federal-test: ## Test Federal scraper (10 files per court)
	@echo "🔍 Testing Federal scraper (10 files per court)..."
	. venv/bin/activate && python scripts/scrape_federal.py --test --verbose

scrape-fedlex: ## Scrape Fedlex (all Swiss federal laws)
	@echo "🔍 Scraping Fedlex (all laws)..."
	. venv/bin/activate && python scripts/scrape_fedlex.py

scrape-fedlex-test: ## Test Fedlex scraper (5 laws only)
	@echo "🔍 Testing Fedlex scraper (5 laws only)..."
	. venv/bin/activate && python scripts/scrape_fedlex.py --test --verbose

build-abbrev-registry: ## Build law abbreviation registry from Fedlex SPARQL
	@echo "📚 Building abbreviation registry from Fedlex..."
	. venv/bin/activate && python scripts/build_abbreviation_registry.py

# ============================================
# DATA PARSING
# ============================================

parse-federal: ## Parse Federal decisions (PDF/HTML -> JSON)
	@echo "🧠 Parsing Federal decisions..."
	. venv/bin/activate && python scripts/parse_federal.py

parse-ticino: ## Parse Ticino decisions (HTML -> JSON)
	@echo "🧠 Parsing Ticino decisions..."
	. venv/bin/activate && python scripts/parse_ticino.py

parse-ticino-test: ## Test Ticino parser (5 files only)
	@echo "🧠 Testing Ticino parser (5 files only)..."
	. venv/bin/activate && python scripts/parse_ticino.py --test --verbose

parse-fedlex: ## Parse Fedlex PDFs (articles with hierarchical metadata)
	@echo "📖 Parsing Fedlex PDFs..."
	. venv/bin/activate && python scripts/parse_fedlex.py

parse-fedlex-test: ## Test Fedlex parser (3 PDFs only)
	@echo "📖 Testing Fedlex parser (3 PDFs only)..."
	. venv/bin/activate && python scripts/parse_fedlex.py --test --verbose

# ============================================
# EMBEDDING
# ============================================

embed-fedlex: ## Embed Fedlex articles into codex collection
	@echo "🔢 Embedding Fedlex articles..."
	. venv/bin/activate && python scripts/embed_fedlex.py

embed-fedlex-test: ## Test Fedlex embedding (10 articles only)
	@echo "🔢 Testing Fedlex embedding..."
	. venv/bin/activate && python scripts/embed_fedlex.py --test --verbose

embed-decisions: ## Embed Federal and Ticino decisions into library collection
	@echo "🔢 Embedding court decisions..."
	. venv/bin/activate && python scripts/embed_decisions.py

embed-decisions-test: ## Test decision embedding (5 decisions only)
	@echo "🔢 Testing decision embedding..."
	. venv/bin/activate && python scripts/embed_decisions.py --test --verbose

embed-all: embed-fedlex embed-decisions ## Embed all parsed data
	@echo "✅ All embeddings complete"

embed-status: ## Show embedding statistics
	. venv/bin/activate && python scripts/embed_status.py

# ============================================
# CLEANUP
# ============================================

clean: ## Remove all data (DESTRUCTIVE)
	@echo "WARNING: This will delete all data!"
	@read -p "Are you sure? [y/N] " -n 1 -r; \
	echo; \
	if [ "$$REPLY" = "y" ] || [ "$$REPLY" = "Y" ]; then \
		docker compose down -v; \
		rm -rf data/qdrant_storage data/redis_data data/postgres_data data/dossier/*.db; \
		echo "All data deleted."; \
	else \
		echo "Cancelled."; \
	fi

clean-logs: ## Clear all log files
	rm -f logs/*.log logs/*.jsonl
	@echo "Logs cleared"

clean-pycache: ## Remove Python cache files
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "Python cache cleared"

# ============================================
# DEVELOPMENT UTILITIES
# ============================================

format: ## Format code with black
	. venv/bin/activate && black src/ tests/

lint: ## Run linting checks
	. venv/bin/activate && flake8 src/ tests/

typecheck: ## Run type checking with mypy
	. venv/bin/activate && mypy src/

# ============================================
# HELP
# ============================================

help: ## Show this help message
	@echo "KERBERUS Development Commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Quick start: make setup && make start"
