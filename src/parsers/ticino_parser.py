"""
Parser for Ticino cantonal court decisions (HTML).

Handles variance between old (1993) and modern (2017+) HTML structures.
Improved extraction for:
- Judges (composta dai giudici pattern)
- Regeste/Summary (if present)
- Section boundaries
"""

import logging
import re
from pathlib import Path
from typing import Dict, Any, Union, List, Optional
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
            raw_html = self._read_raw_html(file_path)
        else:
            logger.warning(f"TicinoParser received non-html: {file_path}")
            return self._get_empty_schema()

        # 2. Extract Metadata (Uses the robust Whitelist)
        metadata = self.extractor.extract_metadata(text)

        # 3. Extract judges from HTML structure (more reliable than text)
        judges = self._extract_judges_from_html(raw_html)
        if not judges:
            judges = self._extract_judges_from_text(text)
        if not judges:
            judges = metadata.get("judges", [])

        # 4. Extract regeste (summary) if present
        regeste = self._extract_regeste(text)

        # 5. Split Sections
        sections = self._split_sections(text)

        # 6. Construct JSON
        result = self._get_empty_schema()

        # Helper to clean newlines but keep paragraphs
        def clean_val(v):
            if not v:
                return None
            return re.sub(r'(?<!\n)\n(?!\n)', ' ', v).strip()

        # Extract case ID from filename if not found in text
        case_id = metadata.get("case_id")
        if not case_id:
            case_id = self._extract_case_id_from_filename(file_path.stem)

        result.update({
            "id": case_id or file_path.stem,
            "file_name": file_path.name,
            "date": metadata.get("date"),
            "year": int(metadata.get("date").split("-")[0]) if metadata.get("date") else None,
            "language": "it",
            "court": self._determine_court(file_path.stem),
            "outcome": metadata.get("outcome"),
            "metadata": {
                "judges": judges,
                "citations": metadata.get("citations"),
                "lower_court": metadata.get("lower_court") or self._extract_lower_court(text)
            },
            "content": {
                "regeste": clean_val(regeste),
                "facts": clean_val(sections.get("facts")),
                "reasoning": clean_val(sections.get("reasoning")),
                "decision": clean_val(sections.get("decision"))
            }
        })

        return result

    def _read_raw_html(self, file_path: Path) -> str:
        """Read raw HTML content."""
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()

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

    def _extract_judges_from_html(self, html: str) -> List[str]:
        """
        Extract judges from HTML structure.
        Ticino decisions have pattern: "composta dai giudici" followed by names.
        """
        soup = BeautifulSoup(html, 'html.parser')
        judges = []

        # Look for the table cell containing "composta dai giudici"
        for td in soup.find_all('td'):
            text = td.get_text().strip().lower()
            if 'composta dai giudici' in text or 'composto dai giudici' in text:
                # The next sibling td typically contains the judge names
                next_td = td.find_next_sibling('td')
                if next_td:
                    names_text = next_td.get_text()
                    judges = self._parse_judge_names(names_text)
                    if judges:
                        return judges

        # Fallback: look for pattern in text
        text = soup.get_text()
        match = re.search(
            r'composta?\s+dai\s+giudici\s*[:\n]?\s*(.+?)(?:segretari[oa]|cancelliere|parti\b)',
            text,
            re.IGNORECASE | re.DOTALL
        )
        if match:
            judges = self._parse_judge_names(match.group(1))

        return judges

    def _parse_judge_names(self, text: str) -> List[str]:
        """Parse judge names from text, handling Italian naming conventions."""
        # Clean up the text
        text = unicodedata.normalize("NFKC", text)
        text = re.sub(r'\s+', ' ', text).strip()

        # Remove common role indicators but keep names
        text = re.sub(r',?\s*presidente\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r',?\s*giudice\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r',?\s*membro\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r',?\s*supplente\b', '', text, flags=re.IGNORECASE)

        # Split by comma, newline, or "e" (and)
        parts = re.split(r'[,\n]+|\s+e\s+', text)

        judges = []
        for part in parts:
            name = part.strip()
            # Filter out noise: must look like a name (2+ chars, starts with uppercase)
            if len(name) >= 2 and name[0].isupper():
                # Skip common non-name words
                skip_words = ['il', 'la', 'lo', 'gli', 'le', 'un', 'una', 'del', 'della',
                              'nei', 'nella', 'dal', 'dalla', 'con', 'per', 'tra', 'fra']
                if name.lower() not in skip_words and not name.lower().startswith('part'):
                    judges.append(name)

        return judges[:6]  # Limit to reasonable number

    def _extract_judges_from_text(self, text: str) -> List[str]:
        """Fallback judge extraction from plain text."""
        # Pattern for Italian court composition
        patterns = [
            r'composta?\s+dai?\s+giudici?\s*[:\n]?\s*(.+?)(?=segretari|cancellier|parti\b|\n\n)',
            r'(?:Giudici|Richter|Juges)\s*[:\n]?\s*(.+?)(?=Cancellier|Segretari|Gerichtsschreiber|\n\n)',
            r'Composizione\s*[:\n]?\s*(.+?)(?=Parti|Parteien|Parties|\n\n)'
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                names = self._parse_judge_names(match.group(1))
                if names:
                    return names

        return []

    def _extract_regeste(self, text: str) -> Optional[str]:
        """
        Extract regeste/summary if present.
        Ticino decisions typically don't have formal regeste, but some may have
        a summary section before the facts.
        """
        # Look for explicit regeste markers
        patterns = [
            r'(?:Regesto|Regeste|Massima|Sommario)\s*[:\n]?\s*(.+?)(?=\bFatti\b|\bIn\s+fatto\b)',
            r'(?:Oggetto|Materia)\s*[:\n]?\s*(.+?)(?=\bFatti\b|\bIn\s+fatto\b)'
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                regeste = match.group(1).strip()
                # Only return if it's substantial (more than just a few words)
                if len(regeste.split()) > 5:
                    return regeste[:2000]  # Limit length

        return None

    def _extract_case_id_from_filename(self, filename: str) -> str:
        """Extract case ID from filename pattern like TI_CATI_001_80-2016-4_2016-09-16."""
        # Remove date suffix if present
        match = re.match(r'(TI_\w+_\d+_.+?)_\d{4}-\d{2}-\d{2}$', filename)
        if match:
            return match.group(1)
        return filename

    def _determine_court(self, filename: str) -> str:
        """Determine specific court from filename."""
        if 'CATI' in filename:
            return "CH_TI_CATI"  # Camera di diritto tributario
        elif 'TRAC' in filename:
            return "CH_TI_TRAC"  # Tribunale d'appello civile
        elif 'GIAR' in filename:
            return "CH_TI_GIAR"  # Giurisdizione amministrativa
        elif 'TRAP' in filename:
            return "CH_TI_TRAP"  # Tribunale d'appello penale
        return "CH_TI"

    def _extract_lower_court(self, text: str) -> Optional[str]:
        """Extract lower court information."""
        patterns = [
            r'(?:contro\s+(?:la\s+)?decisione\s+(?:di|della|del)\s+)(.+?)(?:\.|,|\n)',
            r'(?:avverso\s+(?:la\s+)?(?:decisione|sentenza)\s+(?:di|della|del)\s+)(.+?)(?:\.|,|\n)',
            r'(?:ricorso\s+contro\s+)(.+?)(?:\.|,|\n)',
            r'(?:impugnando\s+la\s+)(.+?)(?:\.|,|\n)'
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                court = match.group(1).strip()
                if 5 < len(court) < 200:
                    return court

        return None

    def _split_sections(self, text: str) -> Dict[str, str]:
        """
        Splits text handling 3 scenarios:
        1. Standard: "Fatti" ... "Diritto" ... "Decisione"
        2. Merged (Old): "In fatto e in diritto" ... "Decisione"
        3. Missing Facts: Starts directly with "Diritto" (rare but possible)
        """
        sections = {"facts": "", "reasoning": "", "decision": ""}

        # --- PATTERNS ---
        # 1. Decision (The Anchor at the end)
        pat_decision = [
            r"^\s*Per\s+questi\s+motivi",
            r"^\s*Dispositivo",
            r"^\s*decide\s*[:\.]?$",
            r"^\s*dichiara\s+e\s+pronuncia"
        ]

        # 2. Law (Reasoning)
        pat_law = [
            r"^\s*Diritto\s*[:\.]?$",
            r"^\s*In\s+diritto\s*[:\.]?$",
            r"^\s*Considerando\s*[:\.]?$",
            r"^\s*Considerato\s*[:\.]?$"
        ]

        # 3. Facts
        pat_facts = [
            r"^\s*Fatti\s*[:\.]?$",
            r"^\s*In\s+fatto\s*[:\.]?$",
            r"^\s*Del\s+fatto\s*[:\.]?$",
            r"^\s*Ritenuto\s+in\s+fatto\s*[:\.]?"
        ]

        # 4. Merged Header (The "1993" Case)
        pat_merged = [
            r"^\s*In\s+fatto\s+e\s+in\s+diritto\s*[:\.]?$",
            r"^\s*Ritenuto\s+in\s+fatto\s+e\s+in\s+diritto\s*[:\.]?"
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
            sections["reasoning"] = text[idx_merged:end_body].strip()
            sections["facts"] = ""

        # --- SCENARIO B: Standard (Fatti -> Diritto) ---
        elif idx_facts != -1 and idx_law != -1:
            sections["facts"] = text[idx_facts:idx_law].strip()
            sections["reasoning"] = text[idx_law:end_body].strip()

        # --- SCENARIO C: Missing "Fatti" Header (Fallback) ---
        elif idx_law != -1:
            sections["facts"] = text[:idx_law].strip()
            sections["reasoning"] = text[idx_law:end_body].strip()

        # --- SCENARIO D: Everything Failed ---
        else:
            sections["reasoning"] = text[:end_body].strip()

        # Extract Decision (always the same logic)
        if idx_decision != -1:
            sections["decision"] = text[idx_decision:].strip()

        return sections

    def _find_first_match(self, text: str, patterns: list) -> int:
        """Helper to find the earliest occurrence of any pattern in the list."""
        best_idx = -1
        for pat in patterns:
            match = re.search(pat, text, re.MULTILINE | re.IGNORECASE)
            if match:
                if best_idx == -1 or match.start() < best_idx:
                    best_idx = match.start()
        return best_idx
