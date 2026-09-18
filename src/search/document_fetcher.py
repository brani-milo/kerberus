"""
Document Fetcher: full document content for the LLM after search.

Lookup order:
1. Document store (PostgreSQL, keyed by id/alias) - see src/database/document_store.py
2. Parsed JSON files on disk, via an in-memory filename index built once
   (no per-request directory globbing)

Sources on disk:
- Library (decisions): data/parsed/{federal,ticino}/{file_stem}.json
- Codex (laws):        data/parsed/fedlex/SR_{sr_number}_{lang}.json  (list of articles)
"""
import json
import logging
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from ..database.document_store import normalize_alias, law_record

logger = logging.getLogger(__name__)

# Data directories (override with PARSED_DATA_DIR, e.g. /app/data/parsed in Docker)
DATA_DIR = Path(os.getenv("PARSED_DATA_DIR") or Path(__file__).parent.parent.parent / "data" / "parsed")
FEDERAL_DIR = DATA_DIR / "federal"
TICINO_DIR = DATA_DIR / "ticino"
FEDLEX_DIR = DATA_DIR / "fedlex"

# Budgets for what one decision may contribute to the LLM context (characters)
MAX_DECISION_CHARS = int(os.getenv("MAX_DECISION_CHARS", "12000"))


# ---------------------------------------------------------------------------
# Filesystem index (fallback when the document store is unavailable)
# ---------------------------------------------------------------------------

class _FileIndex:
    """Maps decision ids (exact and normalised) to JSON paths. Built once, lazily."""

    def __init__(self, directories: List[Path]):
        self.directories = directories
        self._by_stem: Dict[str, Path] = {}
        self._by_norm: Dict[str, Path] = {}
        self._built = False
        self._lock = threading.Lock()

    def _build(self) -> None:
        with self._lock:
            if self._built:
                return
            for directory in self.directories:
                if not directory.exists():
                    continue
                for path in directory.glob("*.json"):
                    self._by_stem[path.stem] = path
                    self._by_norm.setdefault(normalize_alias(path.stem), path)
            self._built = True
            logger.info(f"Decision file index built: {len(self._by_stem)} files")

    def find(self, decision_id: str) -> Optional[Path]:
        if not decision_id:
            return None
        self._build()
        path = self._by_stem.get(decision_id) or self._by_norm.get(normalize_alias(decision_id))
        if path:
            return path
        # Last resort: the id is a citation embedded in the file name (e.g. "BGE-97-I-878")
        needle = normalize_alias(decision_id)
        if len(needle) >= 6:
            for norm, p in self._by_norm.items():
                if needle in norm:
                    return p
        return None

    def reset(self) -> None:
        with self._lock:
            self._by_stem.clear()
            self._by_norm.clear()
            self._built = False


_decision_index = _FileIndex([FEDERAL_DIR, TICINO_DIR])


def _load_json(path: Path) -> Optional[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading {path}: {e}")
        return None


def _store():
    """Document store or None (disabled/unavailable)."""
    try:
        from ..database.document_store import get_document_store
        store = get_document_store()
        if store is not None and store.is_available():
            return store
    except Exception as e:
        logger.debug(f"Document store not usable: {e}")
    return None


# ---------------------------------------------------------------------------
# Public fetch API (cached: the same decision is requested by several chunks)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1024)
def fetch_full_decision(decision_id: str) -> Optional[Dict]:
    """Fetch a full court decision by id (store first, then indexed files)."""
    if not decision_id:
        return None

    store = _store()
    if store is not None:
        try:
            doc = store.get_decision(decision_id)
            if doc:
                return doc
        except Exception as e:
            logger.warning(f"Document store lookup failed for {decision_id}: {e}")

    path = _decision_index.find(decision_id)
    if path:
        return _load_json(path)

    logger.warning(f"Decision not found: {decision_id}")
    return None


@lru_cache(maxsize=512)
def fetch_full_law(sr_number: str, language: Optional[str] = None) -> Optional[Dict]:
    """
    Fetch a law (all articles) by SR number, preferring the requested language.

    Returns the store's law shape: {sr_number, language, title, abbreviation, articles: [...]}.
    """
    if not sr_number:
        return None
    sr_number = str(sr_number)

    store = _store()
    if store is not None:
        try:
            doc = store.get_law(sr_number, language)
            if doc:
                return doc
        except Exception as e:
            logger.warning(f"Document store lookup failed for SR {sr_number}: {e}")

    if not FEDLEX_DIR.exists():
        return None
    order = [language] if language else []
    order += [lang for lang in ("de", "fr", "it") if lang not in order]
    for lang in order:
        path = FEDLEX_DIR / f"SR_{sr_number}_{lang}.json"
        if path.exists():
            data = _load_json(path)
            if data is None:
                continue
            articles = data if isinstance(data, list) else data.get("articles", [])
            return law_record(sr_number, lang, articles).content

    logger.warning(f"Law not found: SR {sr_number}")
    return None


def clear_caches() -> None:
    """Drop cached documents (after a reload of the store or the files)."""
    fetch_full_decision.cache_clear()
    fetch_full_law.cache_clear()
    _decision_index.reset()


# ---------------------------------------------------------------------------
# Formatting for the LLM
# ---------------------------------------------------------------------------

def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0] + "\n[... gekürzt / tronqué / troncato ...]"


