#!/usr/bin/env python3
"""
Initialize KERBERUS database schemas.

Run this after first setup: make db-init

This script:
1. Creates PostgreSQL tables for auth and metadata
2. Creates Qdrant collections for vector storage
3. Validates SQLCipher setup (databases created per-user at signup)
"""
import os
import sys
from pathlib import Path

# Add src to Python path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def init_auth_database():
    """
    Initialize PostgreSQL schema for authentication and metadata.

    Creates tables for:
    - Users (auth)
    - Sessions
    - Firms
    - Firm members
    - Token usage tracking
    """
    print("Creating PostgreSQL tables...")

    # SQL Schema (to be executed when db connection is implemented)
    schema = """
    -- Enable UUID extension
    CREATE EXTENSION IF NOT EXISTS "pgcrypto";

    -- Users table
    CREATE TABLE IF NOT EXISTS users (
        user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email VARCHAR(255) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        totp_secret VARCHAR(255),
        created_at TIMESTAMP DEFAULT NOW(),
        last_login TIMESTAMP,
        is_active BOOLEAN DEFAULT true
    );

    -- Sessions table
    CREATE TABLE IF NOT EXISTS sessions (
        session_token VARCHAR(255) PRIMARY KEY,
        user_id UUID REFERENCES users(user_id) ON DELETE CASCADE,
        expires_at TIMESTAMP NOT NULL,
        created_at TIMESTAMP DEFAULT NOW(),
        device_fingerprint VARCHAR(255)
    );

    -- Firms table
    CREATE TABLE IF NOT EXISTS firms (
        firm_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        firm_name VARCHAR(255) NOT NULL,
        master_key_reference VARCHAR(255),
        created_at TIMESTAMP DEFAULT NOW()
    );

    -- Firm members table
    CREATE TABLE IF NOT EXISTS firm_members (
        user_id UUID REFERENCES users(user_id) ON DELETE CASCADE,
        firm_id UUID REFERENCES firms(firm_id) ON DELETE CASCADE,
        role VARCHAR(50) NOT NULL,
        joined_at TIMESTAMP DEFAULT NOW(),
        PRIMARY KEY (user_id, firm_id)
    );

    -- Token usage tracking (for cost monitoring)
    CREATE TABLE IF NOT EXISTS token_usage (
        id SERIAL PRIMARY KEY,
        user_id UUID REFERENCES users(user_id) ON DELETE CASCADE,
        conversation_id UUID,
        turn_number INTEGER,

        -- Token counts
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        total_tokens INTEGER NOT NULL,

        -- Context breakdown (for debugging)
        chat_history_tokens INTEGER,
        legal_context_tokens INTEGER,
        query_tokens INTEGER,

        -- Costs (CHF)
        input_cost_chf DECIMAL(10,4),
        output_cost_chf DECIMAL(10,4),
        total_cost_chf DECIMAL(10,4),

        -- Metadata
        model_used VARCHAR(100),
        context_swapped BOOLEAN DEFAULT true,
        timestamp TIMESTAMP DEFAULT NOW()
    );

    -- Indexes for performance
    CREATE INDEX IF NOT EXISTS idx_token_usage_user_date ON token_usage(user_id, timestamp);
    CREATE INDEX IF NOT EXISTS idx_token_usage_conversation ON token_usage(conversation_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
    """

    # TODO: Execute schema against PostgreSQL when db connection is implemented
    print("  Schema defined (will execute when auth_db.py is implemented)")
    print("  Tables: users, sessions, firms, firm_members, token_usage")


def init_qdrant_collections():
    """
    Initialize Qdrant collections for vector storage.

    Creates collections for:
    - codex (Swiss laws)
    - library (Case law)
    - Per-user dossier collections created dynamically at signup
    """
    print("Creating Qdrant collections...")

    # Collection configuration
    collections = {
        "codex": {
            "vector_size": 768,
            "distance": "Cosine",
            "description": "Swiss legal code (OR, ZGB, StGB, etc.)"
        },
        "library": {
            "vector_size": 768,
            "distance": "Cosine",
            "description": "Case law (BGE, cantonal courts)"
        }
    }

    for name, config in collections.items():
        print(f"  Collection '{name}': {config['description']}")
        print(f"    - Vector size: {config['vector_size']}")
        print(f"    - Distance: {config['distance']}")

    # TODO: Create collections via Qdrant client when vector_db.py is implemented
    print("  Collections defined (will execute when vector_db.py is implemented)")


def validate_sqlcipher_setup():
    """
    Validate SQLCipher directory exists with correct permissions.
    Actual databases are created per-user at signup time.
    """
    print("Validating SQLCipher setup...")

    dossier_path = Path(__file__).parent.parent / "data" / "dossier"

    if not dossier_path.exists():
        print(f"  Creating dossier directory: {dossier_path}")
        dossier_path.mkdir(parents=True, exist_ok=True)
        os.chmod(dossier_path, 0o700)
        print("  Directory created with permissions 700 (owner only)")
    else:
        print(f"  Dossier directory exists: {dossier_path}")
        # Check permissions
        mode = oct(dossier_path.stat().st_mode)[-3:]
        if mode != "700":
            print(f"  WARNING: Permissions are {mode}, should be 700")
            print("  Run: chmod 700 data/dossier")
        else:
            print("  Permissions OK (700)")


def main():
    print("=" * 60)
    print("KERBERUS Database Initialization")
    print("=" * 60)

    print("\n[1/3] PostgreSQL Schema:")
    init_auth_database()

    print("\n[2/3] Qdrant Collections:")
    init_qdrant_collections()

    print("\n[3/3] SQLCipher Setup:")
    validate_sqlcipher_setup()

    print("\n" + "=" * 60)
    print("Database initialization complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Implement database connection managers (src/database/)")
    print("  2. Run 'make test' to verify setup")
    print("  3. Start developing!")


if __name__ == "__main__":
    main()
