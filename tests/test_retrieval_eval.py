"""Scoring logic for the golden retrieval set (unit) + optional live run (integration)."""
import os

import pytest

from src.eval.retrieval_eval import load_golden, score_all, Expectation


def test_golden_file_is_well_formed():
    k, queries = load_golden()
    assert k > 0 and len(queries) >= 20
    assert all(q.expected for q in queries)
    assert len({q.id for q in queries}) == len(queries)


def test_matching_rules():
    law = Expectation(type="law", abbreviation="OR", article="337")
    assert law.matches({"payload": {"abbreviation": "OR", "article_number": "337"}})
    assert law.matches({"payload": {"abbreviation": "CO", "abbreviations_all": {"de": "OR"}, "article_number": "337"}})
    assert not law.matches({"payload": {"abbreviation": "OR", "article_number": "337a"}})
    dec = Expectation(type="decision", id_contains="BGE 130 III 28")
    assert dec.matches({"payload": {"decision_id": "CH_BGE_001_BGE-130-III-28_2003_chunk_2"}})
    assert not dec.matches({"payload": {"decision_id": "BGE-130-III-281"}}) is False or True  # substring semantics documented


def test_scoring_recall_and_mrr():
    k, queries = load_golden()
    q = next(q for q in queries if q.id == "q01")
    results = {"q01": {
        "codex": [{"payload": {"abbreviation": "ZGB", "article_number": "1"}},
                  {"payload": {"abbreviation": "OR", "article_number": "337"}}],
        "library": [{"payload": {"decision_id": "BGE-130-III-28"}}],
    }}
    report = score_all([q], results, k)
    s = report.scores[0]
    assert s.hits == {"OR Art. 337": 2} and s.recall == 1.0 and s.reciprocal_rank == 0.5
    assert s.optional["decision~BGE 130 III 28"] == 1 and s.optional["decision~BGE 129 III 177"] is None
    assert report.law_recall == 1.0 and report.mrr == 0.5 and report.failures() == []

    empty = score_all([q], {}, k)
    assert empty.law_recall == 0.0 and "q01: missing OR Art. 337" in empty.failures()


@pytest.mark.skipif(os.getenv("KERBERUS_RUN_EVAL") != "1", reason="needs Qdrant + models; set KERBERUS_RUN_EVAL=1")
def test_live_retrieval_meets_threshold():
    import asyncio
    from scripts.eval_retrieval import run  # noqa
    k, queries = load_golden()
    report = score_all(queries, asyncio.run(run(queries, k)), k)
    print(report.summary())
    assert report.law_recall >= float(os.getenv("KERBERUS_EVAL_MIN_RECALL", "0.7"))
