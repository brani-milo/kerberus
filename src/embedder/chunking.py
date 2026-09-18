"""
Shared chunking rules for court decisions.

One implementation used by every ingestion path (local, streaming, Modal) AND
by the backfill script, so a chunk's text can always be recomputed from the
parsed document and matched by (decision_id, chunk_index).
"""
import re
from typing import Dict, List

# Order matters: chunk_index is assigned sequentially across these sections.
DECISION_SECTIONS = ("regeste", "facts", "reasoning", "decision")

DEFAULT_MAX_WORDS = 1000
DEFAULT_MIN_WORDS = 200
PREVIEW_CHARS = 200


def split_text_into_chunks(text: str, max_words: int = DEFAULT_MAX_WORDS, min_words: int = DEFAULT_MIN_WORDS) -> List[str]:
    """Split text at paragraph boundaries into chunks of at most `max_words` words."""
    if not text:
        return []

    words = text.split()
    if len(words) <= max_words:
        return [text]

    paragraphs = re.split(r'\n\n+|\n(?=\d+\.?\s)', text)

    chunks: List[str] = []
    current: List[str] = []
    current_words = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_words = len(para.split())
        if current_words + para_words > max_words and current:
            chunks.append('\n\n'.join(current))
            current = [para]
            current_words = para_words
        else:
            current.append(para)
            current_words += para_words

    if current:
        chunk_text = '\n\n'.join(current)
        if chunks and current_words < min_words:
            chunks[-1] = chunks[-1] + '\n\n' + chunk_text
        else:
            chunks.append(chunk_text)

    return chunks


def chunk_decision(decision: Dict, max_words: int = DEFAULT_MAX_WORDS) -> List[Dict]:
    """
    Split a parsed decision into embeddable chunks.

    Returns dicts with chunk_id, decision_id, chunk_type, chunk_index, text and
    a back-reference to the decision.
    """
    chunks: List[Dict] = []
    decision_id = decision.get("id", "unknown")
    content = decision.get("content", {}) or {}

    chunk_index = 0
    for section_type in DECISION_SECTIONS:
        section_text = content.get(section_type)
        if not section_text:
            continue
        for text in split_text_into_chunks(section_text, max_words):
            chunks.append({
                "chunk_id": f"{decision_id}_chunk_{chunk_index}",
                "decision_id": decision_id,
                "chunk_type": section_type,
                "chunk_index": chunk_index,
                "text": text,
                "decision": decision,
            })
            chunk_index += 1

    return chunks


def create_text_preview(text: str, max_chars: int = PREVIEW_CHARS) -> str:
    """Short, whitespace-normalised preview for display (NOT for reranking)."""
    if not text:
        return ""
    text = ' '.join(text.split())[:max_chars]
    if len(text) >= max_chars:
        last_space = text.rfind(' ')
        if last_space > max_chars // 2:
            text = text[:last_space]
        text += "..."
    return text


def rerank_text(payload: Dict) -> str:
    """
    Text a cross-encoder should score for a Qdrant payload.

    Prefers the full chunk text (`text`) or full article (`article_text`);
    falls back to `text_preview` only for legacy points that were embedded
    before chunk text was stored in the payload.
    """
    if not payload:
        return ""
    content = payload.get("content")
    body = (
        payload.get("text")
        or payload.get("article_text")
        or payload.get("text_preview")
        or (content.get("reasoning") if isinstance(content, dict) else None)
        or (content.get("regeste") if isinstance(content, dict) else None)
        or ""
    )
    # Law articles: prepend the citation and title. Titles carry the defining terms
    # ("Konkurrenzverbot; Voraussetzungen") that the body text often does not repeat,
    # and the cross-encoder scores markedly better with them.
    if payload.get("sr_number") and payload.get("article_number"):
        header = f"{payload.get('abbreviation') or ('SR ' + str(payload['sr_number']))} Art. {payload['article_number']}"
        title = payload.get("article_title")
        if title:
            header += f" {title}"
        return f"{header}\n{body}"
    return body
