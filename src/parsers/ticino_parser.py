import logging
import re
from pathlib import Path
from typing import Dict, Any, Union
from bs4 import BeautifulSoup
import unicodedata

from src.parsers.base_parser import BaseParser
from src.parsers.metadata_extractor import MetadataExtractor

logger = logging.getLogger(__name__)

class TicinoParser(BaseParser):
    """
    Parses Cantonal court decisions from Ticino (HTML).
    Handles variance between old (1993) and modern (2017) HTML structures.
    """

    def __init__(self):
        self.extractor = MetadataExtractor()

    def parse(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        file_path = Path(file_path)
        
        # 1. Read & Clean Text
        if file_path.suffix.lower() == '.html':
            text = self._parse_and_clean_html(file_path)
        else:
            logger.warning(f"TicinoParser received non-html: {file_path}")
            return self._get_empty_schema()

        # 2. Extract Metadata (Uses the robust Whitelist from before)
        metadata = self.extractor.extract_metadata(text)
        
        # 3. Split Sections (The "Smart" Splitter)
        sections = self._split_sections(text)

        # 4. Construct JSON
        result = self._get_empty_schema()
        
        # Helper to clean newlines but keep paragraphs (double newlines)
        def clean_val(v):
            if not v: return None
            # Replace single newlines with space, keep double newlines
            return re.sub(r'(?<!\n)\n(?!\n)', ' ', v).strip()

        result.update({
            "id": metadata.get("case_id") or file_path.stem,
            "file_name": file_path.name,
            "date": metadata.get("date"),
            "year": int(metadata.get("date").split("-")[0]) if metadata.get("date") else None,
            "language": "it",
            "court": "CH_TI",
            "outcome": metadata.get("outcome"),
            "metadata": {
                "judges": metadata.get("judges"),
                "citations": metadata.get("citations"),
                "lower_court": metadata.get("lower_court")
            },
            "content": {
                "regeste": None,
                "facts": clean_val(sections.get("facts")),
                "reasoning": clean_val(sections.get("reasoning")),
                "decision": clean_val(sections.get("decision"))
            }
        })
        
        return result

    def _parse_and_clean_html(self, file_path: Path) -> str:
        """
        Aggressive cleaning to handle Word-generated HTML (MsoNormal, &nbsp;, etc.)
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'html.parser')
            
            # 1. Add newlines to block elements so text doesn't merge
            for block in soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'br', 'tr']):
                block.insert_after('\n')

            # 2. Extract text
            text = soup.get_text()

            # 3. Normalize Unicode (turns &nbsp; into normal spaces)
            text = unicodedata.normalize("NFKC", text)

            # 4. Collapse multiple spaces/newlines into clean blocks
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return "\n".join(lines)

    def _split_sections(self, text: str) -> Dict[str, str]:
        """
        Splits text handling 3 scenarios:
        1. Standard: "Fatti" ... "Diritto" ... "Decisione"
        2. Merged (Old): "In fatto e in diritto" ... "Decisione"
        3. Missing Facts: Starts directly with "Diritto" (rare but possible)
        """
        sections = {"facts": "", "reasoning": "", "decision": ""}
        
        # --- PATTERNS ---
        # We use re.IGNORECASE and relax the matching (allow colons, spaces)
        
        # 1. Decision (The Anchor at the end)
        # Matches: "Per questi motivi", "Decide:", "omissis" (sometimes used)
        pat_decision = [
            r"^\s*Per\s+questi\s+motivi", 
            r"^\s*Dispositivo", 
            r"^\s*decide\s*[:\.]?$"
        ]

        # 2. Law (Reasoning)
        # Matches: "Diritto", "In diritto", "Considerando", "Considerato"
        pat_law = [
            r"^\s*Diritto\s*[:\.]?$", 
            r"^\s*In\s+diritto\s*[:\.]?$", 
            r"^\s*Considerando\s*[:\.]?$", 
            r"^\s*Considerato\s*[:\.]?$"
        ]

        # 3. Facts
        # Matches: "Fatti", "In fatto", "Del fatto"
        pat_facts = [
            r"^\s*Fatti\s*[:\.]?$", 
            r"^\s*In\s+fatto\s*[:\.]?$", 
            r"^\s*Del\s+fatto\s*[:\.]?$"
        ]

        # 4. Merged Header (The "1993" Case)
        pat_merged = [
            r"^\s*In\s+fatto\s+e\s+in\s+diritto\s*[:\.]?$"
        ]

        # --- FIND INDICES ---
        idx_decision = self._find_first_match(text, pat_decision)
        idx_law = self._find_first_match(text, pat_law)
        idx_facts = self._find_first_match(text, pat_facts)
        idx_merged = self._find_first_match(text, pat_merged)

        length = len(text)
        end_body = idx_decision if idx_decision != -1 else length

        # --- SCENARIO A: Merged Header (1993 Style) ---
        if idx_merged != -1:
            # Usually these short decisions mix facts and law. 
            # We put everything in REASONING as it contains the legal argument.
            sections["reasoning"] = text[idx_merged:end_body].strip()
            sections["facts"] = "" # Explicitly empty
        
        # --- SCENARIO B: Standard (Fatti -> Diritto) ---
        elif idx_facts != -1 and idx_law != -1:
            # Facts is between Fatti and Law
            sections["facts"] = text[idx_facts:idx_law].strip()
            # Reasoning is between Law and Decision
            sections["reasoning"] = text[idx_law:end_body].strip()

        # --- SCENARIO C: Missing "Fatti" Header (Fallback) ---
        elif idx_law != -1:
            # Assume everything before Law is Facts (or header + facts)
            # We limit the start to avoid capturing the very top court header if possible,
            # but usually capturing the header into 'facts' is acceptable fallout.
            sections["facts"] = text[:idx_law].strip()
            sections["reasoning"] = text[idx_law:end_body].strip()

        # --- SCENARIO D: Everything Failed ---
        else:
            # Dump everything into Reasoning so we don't lose data
            sections["reasoning"] = text[:end_body].strip()

        # Extract Decision (always the same logic)
        if idx_decision != -1:
            sections["decision"] = text[idx_decision:].strip()

        return sections

    def _find_first_match(self, text: str, patterns: list) -> int:
        """Helper to find the earliest occurrence of any pattern in the list."""
        best_idx = -1
        for pat in patterns:
            # MULTILINE is crucial so ^ matches start of line, not just start of string
            match = re.search(pat, text, re.MULTILINE | re.IGNORECASE)
            if match:
                if best_idx == -1 or match.start() < best_idx:
                    best_idx = match.start()
        return best_idx