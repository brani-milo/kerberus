"""
Relevance gating for reranked results.

The cross-encoder (BGE-reranker-v2-m3) returns raw logits. Calibration on
Swiss legal text: on-point articles score roughly -6 .. +3, tangentially
related ones around -6 .. -8, unrelated ones -10 and below. The old pipeline
ignored the scores and filled fixed quotas (15 laws + 10 ordinances + 10
decisions), so unrelated articles reached the LLM and got cited.

Rule: keep a result only if its score is within `top_margin` logits of the best
result AND above the absolute floor `min_logit`; always keep `min_keep`.
Every kept result is tagged with `relevance_tier` (high / medium / low) so the
context builder can tell the model how much weight a source deserves.
"""
import logging
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MIN_LOGIT = float(os.getenv("RERANK_MIN_LOGIT", "-9.0"))
DEFAULT_TOP_MARGIN = float(os.getenv("RERANK_TOP_MARGIN", "7.0"))
HIGH_TIER_GAP = 2.5      # within 2.5 logits of the top: high
MEDIUM_TIER_GAP = 5.0    # within 5.0: medium; beyond: low

# Confidence of a lane from its best raw score (used by the reranker)
CONFIDENCE_HIGH_LOGIT = float(os.getenv("RERANK_CONFIDENCE_HIGH", "0.0"))
CONFIDENCE_MEDIUM_LOGIT = float(os.getenv("RERANK_CONFIDENCE_MEDIUM", "-5.5"))

# When even the best hit is weak (below the MEDIUM line) the lane probably has
# nothing on point; keep only results very close to the top so a weak lane
# contributes a few [relevance: low] sources instead of a page of noise.
WEAK_TOP_MARGIN = float(os.getenv("RERANK_WEAK_TOP_MARGIN", "1.0"))
# Extra margin for results from the SAME law as the best hit: neighbouring articles
# are context, never the unrelated-law noise the gate exists to remove.
SAME_GROUP_BONUS = float(os.getenv("RERANK_SAME_LAW_BONUS", "3.0"))
WEAK_MAX_KEEP = int(os.getenv("RERANK_WEAK_MAX_KEEP", "3"))


def _score(result: Dict, key: str) -> float:
    value = result.get(key)
    if value is None:
        value = result.get("rerank_score", result.get("final_score", result.get("score", 0.0)))
    return float(value or 0.0)


def tier_for(score: float, top: float) -> str:
    gap = top - score
    if gap <= HIGH_TIER_GAP:
        return "high"
    if gap <= MEDIUM_TIER_GAP:
        return "medium"
    return "low"


def confidence_for(top_score: Optional[float]) -> str:
    if top_score is None:
        return "NONE"
    if top_score >= CONFIDENCE_HIGH_LOGIT:
        return "HIGH"
    if top_score >= CONFIDENCE_MEDIUM_LOGIT:
        return "MEDIUM"
    return "LOW"


def gate_by_relevance(
    results: List[Dict],
    *,
    min_keep: int = 3,
    max_keep: Optional[int] = None,
    min_logit: float = DEFAULT_MIN_LOGIT,
    top_margin: float = DEFAULT_TOP_MARGIN,
    top_reference: Optional[float] = None,
    score_key: str = "base_score",
    label: str = "",
    group_key=None,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Split reranked results into (kept, dropped).

    Args:
        results: reranked results (any order; sorted here by score desc)
        min_keep: never return fewer than this many (if available)
        max_keep: hard cap after gating
        top_reference: use this as the "top" score instead of the best in `results`
                       (e.g. gate ordinances against the best law)
        group_key: callable(result) -> hashable; results in the same group as the
                   best result get SAME_GROUP_BONUS extra margin (e.g. same law)
    """
    if not results:
        return [], []

    ordered = sorted(results, key=lambda r: _score(r, score_key), reverse=True)
    top = top_reference if top_reference is not None else _score(ordered[0], score_key)
    weak = top < CONFIDENCE_MEDIUM_LOGIT
    margin = WEAK_TOP_MARGIN if weak else top_margin
    threshold = max(min_logit, top - margin)
    if weak:
        max_keep = min(max_keep, WEAK_MAX_KEEP) if max_keep is not None else WEAK_MAX_KEEP
    top_group = group_key(ordered[0]) if (group_key and not weak) else None

    kept, dropped = [], []
    for i, r in enumerate(ordered):
        s = _score(r, score_key)
        local_threshold = threshold
        if top_group is not None and group_key(r) == top_group:
            local_threshold = max(min_logit, threshold - SAME_GROUP_BONUS)
        if s >= local_threshold or i < min_keep:
            r["relevance_tier"] = tier_for(s, top)
            r["relevance_gap"] = round(top - s, 2)
            kept.append(r)
        else:
            dropped.append(r)

    if max_keep is not None and len(kept) > max_keep:
        dropped = kept[max_keep:] + dropped
        kept = kept[:max_keep]

    if dropped:
        logger.info(
            f"Relevance gate{(' ' + label) if label else ''}: kept {len(kept)}, dropped {len(dropped)} "
            f"(top={top:.2f}, threshold={threshold:.2f}{', weak lane' if weak else ''})"
        )
    return kept, dropped
