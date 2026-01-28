
import re
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class MetadataExtractor:
    """
    Extracts metadata from Swiss court decision text using regex patterns.
    Handles German, French, and Italian.
    """

    # Outcome mappings
    OUTCOME_PATTERNS = {
        "approved": [
            r"gutgeheissen", r"in Gutheissung", 
            r"admis", r"accolto"
        ],
        "dismissed": [
            r"abgewiesen", r"rejeté", r"respinto"
        ],
        "inadmissible": [
            r"nichteintreten", r"irrecevable", r"inammissibile"
        ]
    }

    # Citation patterns
    # Matches: Art. 8 ZGB, Artikel 12 OR, § 123 StPO
    PAT_LAW = re.compile(r"(?:Art\.|Artikel|§)\s*(\d+[a-z]*)\s*([A-Za-z]+)", re.IGNORECASE)
    
    # Matches: BGE 140 III 348
    PAT_CASE = re.compile(r"BGE\s+(\d+)\s+([IV]+)\s+(\d+)", re.IGNORECASE)

    # Date pattern (Swiss format DD.MM.YYYY)
    PAT_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")

    def extract_metadata(self, text: str) -> Dict:
        """
        Extracts all metadata fields from the text.
        """
        return {
            "case_id": self._extract_case_id(text),
            "citations": self._extract_citations(text),
            "outcome": self._extract_outcome(text),
            "date": self._extract_date(text),
            "judges": self._extract_judges(text)
        }

    def _extract_case_id(self, text: str) -> Optional[str]:
        """
        Attempts to find the BGE Case ID.
        """
        match = self.PAT_CASE.search(text)
        if match:
            # Format: BGE 140 III 348
            return f"BGE {match.group(1)} {match.group(2)} {match.group(3)}"
        return None

    def _extract_citations(self, text: str) -> Dict[str, List[str]]:
        """
        Extracts references to laws and other cases.
        """
        laws = []
        for match in self.PAT_LAW.finditer(text):
            # Format: Art. 8 ZGB
            laws.append(f"Art. {match.group(1)} {match.group(2)}")
        
        cases = []
        for match in self.PAT_CASE.finditer(text):
            # Format: BGE 140 III 348
            cases.append(f"BGE {match.group(1)} {match.group(2)} {match.group(3)}")
        
        return {
            "laws": sorted(list(set(laws))),
            "cases": sorted(list(set(cases)))
        }

    def _extract_outcome(self, text: str) -> str:
        """
        Determines the outcome of the case based on keywords.
        """
        # Search specifically in the "Dispositiv" / "Dispositif" / "Dispositivo" section if possible,
        # but here we search the global text or a provided snippet.
        # It's better to search the end of the document, but for now we search the whole text
        # or rely on the caller to pass the relevant section.
        
        # We will normalize to lowercase for search
        text_lower = text.lower()

        for outcome, patterns in self.OUTCOME_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    return outcome
        
        return "unknown"

    def _extract_date(self, text: str) -> Optional[str]:
        """
        Extracts and normalizes the judgment date (YYYY-MM-DD).
        """
        # Often appears at the top: "Urteil vom 12. Mai 2014" or similar.
        # We look for the first date pattern usually.
        match = self.PAT_DATE.search(text)
        if match:
            day, month, year = match.groups()
            try:
                date_obj = datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
                return date_obj.strftime("%Y-%m-%d")
            except ValueError:
                pass
        return None

    def _extract_judges(self, text: str) -> List[str]:
        """
        Extracts judges. This is heuristic and depends heavily on structure.
        Common patterns: "Besetzung: Bundesrichter ...", "Composition: MM. les Juges ..."
        """
        # Simple extraction strategy: Look for lines after "Besetzung" / "Composition"
        # This is a placeholder for more complex logic if needed.
        
        patterns = [
            r"Besetzung[:\s]+(.*?)(?:\n|$)",
            r"Composition[:\s]+(.*?)(?:\n|$)",
            r"Composizione[:\s]+(.*?)(?:\n|$)"
        ]
        
        for pat in patterns:
            match = re.search(pat, text, re.IGNORECASE | re.DOTALL)
            if match:
                # Naively split by comma or 'und'
                raw_judges = match.group(1).strip()
                # Clean up generic words if necessary
                return [cleaned.strip() for cleaned in re.split(r',|\sund\s|\set\s|\se\s', raw_judges) if cleaned.strip()]
                
        return []
