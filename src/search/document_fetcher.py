"""
Document Fetcher: Retrieves full document content after search.

The search pipeline uses chunks for retrieval, but Qwen needs
full documents for accurate legal analysis.

Sources:
- Library (decisions): data/parsed/{federal,ticino}/{decision_id}.json
- Codex (laws): data/parsed/fedlex/{sr_number}.json
- Dossier (user docs): SQLCipher encrypted database
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Data directories
DATA_DIR = Path(__file__).parent.parent.parent / "data" / "parsed"
FEDERAL_DIR = DATA_DIR / "federal"
TICINO_DIR = DATA_DIR / "ticino"
FEDLEX_DIR = DATA_DIR / "fedlex"


def fetch_full_decision(decision_id: str) -> Optional[Dict]:
    """
    Fetch full court decision from parsed JSON files.

    Args:
        decision_id: Decision identifier (e.g., "BGE_123_III_45")

    Returns:
        Full decision dict with content, or None if not found
    """
    # Try federal first
    for search_dir in [FEDERAL_DIR, TICINO_DIR]:
        if not search_dir.exists():
            continue

        # Try exact filename match
        json_path = search_dir / f"{decision_id}.json"
        if json_path.exists():
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading decision {decision_id}: {e}")
                continue

        # Try searching for matching file (in case of naming variations)
        for json_file in search_dir.glob("*.json"):
            if decision_id in json_file.stem:
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception as e:
                    logger.error(f"Error loading {json_file}: {e}")
                    continue

    logger.warning(f"Decision not found: {decision_id}")
    return None


def fetch_full_law(sr_number: str, article_number: Optional[str] = None) -> Optional[Dict]:
    """
    Fetch full law from parsed Fedlex JSON files.

    Args:
        sr_number: SR (Systematic Collection) number (e.g., "220")
        article_number: Optional specific article

    Returns:
        Full law dict with all articles, or None if not found
    """
    if not FEDLEX_DIR.exists():
        logger.warning(f"Fedlex directory not found: {FEDLEX_DIR}")
        return None

    # Try direct SR number match
    json_path = FEDLEX_DIR / f"{sr_number}.json"
    if json_path.exists():
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading law {sr_number}: {e}")

    # Try searching for matching file
    for json_file in FEDLEX_DIR.glob("*.json"):
        if sr_number in json_file.stem:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading {json_file}: {e}")
                continue

    logger.warning(f"Law not found: {sr_number}")
    return None


def format_decision_for_llm(decision: Dict) -> str:
    """
    Format a court decision for LLM context.

    Args:
        decision: Full decision dict

    Returns:
        Formatted string with all relevant sections
    """
    parts = []

    # Header
    decision_id = decision.get("id", "Unknown")
    court = decision.get("court", "Unknown Court")
    date = decision.get("date", "Unknown Date")

    parts.append(f"=== DECISION: {decision_id} ===")
    parts.append(f"Court: {court}")
    parts.append(f"Date: {date}")
    parts.append("")

    content = decision.get("content", {})

    # Regeste (summary)
    if content.get("regeste"):
        parts.append("--- REGESTE (Summary) ---")
        parts.append(content["regeste"])
        parts.append("")

    # Facts
    if content.get("facts"):
        parts.append("--- FACTS ---")
        parts.append(content["facts"])
        parts.append("")

    # Reasoning (the most important part)
    if content.get("reasoning"):
        parts.append("--- REASONING ---")
        parts.append(content["reasoning"])
        parts.append("")

    # Decision
    if content.get("decision"):
        parts.append("--- DECISION ---")
        parts.append(content["decision"])
        parts.append("")

    return "\n".join(parts)


def format_law_for_llm(law: Dict, relevant_articles: Optional[List[str]] = None) -> str:
    """
    Format a law for LLM context.

    Args:
        law: Full law dict with articles
        relevant_articles: Optional list of specific article numbers to include

    Returns:
        Formatted string with law text
    """
    parts = []

    # Header
    sr_number = law.get("sr_number", "Unknown")
    title = law.get("title", "Unknown Law")

    parts.append(f"=== LAW: SR {sr_number} ===")
    parts.append(f"Title: {title}")
    parts.append("")

    # Articles
    articles = law.get("articles", [])

    for article in articles:
        article_num = article.get("article_number", "")

        # If specific articles requested, filter
        if relevant_articles and article_num not in relevant_articles:
            continue

        article_title = article.get("title", "")
        article_text = article.get("text", "")

        parts.append(f"Art. {article_num} {article_title}")
        parts.append(article_text)
        parts.append("")

    return "\n".join(parts)


def enrich_results_with_full_content(
    results: List[Dict],
    collection: str,
    dossier_service=None
) -> List[Dict]:
    """
    Enrich search results with full document content.

    Args:
        results: Deduplicated search results
        collection: Source collection ('library', 'codex', 'dossier')
        dossier_service: Optional DossierSearchService for user documents

    Returns:
        Results enriched with 'full_content' field
    """
    enriched = []

    for result in results:
        payload = result.get('payload', {})

        if collection == 'library':
            # Fetch full court decision
            decision_id = payload.get('decision_id')
            if decision_id:
                full_doc = fetch_full_decision(decision_id)
                if full_doc:
                    result['full_content'] = format_decision_for_llm(full_doc)
                    result['full_document'] = full_doc
                else:
                    # Fallback to what we have
                    result['full_content'] = payload.get('text_preview', '')

        elif collection == 'codex':
            # Fetch full law
            sr_number = payload.get('sr_number')
            if sr_number:
                full_doc = fetch_full_law(sr_number)
                if full_doc:
                    # Include the specific article that matched + surrounding context
                    article_num = payload.get('article_number')
                    result['full_content'] = format_law_for_llm(full_doc, [article_num] if article_num else None)
                    result['full_document'] = full_doc
                else:
                    result['full_content'] = payload.get('article_text', payload.get('text_preview', ''))

        elif collection == 'dossier':
            # Fetch from encrypted dossier
            doc_id = payload.get('doc_id')
            if doc_id and dossier_service:
                full_doc = dossier_service.get_document(doc_id)
                if full_doc:
                    result['full_content'] = full_doc.get('content', '')
                    result['full_document'] = full_doc
                else:
                    result['full_content'] = payload.get('text_preview', '')
            else:
                result['full_content'] = payload.get('text_preview', '')

        enriched.append(result)

    return enriched
