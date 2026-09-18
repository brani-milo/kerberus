"""
Model service: hosts BGE-M3 (embeddings) and BGE-Reranker-v2-M3 (cross-encoder).

    uvicorn src.services.models_api:app --host 0.0.0.0 --port 8080

Point the web/API containers at it with MODEL_SERVICE_URL=http://models:8080 and
they stop loading the models themselves (src/embedder/remote.py,
src/reranker/remote.py). One copy of the weights serves every replica, and the
CPU-heavy reranking no longer competes with request handling.
"""
import asyncio
import logging
import os
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

app = FastAPI(title="KERBERUS Model Service", version="1.0.0")

_embedder = None
_reranker = None
_lock = asyncio.Lock()


def get_local_embedder():
    global _embedder
    if _embedder is None:
        from ..embedder.bge_embedder import BGEEmbedder, get_best_device
        _embedder = BGEEmbedder(device=os.getenv("EMBEDDER_DEVICE") or get_best_device())
    return _embedder


def get_local_reranker():
    global _reranker
    if _reranker is None:
        from ..reranker.bge_reranker import BGEReranker
        _reranker = BGEReranker(device=os.getenv("RERANKER_DEVICE", "cpu"))
    return _reranker


@app.on_event("startup")
async def _warm_up():
    if os.getenv("MODEL_SERVICE_LAZY", "false").lower() != "true":
        await asyncio.to_thread(get_local_embedder)
        await asyncio.to_thread(get_local_reranker)
        logger.info("Model service ready")


# ---- schemas -----------------------------------------------------------------

class EmbedRequest(BaseModel):
    texts: List[str] = Field(..., min_length=1, max_length=256)


class Embedding(BaseModel):
    dense: List[float]
    sparse: Dict[str, float]


class EmbedResponse(BaseModel):
    embeddings: List[Embedding]


class RerankRequest(BaseModel):
    query: str
    texts: List[str] = Field(..., max_length=1000)
    max_length: Optional[int] = None


class RerankResponse(BaseModel):
    scores: List[float]


def _to_sparse(weights) -> Dict[str, float]:
    return {str(k): float(v) for k, v in (weights or {}).items()}


# ---- endpoints ---------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "embedder_loaded": _embedder is not None, "reranker_loaded": _reranker is not None}


@app.post("/embed", response_model=EmbedResponse)
async def embed(req: EmbedRequest):
    embedder = await asyncio.to_thread(get_local_embedder)
    try:
        if len(req.texts) == 1:
            results = [await asyncio.to_thread(embedder._encode_single, req.texts[0])]
        else:
            results = await asyncio.to_thread(embedder.encode_batch, req.texts)
    except Exception as e:
        logger.error(f"Embedding failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Embedding failed: {e}")
    return EmbedResponse(embeddings=[
        Embedding(dense=[float(x) for x in r["dense"]], sparse=_to_sparse(r["sparse"])) for r in results
    ])


@app.post("/rerank", response_model=RerankResponse)
async def rerank(req: RerankRequest):
    if not req.texts:
        return RerankResponse(scores=[])
    reranker = await asyncio.to_thread(get_local_reranker)
    pairs = [[req.query, t] for t in req.texts]
    try:
        scores = await asyncio.to_thread(reranker._compute_scores, pairs, req.max_length)
    except Exception as e:
        logger.error(f"Reranking failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Reranking failed: {e}")
    return RerankResponse(scores=[float(s) for s in scores])
