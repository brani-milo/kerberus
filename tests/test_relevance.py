"""Relevance gating, tiers, confidence, and word-boundary query expansion."""
from src.search.relevance import gate_by_relevance, tier_for, confidence_for


def R(score, name):
    return {"base_score": score, "payload": {"name": name}}


def test_gate_drops_unrelated_keeps_related():
    results = [R(1.8, "OR 337"), R(-6.3, "OR 336c"), R(-11.0, "BGBB 11"), R(-10.2, "StGB 139")]
    kept, dropped = gate_by_relevance(results, min_keep=1, min_logit=-9.0, top_margin=9.0)
    assert [r["payload"]["name"] for r in kept] == ["OR 337", "OR 336c"]
    assert [r["payload"]["name"] for r in dropped] == ["StGB 139", "BGBB 11"]
    assert kept[0]["relevance_tier"] == "high" and kept[1]["relevance_tier"] == "low"


def test_gate_respects_min_keep_and_max_keep():
    results = [R(-5.0, "a"), R(-12.0, "b"), R(-13.0, "c")]
    kept, dropped = gate_by_relevance(results, min_keep=2)
    assert [r["payload"]["name"] for r in kept] == ["a", "b"] and len(dropped) == 1
    kept, dropped = gate_by_relevance([R(0, "x"), R(-0.5, "y"), R(-1, "z")], min_keep=1, max_keep=2)
    assert len(kept) == 2 and len(dropped) == 1


def test_gate_with_external_top_reference():
    # ordinances gated against the best LAW score: an unrelated ordinance lane contributes nothing
    ordinances = [R(-10.5, "SVG-VO"), R(-11.0, "LwG-VO")]
    kept, dropped = gate_by_relevance(ordinances, min_keep=0, top_reference=1.8)
    assert kept == [] and len(dropped) == 2
    kept, _ = gate_by_relevance([R(-2.0, "DBGV")], min_keep=0, top_reference=1.8)
    assert kept and kept[0]["relevance_tier"] == "medium"


def test_tiers_and_confidence():
    assert tier_for(0, 1) == "high" and tier_for(-4, 0) == "medium" and tier_for(-6, 0) == "low"
    assert confidence_for(1.2) == "HIGH" and confidence_for(-5.4) == "MEDIUM" and confidence_for(-5.9) == "LOW"
    assert confidence_for(None) == "NONE"


def test_query_expansion_uses_whole_words():
    from src.search.triad_search import expand_query_with_related_domains as expand
    assert expand("Wie viele Stunden darf ich gehen?") == "Wie viele Stunden darf ich gehen?"   # 'ehe' inside 'gehen'
    assert "ZGB" in expand("Was gilt bei der Ehe für den Güterstand?")                             # whole word 'ehe'
    assert expand("Der Baum steht auf dem Grundstück") == "Der Baum steht auf dem Grundstück"      # 'bau' inside 'Baum'
    assert "RPG" in expand("Brauche ich eine Baubewilligung für ein Gartenhaus?")


def test_dedupe_keeps_distinct_articles_of_the_same_law():
    from src.search.mmr import deduplicate_by_document
    results = [
        {"base_score": 2.0, "payload": {"sr_number": "220", "base_id": "SR_220_Art_337", "article_number": "337", "language": "de"}},
        {"base_score": 1.5, "payload": {"sr_number": "220", "base_id": "SR_220_Art_337", "article_number": "337", "language": "fr"}},   # same article, other language
        {"base_score": 1.0, "payload": {"sr_number": "220", "base_id": "SR_220_Art_337a", "article_number": "337a", "language": "de"}},
        {"base_score": 0.5, "payload": {"sr_number": "220", "article_number": "336c", "language": "de"}},                             # legacy payload without base_id
        {"base_score": 0.4, "payload": {"sr_number": "220", "article_number": "336c", "language": "it"}},
    ]
    kept = deduplicate_by_document(results, top_k=10)
    assert [(r["payload"]["article_number"], r["payload"]["language"]) for r in kept] == [("337", "de"), ("337a", "de"), ("336c", "de")]


def test_mmr_does_not_force_one_article_per_law():
    from src.search.mmr import apply_mmr
    # RRF-like scores: rank 1/2 are two OR articles, then 60 articles of other laws with lower scores
    cands = [{"score": 1 / (60 + 1), "payload": {"sr_number": "220", "base_id": "SR_220_Art_337"}},
             {"score": 1 / (60 + 2), "payload": {"sr_number": "220", "base_id": "SR_220_Art_337a"}}]
    cands += [{"score": 1 / (60 + 3 + i), "payload": {"sr_number": f"9{i}", "base_id": f"SR_9{i}_Art_1"}} for i in range(60)]
    selected = apply_mmr(cands, query_embedding=None, lambda_param=0.98, top_k=5)
    assert [c["payload"]["base_id"] for c in selected[:2]] == ["SR_220_Art_337", "SR_220_Art_337a"]


