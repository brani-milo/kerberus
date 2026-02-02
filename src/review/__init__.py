"""
KERBERUS Tabular Review System.

Provides AI-powered document review with:
- Schema-based extraction (6 presets)
- Citations for every extracted field
- Interactive chat with review data
- Excel export with citations sheet
"""

from .presets import REVIEW_PRESETS, get_preset, list_presets
from .document_processor import DocumentProcessor
from .schema_extractor import SchemaExtractor
from .review_manager import ReviewManager
from .excel_exporter import ExcelExporter
from .chat_handler import ReviewChatHandler

__all__ = [
    "REVIEW_PRESETS",
    "get_preset",
    "list_presets",
    "DocumentProcessor",
    "SchemaExtractor",
    "ReviewManager",
    "ExcelExporter",
    "ReviewChatHandler",
]