def format_decision_for_llm(decision: Dict, max_chars: Optional[int] = None) -> str:
    """
    Format a court decision for LLM context within a character budget.

    Section priority when the budget is tight: regeste (always), reasoning,
    decision, facts. A whole BGE can exceed 50k characters, so this keeps ten
    decisions inside the model's context window.
    """
    max_chars = MAX_DECISION_CHARS if max_chars is None else max_chars
    content = decision.get("content", {}) or {}

    header = [
        f"=== DECISION: {decision.get('id', 'Unknown')} ===",
        f"Court: {decision.get('court', 'Unknown Court')}",
        f"Date: {decision.get('date', 'Unknown Date')}",
        "",
    ]
    budget = max_chars - sum(len(h) + 1 for h in header)

    sections = []
    regeste = content.get("regeste") or ""
    if regeste:
        regeste = _truncate(regeste, max(budget // 3, 1500))
        sections.append(("--- REGESTE (Summary) ---", regeste))
        budget -= len(regeste) + 30

    # Remaining budget split: reasoning gets the lion's share
    priority = [("reasoning", "--- REASONING ---", 0.7), ("decision", "--- DECISION ---", 0.1), ("facts", "--- FACTS ---", 0.2)]
    remaining = max(budget, 0)
    for key, title, share in priority:
        body = content.get(key) or ""
        if not body:
            continue
        allowed = int(remaining * share) if remaining > 0 else 0
        if allowed < 200:
            continue
        sections.append((title, _truncate(body, allowed)))

    # Output in reading order
    order = {"--- REGESTE (Summary) ---": 0, "--- FACTS ---": 1, "--- REASONING ---": 2, "--- DECISION ---": 3}
    sections.sort(key=lambda s: order.get(s[0], 9))

    parts = list(header)
    for title, body in sections:
        parts.extend([title, body, ""])
    return "\n".join(parts)


def format_law_for_llm(law: Dict, relevant_articles: Optional[List[str]] = None) -> str:
    """Format a law (or the requested articles of it) for LLM context."""
    parts = [f"=== LAW: SR {law.get('sr_number', 'Unknown')} ===", f"Title: {law.get('title', 'Unknown Law')}"]
    if law.get("abbreviation"):
        parts.append(f"Abbreviation: {law['abbreviation']}")
    parts.append("")

    wanted = set(str(a) for a in relevant_articles) if relevant_articles else None
    for article in law.get("articles", []):
        number = str(article.get("article_number", ""))
        if wanted and number not in wanted:
            continue
        title = article.get("article_title") or article.get("title") or ""
        text = article.get("article_text") or article.get("text") or ""
        parts.append(f"Art. {number} {title}".rstrip())
        parts.append(text)
        parts.append("")

    return "\n".join(parts)


def enrich_results_with_full_content(
    results: List[Dict],
    collection: str,
    dossier_service=None
) -> List[Dict]:
    """
    Attach 'full_content' (LLM-ready text) and 'full_document' to search results.
    """
    for result in results:
        payload = result.get('payload', {})

        if collection == 'library':
            decision_id = payload.get('decision_id')
            full_doc = fetch_full_decision(decision_id) if decision_id else None
            if full_doc:
                result['full_content'] = format_decision_for_llm(full_doc)
                result['full_document'] = full_doc
            else:
                result['full_content'] = payload.get('text') or payload.get('text_preview', '')

        elif collection == 'codex':
            sr_number = payload.get('sr_number')
            full_doc = fetch_full_law(sr_number, payload.get('language')) if sr_number else None
            if full_doc:
                article_num = payload.get('article_number')
                result['full_content'] = format_law_for_llm(full_doc, [article_num] if article_num else None)
                result['full_document'] = full_doc
            else:
                result['full_content'] = payload.get('article_text') or payload.get('text_preview', '')

        elif collection == 'dossier':
            doc_id = payload.get('doc_id')
            full_doc = dossier_service.get_document(doc_id) if (doc_id and dossier_service) else None
            if full_doc:
                result['full_content'] = full_doc.get('content', '')
                result['full_document'] = full_doc
            else:
                result['full_content'] = payload.get('text_preview', '')

    return results