def test_weak_lane_keeps_only_near_top():
    # best hit is weak (-6.9): nothing on point -> keep only results within 1.5 of the top (plus min_keep)
    results = [R(-6.9, "a"), R(-7.3, "b"), R(-7.6, "c"), R(-7.8, "d"), R(-8.9, "e")]
    kept, dropped = gate_by_relevance(results, min_keep=1)
    assert [r["payload"]["name"] for r in kept] == ["a", "b", "c"]      # within 1.0 of top, capped at 3
    assert all(r["relevance_tier"] in ("high", "medium", "low") for r in kept)
    # strong top: the normal 7-logit margin applies
    kept, _ = gate_by_relevance([R(2.0, "x"), R(-4.0, "y"), R(-6.5, "z")], min_keep=1)
    assert len(kept) == 2


def test_dedupe_collapses_paragraph_chunks_of_one_article():
    from src.search.mmr import deduplicate_by_document
    results = [{"base_score": 1.0, "payload": {"sr_number": "101", "base_id": "SR_101_Art_196_p14", "article_number": "196"}},
               {"base_score": 0.9, "payload": {"sr_number": "101", "base_id": "SR_101_Art_196_p1", "article_number": "196"}},
               {"base_score": 0.8, "payload": {"sr_number": "101", "base_id": "SR_101_Art_197", "article_number": "197"}}]
    assert [r["payload"]["base_id"] for r in deduplicate_by_document(results, top_k=10)] == ["SR_101_Art_196_p14", "SR_101_Art_197"]


def test_local_reranker_serialises_model_access_and_survives_missing_scores():
    import threading, time
    from src.reranker.bge_reranker import BGEReranker
    rr = BGEReranker.__new__(BGEReranker)
    rr.model_name, rr.device, rr.max_length, rr._lock = "fake", "cpu", 512, threading.Lock()
    state = {"inside": 0, "overlap": False}

    class FakeModel:
        def compute_score(self, pairs, max_length=None):
            state["inside"] += 1
            if state["inside"] > 1:
                state["overlap"] = True
            time.sleep(0.01)
            state["inside"] -= 1
            return [0.0] * len(pairs)
    rr._reranker = FakeModel()
    threads = [threading.Thread(target=rr._compute_scores, args=([["q", "d"]] * 3,)) for _ in range(8)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert state["overlap"] is False

    # documents without any score (rerank fallback path) must not crash confidence computation
    out = rr.rerank_with_confidence("q", [{"text": "a", "payload": {}}], top_k=1)
    assert out["confidence"] in ("HIGH", "MEDIUM", "LOW", "NONE")


def test_same_law_neighbours_get_extra_margin():
    top = R(2.4, "OR 340c"); top["payload"]["sr_number"] = "220"
    neighbour = R(-6.9, "OR 340"); neighbour["payload"]["sr_number"] = "220"
    other = R(-6.9, "BGBB 11"); other["payload"]["sr_number"] = "211.412.11"
    kept, dropped = gate_by_relevance([top, neighbour, other], min_keep=1, group_key=lambda r: r["payload"]["sr_number"])
    assert [r["payload"]["name"] for r in kept] == ["OR 340c", "OR 340"]
    assert [r["payload"]["name"] for r in dropped] == ["BGBB 11"]


def test_rerank_text_prefixes_law_citation_and_title():
    from src.embedder.chunking import rerank_text
    p = {"sr_number": "220", "abbreviation": "OR", "article_number": "340", "article_title": "Konkurrenzverbot; Voraussetzungen",
         "article_text": "1 Der handlungsfähige Arbeitnehmer kann..."}
    assert rerank_text(p).startswith("OR Art. 340 Konkurrenzverbot; Voraussetzungen\n1 Der")
    assert rerank_text({"text": "chunk", "decision_id": "x"}) == "chunk"


def test_codex_artefacts_are_filtered():
    from src.search.triad_search import _plausible_codex_point
    assert _plausible_codex_point({"payload": {"article_number": "340c"}})
    assert _plausible_codex_point({"payload": {"article_number": "1186"}})           # last article of the OR
    assert not _plausible_codex_point({"payload": {"article_number": "197135"}})     # parser artefact
    assert not _plausible_codex_point({"payload": {"article_number": "12", "parse_missing": True}})
    assert _plausible_codex_point({"payload": {}})
