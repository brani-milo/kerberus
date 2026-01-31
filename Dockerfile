# Use PyTorch with CUDA runtime for GPU support (Optimized for Cloud)
FROM pytorch/pytorch:2.1.2-cuda12.1-cudnn8-runtime

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies
# Note: For Docker (Linux), sqlcipher3 usually builds much easier than on Mac
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY frontend/ ./frontend/
COPY data/ ./data/
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

# Default command (Chainlit app)
CMD ["chainlit", "run", "frontend/app.py", "--host", "0.0.0.0", "--port", "8000"]
