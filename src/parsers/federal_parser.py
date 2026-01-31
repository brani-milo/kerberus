import logging
import re
import unicodedata
from pathlib import Path
from typing import Dict, Any, Union
import fitz  # pymupdf
from bs4 import BeautifulSoup
from collections import Counter

from src.parsers.base_parser import BaseParser
from src.parsers.metadata_extractor import MetadataExtractor

logger = logging.getLogger(__name__)

class FederalParser(BaseParser):
    """
    Parses Federal court decisions (BGE, BGer, BVGE, BStGer) from HTML and PDF.
    Handles legacy formats (1970s) and modern layouts.
    """

    def __init__(self):
        self.extractor = MetadataExtractor()

    def parse(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        file_path = Path(file_path)
        
        # 1. Extract Text based on format
        if file_path.suffix.lower() == '.html':
            text = self._parse_html(file_path)
        elif file_path.suffix.lower() == '.pdf':
            text = self._parse_pdf(file_path)
        else:
            logger.warning(f"Unsupported file format: {file_path}")
            return self._get_empty_schema()

        # 2. Detect Language (Robust Scoreboard Method)
        language = self._detect_language(text)

        # 3. Extract Metadata
        metadata = self.extractor.extract_metadata(text)
        
        # 4. Split Sections & Regeste
        sections = self._split_sections(text, language)

        # 5. Construct Final JSON
        result = self._get_empty_schema()
        
        # Helper to clean newlines but keep paragraphs (double newlines)
        def clean_val(v):
            if not v: return None
            
            # 1. Start by removing Page Headers/Footers (common in PDFs)
            # Remove "Page/Seite/Pagina X" on its own line
            v = re.sub(r'(?m)^\s*(?:Seite|Page|Pagina|S\.)\s*\d+\s*$', '', v)
            
            # Remove standalone Case IDs on their own line (e.g. "C-4764/2012")
            # This matches the typical Federal/BVGE file number patterns
            v = re.sub(r'(?m)^\s*[A-Z][\-\.]\d+/\d{4}\s*$', '', v)
            
            # 2. Replace single newlines with space, keep double newlines
            # Replace all single newlines that are NOT precedent/succedent by another newline
            return re.sub(r'(?<!\n)\n(?!\n)', ' ', v).strip()

        result.update({
            "id": metadata.get("case_id") or file_path.stem,
            "file_name": file_path.name,
            "date": metadata.get("date"),
            "year": int(metadata.get("date").split("-")[0]) if metadata.get("date") else None,
            "language": language,
            "court": self._determine_court(file_path.name),
            "outcome": metadata.get("outcome"),
            "metadata": {
                "judges": metadata.get("judges"),
                "citations": metadata.get("citations"),
                "lower_court": metadata.get("lower_court")
            },
            "content": {
                "regeste": clean_val(sections.get("regeste")),
                "facts": clean_val(sections.get("facts")),
                "reasoning": clean_val(sections.get("reasoning")),
                "decision": clean_val(sections.get("decision"))
            }
        })
        
        return result

    def _determine_court(self, filename: str) -> str:
        """Helper to guess court from filename conventions."""
        name = filename.upper()
        if "BGE" in name or "ATF" in name: return "CH_BGE" # Official Collection
        if "BGER" in name: return "CH_BGer" # Supreme Court (Unpublished)
        if "BVGE" in name or "ATAF" in name: return "CH_BVGE" # Administrative
        if "BSTG" in name or "TPF" in name: return "CH_BStGer" # Criminal
        return "CH_FED"

    def _parse_html(self, file_path: Path) -> str:
        """
        Clean extraction for BGE/BGer HTMLs.
        Handles <p class="para"> tags by inserting newlines.
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'html.parser')
            
            # Ensure block elements have spacing
            for tag in soup.find_all(['p', 'div', 'br', 'h1', 'h2', 'tr']):
                tag.insert_after('\n')
            
            text = soup.get_text()
            
            # Normalize unicode (fix &nbsp; etc)
            text = unicodedata.normalize("NFKC", text)
            
            # Collapse excessive whitespace but preserve paragraphs
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return "\n".join(lines)

    def _parse_pdf(self, file_path: Path) -> str:
        """
        Extracts text from PDF while trying to remove headers/footers.
        """
        text_blocks = []
        with fitz.open(file_path) as doc:
            for page in doc:
                # Remove header/footer by ignoring top/bottom 5% of page?
                # For now, we take full text but use "blocks" to keep paragraph structure
                blocks = page.get_text("blocks")
                # Block format: (x0, y0, x1, y1, "text", block_no, block_type)
                
                # Sort blocks vertically
                blocks.sort(key=lambda b: b[1])
                
                for b in blocks:
                    # Basic Header Filter: If text is at very top and looks like ID
                    # (y0 < 50) - Skipping for now to be safe, can refine if needed.
                    clean_text = b[4].strip()
                    if clean_text:
                        text_blocks.append(clean_text)
                        
        return "\n\n".join(text_blocks)

    def _detect_language(self, text: str) -> str:
        # Scoreboard method (same as before)
        indicators = {
            "de": {"der", "die", "und", "ist", "in", "das", "nicht", "sachverhalt"},
            "fr": {"le", "la", "et", "est", "dans", "il", "pas", "faits"},
            "it": {"il", "la", "e", "è", "in", "che", "non", "fatti"}
        }
        words = re.findall(r"\b\w+\b", text.lower()[:5000])
        scores = {"de": 0, "fr": 0, "it": 0}
        for word in words:
            for lang, stops in indicators.items():
                if word in stops:
                    scores[lang] += 1
        
        detected = max(scores, key=scores.get)
        if scores[detected] == 0: return "de"
        return detected

    def _split_sections(self, text: str, lang: str) -> Dict[str, str]:
        sections = {"regeste": None, "facts": "", "reasoning": "", "decision": ""}
        
        # --- PATTERNS ---
        # "Strong" patterns are explicit headers (Sachverhalt, Erwägung).
        # "Weak" patterns are generic counters (I., II., A.) which are prone to false positives.
        # We only use weak patterns if NO strong patterns are found for that section.
        
        patterns = {
            "de": {
                "facts": {
                    "strong": [
                        r"^\s*Sachverhalt:?", 
                        r"^\s*Tatbestand:?"
                    ],
                    "weak": [r"^\s*A\.-", r"^\s*I\."]
                },
                "reasoning": {
                    "strong": [
                        r"^\s*Erwägung:?",
                        r"^\s*Aus den Erwägungen:?",
                        r"^\s*(?:Das|Die)\s+[\w\s]+\s+zieht\s+in\s+Erwägung:?" # Generalized "Das ... zieht in Erwägung"
                    ],
                    "weak": [r"^\s*B\.-", r"^\s*II\."]
                },
                "decision": {
                    "strong": [
                        r"^\s*Dispositiv:?", 
                        r"^\s*Demnach erkennt.*:", 
                        r"^\s*Urteil:?", 
                        r"^\s*Erkenntnis:?"
                    ],
                    "weak": [r"^\s*III\."]
                }
            },
            "fr": {
                "facts": {
                    "strong": [r"^\s*Faits:?", r"^\s*En fait:?"],
                    "weak": [r"^\s*A\.-", r"^\s*I\."]
                },
                "reasoning": {
                    "strong": [
                        r"^\s*Considérant:?", 
                        r"^\s*En droit:?",
                        r"^\s*(?:Le|La)\s+[\w\s]+\s+considère:?"
                    ],
                    "weak": [r"^\s*B\.-", r"^\s*II\."]
                },
                "decision": {
                    "strong": [r"^\s*Dispositif:?", r"^\s*Par ces motifs:?", r"^\s*Prononce:?"],
                    "weak": [r"^\s*III\."]
                }
            },
            "it": {
                "facts": {
                    "strong": [r"^\s*Fatti:?", r"^\s*In fatto:?"],
                    "weak": [r"^\s*A\.-", r"^\s*I\."]
                },
                "reasoning": {
                    "strong": [
                        r"^\s*Diritto:?", 
                        r"^\s*In diritto:?", 
                        r"^\s*Considerando:?"
                    ],
                    "weak": [r"^\s*B\.-", r"^\s*II\."]
                },
                "decision": {
                    "strong": [r"^\s*Dispositivo:?", r"^\s*Per questi motivi:?", r"^\s*Pronuncia:?"],
                    "weak": [r"^\s*III\."]
                }
            }
        }

        lang_pats = patterns.get(lang, patterns["de"])
        
        def find_best_idx(txt, section_pats):
            # 1. Try Strong Patterns
            # We want the *first* occurrence of *any* strong pattern that matches.
            # But wait, if multiple strong patterns match, we want the earliest one in the text.
            best_strong = -1
            for p in section_pats["strong"]:
                match = re.search(p, txt, re.MULTILINE | re.IGNORECASE)
                if match:
                    if best_strong == -1 or match.start() < best_strong:
                        best_strong = match.start()
            
            if best_strong != -1:
                return best_strong

            # 2. Fallback to Weak Patterns
            best_weak = -1
            for p in section_pats["weak"]:
                match = re.search(p, txt, re.MULTILINE | re.IGNORECASE)
                if match:
                    if best_weak == -1 or match.start() < best_weak:
                        best_weak = match.start()
            
            return best_weak

        idx_facts = find_best_idx(text, lang_pats["facts"])
        idx_reasoning = find_best_idx(text, lang_pats["reasoning"])
        idx_decision = find_best_idx(text, lang_pats["decision"])

        # SANITY CHECK: Ensure logical order (Facts < Reasoning < Decision)
        # If strict order is violated with weak patterns, we might want to invalidate the weak match.
        # But for now, let's just proceed with simple slicing logic.

        # LOGIC
        length = len(text)
        
        # 1. REGESTE (Headnote) extraction
        if idx_facts != -1:
            raw_header = text[:idx_facts].strip()
            sections["regeste"] = raw_header[-2000:] if len(raw_header) > 2000 else raw_header
        elif idx_reasoning != -1:
             # If no facts found, but reasoning found, everything before reasoning is regeste
             sections["regeste"] = text[:idx_reasoning][-2000:]

        # 2. FACTS
        if idx_facts != -1:
            # End of facts is start of reasoning, or start of decision, or end of text
            candidates = [i for i in [idx_reasoning, idx_decision, length] if i > idx_facts]
            end = min(candidates)
            sections["facts"] = text[idx_facts:end].strip()
        
        # 3. REASONING
        if idx_reasoning != -1:
            candidates = [i for i in [idx_decision, length] if i > idx_reasoning]
            end = min(candidates)
            sections["reasoning"] = text[idx_reasoning:end].strip()
        elif idx_facts != -1:
             # If facts exist but NO reasoning header found, the rest is reasoning?
             # Or maybe the "Facts" section actually contains reasoning?
             # Fallback: if we have facts but no reasoning header, usually the reasoning is just implicit or missed.
             # We try to grab from end-of-facts to decision.
             if idx_decision != -1 and idx_decision > idx_facts:
                  # Assuming facts ends at logic break? No, we can't guess.
                  # But typically, if we missed the Reasoning header, we might just have a huge Facts block.
                  # Let's check if we can rescue it -> default behavior is do nothing (empty reasoning).
                  pass
            
        # 4. DECISION
        if idx_decision != -1:
            sections["decision"] = text[idx_decision:].strip()

        # Fallback for old PDFs: If nothing found, put everything in reasoning
        if idx_facts == -1 and idx_reasoning == -1 and idx_decision == -1:
             sections["reasoning"] = text
             
        return sections