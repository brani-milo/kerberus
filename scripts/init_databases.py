#!/usr/bin/env python3
"""
Initialize KERBERUS storage.

    make db-init   (or: python scripts/init_databases.py)

1. PostgreSQL schema (auth, sessions, usage, dossier keys, document store)
   - prefers Alembic (`alembic upgrade head`); falls back to CREATE IF NOT EXISTS
2. Qdrant collections (codex, library)
3. Dossier directory permissions (700)
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def init_postgres() -> None:
    print("PostgreSQL schema...")
    try:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, check=True)
        print("  applied via Alembic")
        return
    except Exception as e:
        print(f"  Alembic unavailable ({e}); using CREATE IF NOT EXISTS")
    from src.database.auth_db import get_auth_db
    from src.database.document_store import DocumentStore
    get_auth_db().init_schema()
    DocumentStore().init_schema()
    print("  auth + document store tables ready")


def init_qdrant() -> None:
    print("Qdrant collections...")
    from src.database.vector_db import init_qdrant_collections
    init_qdrant_collections()


def init_dossier_dir() -> None:
    print("Dossier directory...")
    path = Path(os.getenv("DOSSIER_STORAGE_PATH", ROOT / "data" / "dossier"))
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    print(f"  {path} (700)")


def main() -> int:
    for step in (init_postgres, init_qdrant, init_dossier_dir):
        try:
            step()
        except Exception as e:
            print(f"  FAILED: {e}")
    print("Done. Next: make load-documents (full texts), then embed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
