"""Model service + remote clients, with fake models (no weights, real HTTP on a local port)."""
import socket
import threading
import time

import pytest
import uvicorn

from src.services import models_api
from src.embedder.remote import RemoteEmbedder
from src.reranker.remote import RemoteReranker


class FakeEmbedder:
    def _encode_single(self, text):
        return {"dense": [float(len(text))] * 4, "sparse": {"1": 0.5}}

    def encode_batch(self, texts, batch_size=32, show_progress=False):
        return [self._encode_single(t) for t in texts]


class FakeReranker:
    def _compute_scores(self, pairs, max_length=None):
        return [float(len(t)) / 10 for _, t in pairs]


@pytest.fixture(scope="module")
def service_url(module_mocker=None):
    models_api._embedder = FakeEmbedder()
    models_api._reranker = FakeReranker()
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    config = uvicorn.Config(models_api.app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_health_and_embed_roundtrip(service_url):
    import requests
    assert requests.get(f"{service_url}/health").json()["embedder_loaded"] is True
    emb = RemoteEmbedder(service_url)
    single = emb._encode_single("abc")
    assert single["dense"] == [3.0] * 4 and single["sparse"] == {"1": 0.5}
    batch = emb.encode_batch(["a", "bb", "ccc"], batch_size=2)
    assert [e["dense"][0] for e in batch] == [1.0, 2.0, 3.0]
    import asyncio
    assert asyncio.run(emb.encode_async("xy"))["dense"][0] == 2.0


def test_remote_reranker_keeps_scoring_semantics(service_url):
    rr = RemoteReranker(service_url)
    docs = [{"text": "short", "payload": {"year": 2000}}, {"text": "a much longer document", "payload": {"year": 2024}}]
    out = rr.rerank_with_confidence("q", docs, top_k=2)
    assert out["results"][0]["text"] == "a much longer document"        # higher cross-encoder score first
    assert "final_score" in out["results"][0] and out["confidence"] in ("HIGH", "MEDIUM", "LOW")
    assert rr.rerank("q", [], top_k=5) == []


def test_factories_pick_remote_when_configured(service_url, monkeypatch):
    from src.config import get_settings
    from src.embedder import bge_embedder
    from src.reranker import bge_reranker
    monkeypatch.setenv("MODEL_SERVICE_URL", service_url)
    get_settings.cache_clear()
    monkeypatch.setattr(bge_embedder, "_embedder_instance", None)
    monkeypatch.setattr(bge_reranker, "_reranker_instance", None)
    try:
        assert isinstance(bge_embedder.get_embedder(), RemoteEmbedder)
        assert isinstance(bge_reranker.get_reranker(), RemoteReranker)
    finally:
        get_settings.cache_clear()
