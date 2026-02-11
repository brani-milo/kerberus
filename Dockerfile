# ===========================================
# KERBERUS - Optimized CPU-only Dockerfile
# Multi-stage build for minimal image size
# ===========================================

# Stage 1: Builder
FROM python:3.10-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install PyTorch CPU-only FIRST (before requirements.txt)
RUN pip install --no-cache-dir \
    torch==2.2.0+cpu \
    --index-url https://download.pytorch.org/whl/cpu

# Copy and install requirements (torch will be skipped as already installed)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ===========================================
# Stage 2: Runtime (minimal)
# ===========================================
FROM python:3.10-slim as runtime

WORKDIR /app

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsqlite3-0 \
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

# Default command (Chainlit app)
CMD ["chainlit", "run", "frontend/app.py", "--host", "0.0.0.0", "--port", "8000"]
