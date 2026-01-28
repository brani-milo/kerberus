
import logging
from pathlib import Path
from typing import Dict, Any, Union
from bs4 import BeautifulSoup
import re

from src.parsers.base_parser import BaseParser
from src.parsers.metadata_extractor import MetadataExtractor

logger = logging.getLogger(__name__)

class TicinoParser(BaseParser):
    """
    Parses Ticino specific court decisions (mostly Italian HTML).
    """

    def __init__(self):
        self.extractor = MetadataExtractor()

    def parse(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        file_path = Path(file_path)
        logger.info(f"Parsing Ticino file: {file_path}")
        
        # Ticino files are typically HTML
        if file_path.suffix.lower() != '.html':
             logger.warning(f"Unexpected file type for Ticino parser: {file_path.suffix}")

        text = self._parse_html(file_path)
        
        # Extract Metadata
        metadata = self.extractor.extract_metadata(text)
        
        # Split Sections (Italian specific)
        sections = self._split_sections_ticino(text)

        result = self._get_empty_schema()
        result.update({
            "id": file_path.stem, # Often case ID is filename
            "file_name": file_path.name,
            "date": metadata.get("date"),
            "year": int(metadata.get("date").split("-")[0]) if metadata.get("date") else None,
            "language": "it", # Ticino is predominantly Italian
            "court": "TI_CdK", # Corte di cassazione e di revisione penale (example, generic Ticino code)
            "outcome": metadata.get("outcome"),
            "metadata": {
                "judges": metadata.get("judges"),
                "citations": metadata.get("citations"),
                "lower_court": None
            },
            "content": {
                "regeste": None,
                "facts": sections.get("facts"),
                "reasoning": sections.get("reasoning"),
                "decision": sections.get("decision")
            }
        })
        
        return result

    def _parse_html(self, file_path: Path) -> str:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            soup = BeautifulSoup(f, 'html.parser')
            
            # Ticino decisions often use <pre> tags for the main text
            pre_tag = soup.find('pre')
            if pre_tag:
                return pre_tag.get_text()
            
            # Fallback to full text if no pre tag
            return soup.get_text(separator='\n')

    def _split_sections_ticino(self, text: str) -> Dict[str, str]:
        """
        Specialized splitter for Ticino documents.
        """
        # Common headers in Ticino:
        # Fatti / Ritenuto in fatto
        # Diritto / Considerando in diritto
        # Decisione / Per questi motivi
        
        markers = {
            "facts": [r"Fatti", r"Ritenuto in fatto", r"Ritenuto"],
            "reasoning": [r"Diritto", r"Considerando in diritto", r"Considerando", r"Considerato"],
            "decision": [r"Per questi motivi", r"Decisione", r"Dispositivo"]
        }
        
        def find_start(txt: str, patterns: list) -> int:
            for pat in patterns:
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

        if facts_start != -1:
            end = reasoning_start if reasoning_start != -1 else decision_start if decision_start != -1 else len(text)
            sections["facts"] = text[facts_start:end].strip()
        
        if reasoning_start != -1:
            end = decision_start if decision_start != -1 else len(text)
            sections["reasoning"] = text[reasoning_start:end].strip()
            
        if decision_start != -1:
            sections["decision"] = text[decision_start:].strip()

        return sections
