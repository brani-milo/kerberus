"""
Retrieval evaluation against a golden query set.

Measures, per query, whether the expected law articles / decisions appear in
the top-k TriadSearch results (recall@k, MRR). Pure scoring lives here so it
can be unit-tested; `scripts/eval_retrieval.py` wires it to a live index.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..database.document_store import normalize_alias

DEFAULT_GOLDEN = Path(__file__).parent.parent.parent / "tests" / "eval" / "golden_queries.json"


@dataclass
class Expectation:
    type: str                       # law | decision
    abbreviation: Optional[str] = None
    article: Optional[str] = None
    id_contains: Optional[str] = None
    optional: bool = False

    def label(self) -> str:
        return f"{self.abbreviation} Art. {self.article}" if self.type == "law" else f"decision~{self.id_contains}"

    def matches(self, result: Dict) -> bool:
        p = result.get("payload", {}) or {}
        if self.type == "law":
            abbrevs = {str(p.get("abbreviation", "")).upper()}
            abbrevs |= {str(v).upper() for v in (p.get("abbreviations_all") or {}).values()}
            return (self.abbreviation or "").upper() in abbrevs and str(p.get("article_number", "")).lower() == str(self.article).lower()
        needle = normalize_alias(self.id_contains or "")
        hay = normalize_alias(str(p.get("decision_id", "") or p.get("_original_id", "")))
        return bool(needle) and needle in hay


@dataclass
class GoldenQuery:
    id: str
    query: str
    language: Optional[str]
    expected: List[Expectation]


@dataclass
class QueryScore:
    id: str
    hits: Dict[str, Optional[int]] = field(default_factory=dict)   # label -> rank (1-based) or None
    optional: Dict[str, Optional[int]] = field(default_factory=dict)

    @property
    def recall(self) -> float:
        return (sum(1 for r in self.hits.values() if r) / len(self.hits)) if self.hits else 1.0

    @property
    def reciprocal_rank(self) -> float:
        ranks = [r for r in self.hits.values() if r]
        return 1.0 / min(ranks) if ranks else 0.0


@dataclass
class EvalReport:
    k: int
    scores: List[QueryScore]

    @property
    def law_recall(self) -> float:
        vals = [s.recall for s in self.scores if s.hits]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def mrr(self) -> float:
        return sum(s.reciprocal_rank for s in self.scores) / len(self.scores) if self.scores else 0.0

    @property
    def optional_recall(self) -> float:
        items = [r for s in self.scores for r in s.optional.values()]
        return (sum(1 for r in items if r) / len(items)) if items else 0.0

    def failures(self) -> List[str]:
        return [f"{s.id}: missing {label}" for s in self.scores for label, rank in s.hits.items() if not rank]

    def summary(self) -> str:
        lines = [f"Retrieval eval @k={self.k}: {len(self.scores)} queries",
                 f"  required recall: {self.law_recall:.2%}   MRR: {self.mrr:.3f}   optional recall: {self.optional_recall:.2%}"]
        lines += [f"  FAIL {f}" for f in self.failures()]
        return "\n".join(lines)


def load_golden(path: Path = DEFAULT_GOLDEN) -> tuple:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    queries = [
        GoldenQuery(id=q["id"], query=q["query"], language=q.get("language"),
                    expected=[Expectation(**e) for e in q["expected"]])
        for q in data["queries"]
    ]
    return int(data.get("k", 25)), queries


def score_query(q: GoldenQuery, codex_results: List[Dict], library_results: List[Dict], k: int) -> QueryScore:
    score = QueryScore(id=q.id)
    for exp in q.expected:
        pool = codex_results if exp.type == "law" else library_results
        rank = next((i + 1 for i, r in enumerate(pool[:k]) if exp.matches(r)), None)
        (score.optional if exp.optional else score.hits)[exp.label()] = rank
    return score


def score_all(queries: List[GoldenQuery], results: Dict[str, Dict], k: int) -> EvalReport:
    """results: query id -> {"codex": [...], "library": [...]} (TriadSearch result lists)."""
    scores = []
    for q in queries:
        r = results.get(q.id, {})
        scores.append(score_query(q, r.get("codex", []), r.get("library", []), k))
    return EvalReport(k=k, scores=scores)
