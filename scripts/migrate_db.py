#!/usr/bin/env python3
"""
Run database migrations for KERBERUS.

Usage:
    python scripts/migrate_db.py
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.auth_db import get_auth_db


def main():
    print("=" * 60)
    print("KERBERUS Database Migration")
    print("=" * 60)

    db = get_auth_db()

    # Run migrations
    print("\n[1] Checking backup_codes column...")
    if db.migrate_add_backup_codes_column():
        print("    Added backup_codes column to users table")
    else:
        print("    backup_codes column already exists")

    print("\n" + "=" * 60)
    print("Migration complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
