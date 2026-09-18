# ===========================================
# KERBERUS - Optimized CPU-only Dockerfile
# Multi-stage build for minimal image size
# ===========================================

# Stage 1: Builder
# Python 3.13 to match the tested development environment (requirements.txt pins).
# bookworm is pinned explicitly because the runtime needs Debian's libsqlcipher1.
FROM python:3.13-slim-bookworm AS builder

WORKDIR /app

# Install build dependencies
# libsqlcipher-dev: required to compile the `sqlcipher3` bindings (encrypted dossiers).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsqlcipher-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install PyTorch CPU-only FIRST (before requirements.txt). Version must match
# the torch pin implied by requirements.txt (transformers/FlagEmbedding versions).
RUN pip install --no-cache-dir \
    torch==2.9.1 \
    --index-url https://download.pytorch.org/whl/cpu

# Copy and install requirements (torch will be skipped as already installed)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ===========================================
# Stage 2: Runtime (minimal)
# ===========================================
FROM python:3.13-slim-bookworm AS runtime

WORKDIR /app

# Install only runtime dependencies (libsqlcipher1 = SQLCipher shared library)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsqlcipher1 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean \
    && rm -rf /var/cache/apt/archives/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy source code only (data is mounted as volumes at runtime)
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY frontend/ ./frontend/
COPY .chainlit/ ./.chainlit/
COPY chainlit.md .
COPY public/ ./public/
# Schema migrations (alembic upgrade head) and the golden retrieval set
COPY alembic.ini .
COPY alembic/ ./alembic/
COPY tests/eval/ ./tests/eval/

# Create directory for dossier storage
RUN mkdir -p /app/data/dossier && chmod 700 /app/data/dossier

# Environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV QDRANT_HOST=qdrant
ENV QDRANT_PORT=6333

# Expose ports
EXPOSE 8000

# Copy and setup entrypoint script for Docker secrets
COPY scripts/docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

# Default command (Chainlit app). The REST API uses the same image with:
#   uvicorn src.api.main:app --host 0.0.0.0 --port 8000
# (see the `api` service in docker-compose.yml)
CMD ["chainlit", "run", "frontend/app.py", "--host", "0.0.0.0", "--port", "8000"]
