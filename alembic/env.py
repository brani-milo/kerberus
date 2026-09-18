"""Alembic environment: URL from Settings (POSTGRES_* env vars / Docker secrets)."""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _url() -> str:
    try:
        from src.config import get_settings
        return get_settings().postgres_dsn
    except Exception:
        return os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))


target_metadata = None  # migrations are written by hand (raw SQL, IF NOT EXISTS)


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), pool_pre_ping=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
