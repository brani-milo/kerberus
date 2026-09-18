"""
Neighbour expansion for law articles.

Articles that regulate one topic sit next to each other (OR 340, 340a, 340b,
340c). When first-stage retrieval finds one of them, the adjacent articles are
usually the "missing" ones a lawyer expects. This pulls them from the document
store (no vector search, no re-embedding needed) and adds them to the codex
results as `[relevance: context]` sources.

Especially useful for indexes whose vectors were built from imperfect parses:
an article whose embedding is poor is still reachable through its neighbours.
"""
import logging
import os
from typing import Dict, List

from .document_fetcher import fetch_full_law

logger = logging.getLogger(__name__)

NEIGHBOUR_EXPANSION_ENABLED = os.getenv("CODEX_NEIGHBOUR_EXPANSION", "true").lower() == "true"
NEIGHBOUR_SEEDS = int(os.getenv("CODEX_NEIGHBOUR_SEEDS", "3"))       # expand around the best N hits
NEIGHBOUR_RADIUS = int(os.getenv("CODEX_NEIGHBOUR_RADIUS", "1"))     # articles before/after each seed
NEIGHBOUR_MAX_ADDED = int(os.getenv("CODEX_NEIGHBOUR_MAX_ADDED", "6"))
NEIGHBOUR_SCORE_PENALTY = 3.0                                        # logits below the seed's score


def _distinct_articles(law: Dict) -> List[Dict]:
    """Articles of a law in document order, one entry per article number (paragraph chunks collapsed)."""
    seen = set()
    out = []
    for art in law.get("articles", []):
        num = str(art.get("article_number") or "")
        if not num or num in seen:
            continue
        seen.add(num)
        out.append(art)
    return out


def _as_result(art: Dict, law: Dict, seed: Dict, language: str) -> Dict:
    sr = str(law.get("sr_number"))
    num = str(art.get("article_number"))
    seed_score = float(seed.get("base_score", seed.get("final_score", 0.0)) or 0.0)
    payload = {
        "id": art.get("id") or f"SR_{sr}_Art_{num}_{language}",
        "base_id": f"SR_{sr}_Art_{num}",
        "sr_number": sr,
        "sr_name": law.get("title"),
        "abbreviation": law.get("abbreviation") or seed.get("payload", {}).get("abbreviation"),
        "abbreviations_all": law.get("abbreviations_all", {}),
        "article_number": num,
        "article_title": art.get("article_title"),
        "article_text": art.get("article_text", ""),
        "hierarchy_path": art.get("hierarchy_path"),
        "language": language,
        "source": seed.get("payload", {}).get("source", "fedlex"),
        "doc_type": seed.get("payload", {}).get("doc_type", "law"),
    }
    return {
        "id": payload["id"],
        "score": seed.get("score", 0.0),
        "base_score": seed_score - NEIGHBOUR_SCORE_PENALTY,
        "final_score": seed_score - NEIGHBOUR_SCORE_PENALTY,
        "relevance_tier": "context",
        "is_neighbour": True,
        "neighbour_of": f"{payload['abbreviation']} Art. {seed.get('payload', {}).get('article_number')}",
        "payload": payload,
    }


def expand_with_neighbours(
    results: List[Dict],
    max_seeds: int = NEIGHBOUR_SEEDS,
    radius: int = NEIGHBOUR_RADIUS,
    max_added: int = NEIGHBOUR_MAX_ADDED,
    fetch_law=fetch_full_law,
) -> List[Dict]:
    """Append adjacent articles of the top `max_seeds` law hits (deduplicated, capped)."""
    if not results or max_seeds <= 0 or radius <= 0:
        return results

    present: set = {(str(r.get("payload", {}).get("sr_number")), str(r.get("payload", {}).get("article_number")))
                    for r in results}
    added: List[Dict] = []

    for seed in [r for r in results if not r.get("is_neighbour")][:max_seeds]:
        p = seed.get("payload", {})
        sr, num, lang = str(p.get("sr_number") or ""), str(p.get("article_number") or ""), p.get("language") or "de"
        if not sr or not num:
            continue
        try:
            law = fetch_law(sr, lang)
        except Exception as e:
            logger.debug(f"Neighbour lookup failed for SR {sr} Art. {num}: {e}")
            continue
        if not law:
            continue
        arts = _distinct_articles(law)
        idx = next((i for i, a in enumerate(arts) if str(a.get("article_number")) == num), None)
        if idx is None:
            continue
        for offset in range(-radius, radius + 1):
            if offset == 0 or not (0 <= idx + offset < len(arts)):
                continue
            art = arts[idx + offset]
            key = (sr, str(art.get("article_number")))
            if key in present or len(art.get("article_text", "").strip()) < 20:
                continue
            present.add(key)
            added.append(_as_result(art, law, seed, law.get("language") or lang))
            if len(added) >= max_added:
                break
        if len(added) >= max_added:
            break

    if added:
        logger.info(f"Neighbour expansion: +{len(added)} adjacent articles "
                    f"({', '.join(a['payload']['abbreviation'] + ' ' + a['payload']['article_number'] for a in added)})")
    return results + added
