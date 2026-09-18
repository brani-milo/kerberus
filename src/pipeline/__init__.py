"""Single orchestration of the legal query pipeline, shared by the API and the UI."""
from .service import LegalQueryService, PipelineOptions, PipelineEvent, LegalAnswer, get_query_service

__all__ = ["LegalQueryService", "PipelineOptions", "PipelineEvent", "LegalAnswer", "get_query_service"]
