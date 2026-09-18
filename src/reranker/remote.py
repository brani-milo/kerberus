"""
RemoteReranker: BGEReranker's scoring (recency boost, confidence) with the
cross-encoder call delegated to the model service.
"""
import logging
import threading
from typing import List, Optional

import requests

from .bge_reranker import BGEReranker

logger = logging.getLogger(__name__)


class RemoteReranker(BGEReranker):
    def __init__(self, base_url: str, timeout: float = 120.0, max_length: int = 512):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._lock = threading.Lock()  # unused for HTTP, kept for interface parity
        # BGEReranker.__init__ would load the model; set what it sets, skip the load
        self.model_name = "BAAI/bge-reranker-v2-m3 (remote)"
        self.device = "remote"
        self.max_length = max_length
        self._reranker = None
        logger.info(f"Using remote reranker at {self.base_url}")

    def _load_model(self):  # never called; defensive
        return None

    def _compute_scores(self, pairs: List[List[str]], max_length: Optional[int] = None) -> List[float]:
        if not pairs:
            return []
        query = pairs[0][0]
        resp = self._session.post(
            f"{self.base_url}/rerank",
            json={"query": query, "texts": [p[1] for p in pairs], "max_length": max_length or self.max_length},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return [float(s) for s in resp.json()["scores"]]
