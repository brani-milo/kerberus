"""Chat endpoints over a fake LegalQueryService: JSON response, SSE framing, error mapping."""
import json

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.deps import get_db, check_rate_limit
from src.api.routes.chat import get_service
from src.pipeline.service import PipelineEvent, LegalAnswer


def make_answer(**over):
    base = dict(
        answer="Antwort", raw_answer="Antwort", consistency="CONSISTENT", confidence="high", language="de",
        sources=[{"id": "c1", "type": "law", "citation": "OR Art. 337", "language": "de", "url": None, "relevance_score": 0.9}],
        codex_results=[{"id": "c1"}], library_results=[], codex_confidence="HIGH", library_confidence="NONE",
        overall_confidence="HIGH",
        token_usage={"input_tokens": 100, "output_tokens": 40, "total_tokens": 140, "total_cost_chf": 0.01,
                     "stages": {"guard": 10, "reformulate": 10, "analyze": 120}},
        processing_time_ms=12.5, reformulated_query="r", enhanced_query="e", pii_detected=False, pii_types=[],
        followup_used=False,
    )
    base.update(over)
    return LegalAnswer(**base)


class FakeService:
    def __init__(self, events):
        self.events = events
        self.calls = []

    async def run(self, query, **kw):
        self.calls.append((query, kw))
        for e in self.events:
            yield e


class FakeDB:
    def __init__(self):
        self.usage = []

    def record_token_usage(self, user_id, usage_record):
        self.usage.append((user_id, usage_record))
        return 1


@pytest.fixture
def client():
    db = FakeDB()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[check_rate_limit] = lambda: {"user_id": "u1", "email": "t@x.ch"}
    yield TestClient(app), db
    app.dependency_overrides.clear()


def success_events():
    answer = make_answer()
    return [
        PipelineEvent("guard", "processing"),
        PipelineEvent("guard", "complete", {"language": "de"}),
        PipelineEvent("search", "complete", {"laws": 1, "decisions": 0, "codex_confidence": "HIGH",
                                             "library_confidence": "NONE", "overall_confidence": "HIGH",
                                             "codex_results": [{"heavy": True}], "library_results": []}),
        PipelineEvent("analyze", "chunk", {"chunk": "Ant"}),
        PipelineEvent("analyze", "chunk", {"chunk": "wort"}),
        PipelineEvent("complete", "complete", {"consistency": "CONSISTENT", "confidence": "high",
                                               "sources": answer.sources, "token_usage": answer.token_usage,
                                               "answer_obj": answer}),
    ]


def test_chat_json_response_and_usage_recording(client):
    c, db = client
    svc = FakeService(success_events())
    app.dependency_overrides[get_service] = lambda: svc
    r = c.post("/chat", json={"query": "Fristlose Kündigung?", "language": "de", "max_laws": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "Antwort" and body["sources"][0]["citation"] == "OR Art. 337"
    assert body["token_usage"]["search_confidence"] == {"codex": "HIGH", "library": "NONE", "overall": "HIGH"}
    assert db.usage[0][1]["input_tokens"] == 100 and db.usage[0][1]["output_tokens"] == 40
    query, kw = svc.calls[0]
    assert kw["options"].stream is False and kw["options"].language == "de" and kw["options"].max_laws == 5


@pytest.mark.parametrize("status,http", [("blocked", 400), ("no_sources", 404), ("failed", 503)])
def test_chat_error_mapping(client, status, http):
    c, _ = client
    app.dependency_overrides[get_service] = lambda: FakeService([PipelineEvent("error", status, {"message": "why"})])
    r = c.post("/chat", json={"query": "irgendwas"})
    assert r.status_code == http and r.json()["detail"] == "why"


def test_chat_stream_emits_valid_json_events(client):
    c, db = client
    app.dependency_overrides[get_service] = lambda: FakeService(success_events())
    with c.stream("POST", "/chat/stream", json={"query": "Fristlose Kündigung?"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = [ln for ln in r.iter_lines() if ln]
    assert lines[-1] == "data: [DONE]"
    payloads = [json.loads(ln[len("data: "):]) for ln in lines[:-1]]
    assert all("stage" in p and "status" in p for p in payloads)
    chunks = "".join(p["chunk"] for p in payloads if p.get("status") == "chunk")
    assert chunks == "Antwort"
    assert not any("codex_results" in p for p in payloads)  # heavy objects never leave the process
    assert payloads[-1]["stage"] == "complete" and "answer_obj" not in payloads[-1]
    assert db.usage, "usage recorded for streaming too"


def test_chat_requires_query(client):
    c, _ = client
    assert c.post("/chat", json={"query": "ab"}).status_code == 422
