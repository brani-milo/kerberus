"""
LLM Integration for KERBERUS.

Provides:
- LLM client (Mistral, Qwen, or mock)
- Context assembly for RAG
- Legal prompt templates
- Token usage tracking
"""
from .client import LLMClient, get_llm_client
from .context import ContextAssembler
from .prompts import LegalPrompts

__all__ = [
    "LLMClient",
    "get_llm_client",
    "ContextAssembler",
    "LegalPrompts",
]
