"""
RemoteEmbedder: same interface as BGEEmbedder, backed by the model service.

Selected automatically by `get_embedder()` when MODEL_SERVICE_URL is set.
"""
import asyncio
import logging
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)


class RemoteEmbedder:
    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.device = "remote"
        self.model_name = "BAAI/bge-m3 (remote)"
        self._session = requests.Session()
        logger.info(f"Using remote embedder at {self.base_url}")

    def _post(self, texts: List[str]) -> List[Dict]:
        resp = self._session.post(f"{self.base_url}/embed", json={"texts": texts}, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["embeddings"]

    def _encode_single(self, text: str) -> Dict:
        try:
            return self._post([text])[0]
        except Exception as e:
            raise RuntimeError(f"Remote embedding failed: {e}")

    async def encode_async(self, text: str) -> Dict:
        return await asyncio.to_thread(self._encode_single, text)

    def encode_batch(self, texts: List[str], batch_size: int = 32, show_progress: bool = False) -> List[Dict]:
        out: List[Dict] = []
        for i in range(0, len(texts), batch_size):
            out.extend(self._post(texts[i:i + batch_size]))
            if show_progress:
                logger.info(f"Encoded {min(i + batch_size, len(texts))}/{len(texts)} documents (remote)")
        return out

    def get_embedding_dimension(self) -> int:
        return 1024
