from src.search.neighbours import expand_with_neighbours


def fake_law(sr, lang):
    if sr != "220":
        return None
    arts = [{"id": f"SR_220_Art_{n}_de", "article_number": n, "article_title": t, "article_text": f"Text of {n} " * 5}
            for n, t in [("339d", "c. Ersatzleistungen"), ("340", "VII. Konkurrenzverbot 1. Voraussetzungen"),
                         ("340a", "2. Beschränkungen"), ("340b", "3. Folgen"), ("340c", "4. Wegfall"), ("341", "Unverzichtbarkeit")]]
    arts.insert(2, {"id": "SR_220_Art_340_p2_de", "article_number": "340", "article_text": "paragraph chunk"})  # paragraph chunk
    return {"sr_number": "220", "language": "de", "title": "Obligationenrecht", "abbreviation": "OR", "articles": arts}


def hit(num, score):
    return {"id": f"SR_220_Art_{num}_de", "base_score": score, "final_score": score, "relevance_tier": "high",
            "payload": {"sr_number": "220", "article_number": num, "language": "de", "abbreviation": "OR", "doc_type": "law"}}


def test_adjacent_articles_are_added_once_with_context_tier():
    results = [hit("340c", 2.4), hit("340b", 1.0)]
    out = expand_with_neighbours(results, max_seeds=3, radius=1, fetch_law=fake_law)
    added = [r for r in out if r.get("is_neighbour")]
    assert [r["payload"]["article_number"] for r in added] == ["341", "340a"]   # 340b/340c already present, no duplicates
    assert all(r["relevance_tier"] == "context" and r["base_score"] < 2.4 for r in added)
    assert added[1]["payload"]["article_title"] == "2. Beschränkungen" and added[1]["payload"]["sr_name"] == "Obligationenrecht"
    assert out[:2] == results                                                   # originals untouched, in order


def test_expansion_is_capped_and_tolerates_unknown_laws():
    results = [hit("340", 2.0), {"id": "x", "base_score": 1.0, "payload": {"sr_number": "999", "article_number": "1", "language": "de"}}]
    out = expand_with_neighbours(results, max_seeds=3, radius=1, max_added=1, fetch_law=fake_law)
    assert len(out) == 3 and out[2]["payload"]["article_number"] == "339d"
    assert expand_with_neighbours([], fetch_law=fake_law) == []
