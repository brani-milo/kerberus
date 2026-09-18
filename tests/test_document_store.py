"""Document store + fetcher tests (SQLite engine, sample data under data/parsed)."""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from src.database.document_store import DocumentStore, DocumentRecord, decision_record, law_record, normalize_alias
from src.search import document_fetcher as df

SAMPLE = Path(__file__).parent.parent / "data" / "parsed"


@pytest.fixture
def store():
    s = DocumentStore(engine=create_engine("sqlite:///:memory:"))
    s.init_schema()
    return s


def test_normalize_alias():
    assert normalize_alias("BGE 102 Ia 35") == normalize_alias("bge-102-IA-35") == "BGE-102-IA-35"
    assert normalize_alias("2C_242/2010") == "2C-242-2010"


def test_upsert_and_lookup_by_id_alias_and_normalised(store):
    rec = DocumentRecord(doc_id="CH_BGE_001_BGE-97-I-878_1971", collection="library",
                         content={"id": "x", "content": {"regeste": "R"}}, language="de",
                         aliases=["BGE 97 I 878"])
    assert store.upsert_documents([rec]) == 1
    assert store.get("CH_BGE_001_BGE-97-I-878_1971")["id"] == "x"
    assert store.get_decision("bge-97-i-878")["id"] == "x"
    assert store.get_decision("BGE 97 I 878")["id"] == "x"
    assert store.get_decision("missing") is None
    # upsert replaces
    rec.content = {"id": "y"}
    store.upsert_documents([rec])
    assert store.get("CH_BGE_001_BGE-97-I-878_1971")["id"] == "y"
    assert store.count("library") == 1


def test_law_record_language_fallback(store):
    arts = [{"id": "SR_220_Art_1_de", "sr_number": "220", "sr_name": "OR", "abbreviation": "OR",
             "article_number": "1", "article_text": "Text"}]
    store.upsert_documents([law_record("220", "de", arts)])
    assert store.get_law("220", "it")["language"] == "de"   # falls back
    assert store.get_law("220")["articles"][0]["article_number"] == "1"
    assert store.get_law("999") is None


@pytest.mark.skipif(not (SAMPLE / "ticino").exists(), reason="sample data not present")
def test_decision_record_from_sample_files(store):
    path = next((SAMPLE / "ticino").glob("*.json"))
    doc = json.load(open(path, encoding="utf-8"))
    store.upsert_documents([decision_record(doc, file_stem=path.stem, source="ticino")])
    # Ticino ids are citations ("2C_242/2010"), file stems are different: both resolve
    assert store.get_decision(doc["id"])["file_name"] == doc["file_name"]
    assert store.get_decision(path.stem)["id"] == doc["id"]


def test_format_decision_respects_budget():
    decision = {"id": "D", "court": "CH_BGE", "date": "2020-01-01",
                "content": {"regeste": "R" * 500, "facts": "F" * 20000, "reasoning": "E" * 50000, "decision": "D" * 3000}}
    out = df.format_decision_for_llm(decision, max_chars=8000)
    assert len(out) <= 8600  # small slack for headers/markers
    assert "REGESTE" in out and "REASONING" in out
    full = df.format_decision_for_llm(decision, max_chars=200000)
    assert "F" * 20000 in full


def test_format_law_handles_store_and_legacy_fields():
    law = {"sr_number": "220", "title": "OR", "abbreviation": "OR",
           "articles": [{"article_number": "337", "article_title": "Fristlose", "article_text": "T1"},
                        {"article_number": "1", "title": "Legacy", "text": "T0"}]}
    out = df.format_law_for_llm(law, ["337"])
    assert "Art. 337 Fristlose" in out and "T1" in out and "T0" not in out
    assert "Legacy" in df.format_law_for_llm(law)


@pytest.mark.skipif(not (SAMPLE / "federal").exists(), reason="sample data not present")
def test_fetcher_file_fallback_uses_index(monkeypatch):
    monkeypatch.setattr(df, "_store", lambda: None)  # force filesystem path
    df.clear_caches()
    stem = next((SAMPLE / "federal").glob("*.json")).stem
    doc = df.fetch_full_decision(stem)
    assert doc and doc["id"] == stem
    # normalised / citation-only lookups resolve through the in-memory index
    assert df.fetch_full_decision(stem.lower()) is not None
    assert df.fetch_full_decision("BGE-97-I-878") is not None
    law = df.fetch_full_law("220", "fr")
    assert law and law["language"] == "fr" and law["articles"]
    assert df.fetch_full_law("220", "rm")["language"] == "de"


def test_enrich_uses_store(monkeypatch, store):
    store.upsert_documents([DocumentRecord(doc_id="X1", collection="library",
                                           content={"id": "X1", "court": "C", "date": "d",
                                                    "content": {"regeste": "the regeste"}})])
    monkeypatch.setattr(df, "_store", lambda: store)
    df.clear_caches()
    results = [{"payload": {"decision_id": "x1", "text_preview": "prev"}}]
    df.enrich_results_with_full_content(results, "library")
    assert "the regeste" in results[0]["full_content"]
    assert results[0]["full_document"]["id"] == "X1"
