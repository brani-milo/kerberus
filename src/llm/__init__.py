"""
LLM Integration for KERBERUS.

Three-stage pipeline:
1. Mistral 1: Guard & Enhance (security + query improvement)
2. Search + Rerank + MMR
3. Mistral 2: Query Reformulator (structure for Qwen)
4. Qwen: Full Legal Analysis (with citations)

Provides:
- Dual LLM clients (Mistral + Qwen)
- Pipeline orchestration
- Context assembly for RAG
- Legal prompt templates
"""
from .client import (
    MistralClient,
    QwenClient,
    LLMResponse,
    get_mistral_client,
    get_qwen_client,
)
from .context import ContextAssembler
from .prompts import (
    GuardEnhancePrompts,
    ReformulatorPrompts,
    LegalAnalysisPrompts,
    build_fedlex_url,
    build_bger_url,
)
from .pipeline import (
    LegalPipeline,
    GuardResult,
    PipelineResult,
    get_pipeline,
)

# Legacy aliases for backwards compatibility
LLMClient = MistralClient
LegalPrompts = LegalAnalysisPrompts

__all__ = [
    # Clients
    "MistralClient",
    "QwenClient",
    "LLMResponse",
    "get_mistral_client",
    "get_qwen_client",
    # Pipeline
    "LegalPipeline",
    "GuardResult",
    "PipelineResult",
    "get_pipeline",
    # Context
    "ContextAssembler",
    # Prompts
    "GuardEnhancePrompts",
    "ReformulatorPrompts",
    "LegalAnalysisPrompts",
    "build_fedlex_url",
    "build_bger_url",
    # Legacy
    "LLMClient",
    "LegalPrompts",
]
