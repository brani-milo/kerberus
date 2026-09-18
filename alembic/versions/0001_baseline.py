"""Baseline schema (auth, sessions, usage, firms, dossier keys, documents, conversations)

Every statement uses IF NOT EXISTS so the migration can be applied to databases
that were created by AuthDB.init_schema() before Alembic was introduced
(`alembic stamp 0001` is equivalent on such databases).

Revision ID: 0001
Revises: None
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STATEMENTS = [
    # --- auth --------------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS users (
        user_id UUID PRIMARY KEY,
        email VARCHAR(255) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        totp_secret VARCHAR(64),
        backup_codes TEXT,
        is_active BOOLEAN DEFAULT TRUE,
        mfa_enabled BOOLEAN DEFAULT FALSE,
        last_login TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        session_token VARCHAR(64) PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        device_fingerprint VARCHAR(255),
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS token_usage (
        usage_id SERIAL PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        model VARCHAR(100) NOT NULL,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cost_chf DECIMAL(10, 6) NOT NULL DEFAULT 0,
        operation VARCHAR(50),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS firms (
        firm_id UUID PRIMARY KEY,
        firm_name VARCHAR(255) NOT NULL,
        master_key_reference VARCHAR(255),
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS firm_members (
        user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        firm_id UUID NOT NULL REFERENCES firms(firm_id) ON DELETE CASCADE,
        role VARCHAR(50) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (user_id, firm_id)
    )""",
    """CREATE TABLE IF NOT EXISTS failed_logins (
        id SERIAL PRIMARY KEY,
        email VARCHAR(255) NOT NULL,
        attempted_at TIMESTAMP WITH TIME ZONE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS dossier_keys (
        user_id UUID PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
        wrapped_dek TEXT NOT NULL,
        kdf_salt TEXT NOT NULL,
        kdf_iterations INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_token_usage_user ON token_usage(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_failed_logins_email ON failed_logins(email, attempted_at)",
    # --- document store ------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS documents (
        doc_id VARCHAR(255) PRIMARY KEY,
        collection VARCHAR(32) NOT NULL,
        language VARCHAR(8),
        sr_number VARCHAR(64),
        content JSONB NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS document_aliases (
        alias VARCHAR(255) PRIMARY KEY,
        doc_id VARCHAR(255) NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE
    )""",
    "CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection)",
    "CREATE INDEX IF NOT EXISTS idx_documents_sr_lang ON documents(sr_number, language)",
    "CREATE INDEX IF NOT EXISTS idx_document_aliases_doc ON document_aliases(doc_id)",
    # --- encrypted conversations (Chainlit data layer) ----------------------
    """CREATE TABLE IF NOT EXISTS conversation_threads (
        id UUID PRIMARY KEY,
        user_id VARCHAR(255) NOT NULL,
        name_encrypted TEXT,
        metadata_encrypted TEXT,
        created_at TIMESTAMP WITH TIME ZONE,
        updated_at TIMESTAMP WITH TIME ZONE,
        is_active BOOLEAN
    )""",
    """CREATE TABLE IF NOT EXISTS conversation_messages (
        id UUID PRIMARY KEY,
        thread_id UUID NOT NULL REFERENCES conversation_threads(id) ON DELETE CASCADE,
        content_encrypted TEXT NOT NULL,
        metadata_encrypted TEXT,
        role VARCHAR(50) NOT NULL,
        sequence INTEGER NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE
    )""",
    """CREATE TABLE IF NOT EXISTS conversation_feedback (
        id UUID PRIMARY KEY,
        message_id UUID NOT NULL REFERENCES conversation_messages(id) ON DELETE CASCADE,
        value INTEGER NOT NULL,
        comment_encrypted TEXT,
        created_at TIMESTAMP WITH TIME ZONE
    )""",
    "CREATE INDEX IF NOT EXISTS idx_thread_user_updated ON conversation_threads(user_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_message_thread_seq ON conversation_messages(thread_id, sequence)",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    # Baseline: intentionally non-destructive. Drop tables manually if you really need to.
    pass
