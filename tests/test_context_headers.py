"""build_context: relevance tier + law name/hierarchy reach the model; budgets hold."""
from src.llm.pipeline import LegalPipeline


class FakeAssembler:
    def assemble(self, codex_results, library_results, fetch_full_documents=True):
        return "", {}


def make_pipeline():
    p = LegalPipeline.__new__(LegalPipeline)   # skip __init__ (no LLM client / Qdrant)
    p.context_assembler = FakeAssembler()
    return p


def test_law_header_carries_name_hierarchy_and_tier():
    p = make_pipeline()
    codex = [{
        "id": "1", "score": 0.9, "relevance_tier": "high",
        "payload": {"abbreviation": "DBG", "article_number": "27", "article_title": "Geschäftsmässig begründete Kosten",
                    "sr_number": "642.11", "language": "de", "article_text": "Bei selbständiger Erwerbstätigkeit...",
                    "sr_name": "Bundesgesetz über die direkte Bundessteuer",
                    "hierarchy_path": "Zweiter Teil > Zweiter Titel > 3. Kapitel"},
    }, {
        "id": "2", "score": 0.1, "relevance_tier": "low",
        "payload": {"abbreviation": "BGBB", "article_number": "11", "sr_number": "211.412.11", "language": "de",
                    "article_text": "Erbteilung...", "sr_name": "Bundesgesetz über das bäuerliche Bodenrecht"},
    }]
    laws, decisions, meta = p.build_context(codex_results=codex, library_results=[], max_laws=10, max_decisions=5)
    assert "### DBG Art. 27 - Geschäftsmässig begründete Kosten (SR 642.11, DE) [relevance: high]" in laws
    assert "Gesetz/Loi/Legge: Bundesgesetz über die direkte Bundessteuer | Systematik: Zweiter Teil > Zweiter Titel > 3. Kapitel" in laws
    assert "[relevance: low]" in laws and "bäuerliche Bodenrecht" in laws
    assert meta["laws_count"] == 2 and "Keine relevanten Entscheide" in decisions


def test_decision_header_uses_full_content_and_tier():
    p = make_pipeline()
    library = [{"id": "d1", "score": 0.8, "relevance_tier": "medium", "full_content": "=== DECISION ===\nfull text here",
                "payload": {"decision_id": "CH_BGE_001_BGE-130-III-28_2003", "year": 2003, "court": "CH_BGE", "language": "de"}}]
    laws, decisions, meta = p.build_context(codex_results=[], library_results=library)
    assert "[relevance: medium]" in decisions and "full text here" in decisions
    assert meta["decisions_count"] == 1
