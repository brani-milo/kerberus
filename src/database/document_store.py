"""
Document store: full texts of laws and court decisions, keyed for O(1) lookup.

Why: the search index (Qdrant) holds chunks; the LLM needs whole documents.
Previously those came from JSON files on disk found by globbing the parsed
directory per request, which was linear in the corpus size and coupled the
web app to the scraper's filesystem layout.

Storage: PostgreSQL (SQLAlchemy Core, so tests can run against SQLite).
  documents(doc_id PK, collection, language, sr_number, content JSON, updated_at)
  document_aliases(alias PK, doc_id FK)   -- alternate ids (citation, file stem, normalised forms)
"""
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, MetaData, String, Table, create_engine, select, text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.types import JSON

logger = logging.getLogger(__name__)

metadata = MetaData()

# JSONB on PostgreSQL, plain JSON elsewhere (SQLite in tests)
JSONType = JSONB().with_variant(JSON(), "sqlite")

documents = Table(
    "documents", metadata,
    Column("doc_id", String(255), primary_key=True),
    Column("collection", String(32), nullable=False),      # library | codex
    Column("language", String(8), nullable=True),
    Column("sr_number", String(64), nullable=True),
    Column("content", JSONType, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("idx_documents_collection", "collection"),
    Index("idx_documents_sr_lang", "sr_number", "language"),
)

document_aliases = Table(
    "document_aliases", metadata,
    Column("alias", String(255), primary_key=True),
    Column("doc_id", String(255), ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False),
    Index("idx_document_aliases_doc", "doc_id"),
)


def normalize_alias(value: str) -> str:
    """Case/separator-insensitive form of an id: 'BGE 102 Ia 35' == 'bge-102-IA-35'."""
    if not value:
        return ""
    v = str(value).strip().upper()
    v = re.sub(r"\s+", "-", v)
    v = v.replace("/", "-").replace("_", "-")
    v = re.sub(r"-{2,}", "-", v)
    return v.strip("-")


@dataclass
class DocumentRecord:
    doc_id: str
    collection: str
    content: Dict
    language: Optional[str] = None
    sr_number: Optional[str] = None
    aliases: List[str] = field(default_factory=list)


class DocumentStore:
    """Read/write access to full documents. Safe to share across threads."""

    RETRY_SECONDS = 60  # how long to back off after the database was unreachable

    def __init__(self, engine=None, dsn: Optional[str] = None):
        if engine is None:
            if dsn is None:
                from ..config import get_settings
                dsn = get_settings().postgres_dsn
            engine = create_engine(dsn, pool_pre_ping=True, pool_size=5, max_overflow=10, pool_recycle=300)
        self.engine = engine
        self._unavailable_until = 0.0
        self._lock = threading.Lock()

    # ---- schema -----------------------------------------------------------

    def init_schema(self) -> None:
        metadata.create_all(self.engine)

    # ---- availability -----------------------------------------------------

    def is_available(self) -> bool:
        """Cheap liveness check with back-off so a down database is not hammered per request."""
        if time.time() < self._unavailable_until:
            return False
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            with self._lock:
                self._unavailable_until = time.time() + self.RETRY_SECONDS
            logger.warning(f"Document store unavailable ({e}); falling back to files for {self.RETRY_SECONDS}s")
            return False

    # ---- writes -----------------------------------------------------------

    def upsert_documents(self, records: Iterable[DocumentRecord], batch_size: int = 500) -> int:
        """Insert or replace documents (and their aliases). Returns number written."""
        written = 0
        batch: List[DocumentRecord] = []
        for rec in records:
            batch.append(rec)
            if len(batch) >= batch_size:
                written += self._write_batch(batch)
                batch = []
        if batch:
            written += self._write_batch(batch)
        return written

    def _write_batch(self, batch: List[DocumentRecord]) -> int:
        now = datetime.now(timezone.utc)
        doc_rows = [
            {"doc_id": r.doc_id, "collection": r.collection, "language": r.language,
             "sr_number": r.sr_number, "content": r.content, "updated_at": now}
            for r in batch
        ]
        alias_rows = []
        seen = set()
        for r in batch:
            for alias in self._alias_set(r):
                if alias and alias not in seen:
                    seen.add(alias)
                    alias_rows.append({"alias": alias, "doc_id": r.doc_id})

        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                stmt = pg_insert(documents).values(doc_rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[documents.c.doc_id],
                    set_={"collection": stmt.excluded.collection, "language": stmt.excluded.language,
                          "sr_number": stmt.excluded.sr_number, "content": stmt.excluded.content,
                          "updated_at": stmt.excluded.updated_at},
                )
                conn.execute(stmt)
                if alias_rows:
                    astmt = pg_insert(document_aliases).values(alias_rows)
                    astmt = astmt.on_conflict_do_update(
                        index_elements=[document_aliases.c.alias], set_={"doc_id": astmt.excluded.doc_id})
                    conn.execute(astmt)
            else:  # generic path (SQLite in tests): delete-then-insert
                ids = [r["doc_id"] for r in doc_rows]
                conn.execute(document_aliases.delete().where(document_aliases.c.doc_id.in_(ids)))
                conn.execute(documents.delete().where(documents.c.doc_id.in_(ids)))
                conn.execute(documents.insert(), doc_rows)
                if alias_rows:
                    aliases = [a["alias"] for a in alias_rows]
                    conn.execute(document_aliases.delete().where(document_aliases.c.alias.in_(aliases)))
                    conn.execute(document_aliases.insert(), alias_rows)
        return len(doc_rows)

    @staticmethod
    def _alias_set(rec: DocumentRecord) -> List[str]:
        raw = [rec.doc_id, *rec.aliases]
        out: List[str] = []
        for a in raw:
            if not a:
                continue
            out.append(str(a))
            out.append(normalize_alias(a))
        return list(dict.fromkeys(out))

    # ---- reads ------------------------------------------------------------

    def get(self, doc_id: str) -> Optional[Dict]:
        with self.engine.connect() as conn:
            row = conn.execute(select(documents.c.content).where(documents.c.doc_id == doc_id)).first()
        return dict(row[0]) if row else None

    def get_by_alias(self, alias: str) -> Optional[Dict]:
        """Resolve an id via exact alias, then its normalised form."""
        if not alias:
            return None
        candidates = [str(alias), normalize_alias(alias)]
        with self.engine.connect() as conn:
            row = conn.execute(
                select(documents.c.content)
                .select_from(document_aliases.join(documents, document_aliases.c.doc_id == documents.c.doc_id))
                .where(document_aliases.c.alias.in_(candidates))
                .limit(1)
            ).first()
        return dict(row[0]) if row else None

    def get_decision(self, decision_id: str) -> Optional[Dict]:
        return self.get_by_alias(decision_id)

    def get_law(self, sr_number: str, language: Optional[str] = None) -> Optional[Dict]:
        """Law by SR number; prefers the requested language, then de/fr/it."""
        order = [language] if language else []
        order += [lang for lang in ("de", "fr", "it") if lang not in order]
        with self.engine.connect() as conn:
            for lang in order:
                row = conn.execute(
                    select(documents.c.content).where(
                        documents.c.collection == "codex",
                        documents.c.sr_number == str(sr_number),
                        documents.c.language == lang,
                    )
                ).first()
                if row:
                    return dict(row[0])
        return None

    def count(self, collection: Optional[str] = None) -> int:
        stmt = select(func.count()).select_from(documents)
        if collection:
            stmt = stmt.where(documents.c.collection == collection)
        with self.engine.connect() as conn:
            return int(conn.execute(stmt).scalar() or 0)


# ---- singleton ----------------------------------------------------------

_store: Optional[DocumentStore] = None
_store_lock = threading.Lock()


def get_document_store() -> Optional[DocumentStore]:
    """Shared store, or None when disabled via DOCUMENT_STORE_ENABLED=false."""
    global _store
    from ..config import get_settings
    if not get_settings().document_store_enabled:
        return None
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = DocumentStore()
    return _store


# ---- converters from parsed JSON --------------------------------------------

def decision_record(decision: Dict, file_stem: Optional[str] = None, source: str = "federal") -> DocumentRecord:
    """Build a store record from a parsed decision JSON (data/parsed/{federal,ticino})."""
    stem = file_stem or (decision.get("file_name") or decision.get("id") or "").rsplit(".", 1)[0]
    doc_id = stem or str(decision.get("id"))
    aliases = [decision.get("id"), decision.get("file_name", "").rsplit(".", 1)[0]]
    return DocumentRecord(
        doc_id=doc_id,
        collection="library",
        content={**decision, "source": decision.get("source", source)},
        language=decision.get("language"),
        aliases=[a for a in aliases if a],
    )


def law_record(sr_number: str, language: str, articles: List[Dict]) -> DocumentRecord:
    """Build one store record per (law, language) from parsed Fedlex articles."""
    first = articles[0] if articles else {}
    content = {
        "sr_number": str(sr_number),
        "language": language,
        "title": first.get("sr_name") or first.get("title") or f"SR {sr_number}",
        "abbreviation": first.get("abbreviation"),
        "abbreviations_all": first.get("abbreviations_all", {}),
        "articles": [
            {
                "id": a.get("id"),
                "article_number": a.get("article_number"),
                "article_title": a.get("article_title"),
                "article_text": a.get("article_text", ""),
                "hierarchy_path": a.get("hierarchy_path"),
                "paragraph_number": a.get("paragraph_number"),
            }
            for a in articles
        ],
    }
    return DocumentRecord(
        doc_id=f"law:{sr_number}:{language}",
        collection="codex",
        content=content,
        language=language,
        sr_number=str(sr_number),
        aliases=[f"SR_{sr_number}_{language}"],
    )
