
import logging
from pathlib import Path
from typing import Dict, Any, Union, Tuple
import fitz  # pymupdf
from bs4 import BeautifulSoup
import re

from src.parsers.base_parser import BaseParser
from src.parsers.metadata_extractor import MetadataExtractor

logger = logging.getLogger(__name__)

class FederalParser(BaseParser):
    """
    Parses Federal court decisions from HTML and PDF.
    """

    def __init__(self):
        self.extractor = MetadataExtractor()

    def parse(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        file_path = Path(file_path)
        logger.info(f"Parsing file: {file_path}")

        if file_path.suffix.lower() == '.html':
            text = self._parse_html(file_path)
        elif file_path.suffix.lower() == '.pdf':
            text = self._parse_pdf(file_path)
        else:
            raise ValueError(f"Unsupported file format: {file_path.suffix}")

        # Extract Metadata
        metadata = self.extractor.extract_metadata(text)
        
        # Split Sections
        sections = self._split_sections(text)

        # Construct Final JSON
        result = self._get_empty_schema()
        result.update({
            "id": metadata.get("case_id") or file_path.stem, # Fallback to filename if no ID found
            "file_name": file_path.name,
            "date": metadata.get("date"),
            "year": int(metadata.get("date").split("-")[0]) if metadata.get("date") else None,
            "language": self._detect_language(text),
            "court": "CH_BGer",
            "outcome": metadata.get("outcome"),
            "metadata": {
                "judges": metadata.get("judges"),
                "citations": metadata.get("citations"),
                "lower_court": None # Not currently extracted
            },
            "content": {
                "regeste": None, # Complex extraction, placeholder
                "facts": sections.get("facts"),
                "reasoning": sections.get("reasoning"),
                "decision": sections.get("decision")
            }
        })
        
        return result

    def _parse_html(self, file_path: Path) -> str:
        with open(file_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'html.parser')
            # Get all text, clean it up
            return soup.get_text(separator='\n')

    def _parse_pdf(self, file_path: Path) -> str:
        text = ""
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text() + "\n"
        return text

    def _split_sections(self, text: str) -> Dict[str, str]:
        """
        Splits text into Facts, Reasoning, and Decision based on keywords.
        """
        # Search patterns
        # Fact patterns: Sachverhalt, Faits, Fatti
        # Reasoning patterns: Erwägungen, Considérants, Diritto
        # Decision patterns: Dispositiv, Dispositif, Dispositivo
        
        # Note: These are simplified heuristics. Real text is messy.
        
        markers = {
            "facts": [r"Sachverhalt", r"Faits", r"Fatti"],
            "reasoning": [r"Erwägung", r"Considérant", r"Diritto"], # 'Erwägung' matches starts of paragraphs too often? 'Erwägungen' is better section title usually.
            "decision": [r"Dispositiv", r"Dispositif", r"Dispositivo"]
        }
        
        # Normalize text for searching indices, but keep original for extraction
        # Not actually lowercasing because headers are often capitalized specific ways
        
        # Helper to find first index of any marker
        def find_start(txt: str, patterns: list) -> int:
            for pat in patterns:
                # We look for the pattern often as a standalone line or header
                # A simple search might be too aggressive matching inside sentences.
                # Let's try to match it at start of line or with minimal context.
                # Adjust regex as needed.
                match = re.search(pat, txt, re.IGNORECASE)
                if match:
                    return match.start()
            return -1

        facts_start = find_start(text, markers["facts"])
        reasoning_start = find_start(text, markers["reasoning"])
        decision_start = find_start(text, markers["decision"])

        sections = {
            "facts": None,
            "reasoning": None,
            "decision": None
        }
        
        # Logic: 
        # Facts is usually first section after header.
        # Reasoning follows Facts.
        # Decision follows Reasoning.
        
        # If we found reasoning start, we can guess facts end
        if facts_start != -1:
            end = reasoning_start if reasoning_start != -1 else decision_start if decision_start != -1 else len(text)
            sections["facts"] = text[facts_start:end].strip()
        
        if reasoning_start != -1:
            end = decision_start if decision_start != -1 else len(text)
            sections["reasoning"] = text[reasoning_start:end].strip()
            
        if decision_start != -1:
            sections["decision"] = text[decision_start:].strip()

        return sections

    def _detect_language(self, text: str) -> str:
        # Simple heuristic based on common words
        if "Bundesgericht" in text or "Sachverhalt" in text:
            return "de"
        elif "Tribunal fédéral" in text or "Faits" in text:
            return "fr"
        elif "Tribunale federale" in text or "Fatti" in text:
            return "it"
        return "unknown"
