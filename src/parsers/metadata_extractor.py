import json
import logging
import re
from pathlib import Path
from typing import Dict, Any, List, Set, Optional

logger = logging.getLogger(__name__)


class MetadataExtractor:
    """
    Central Logic for extracting metadata from Swiss Legal Texts.
    Handles Multilingual Regex (DE/FR/IT).

    Uses a dynamic registry of law abbreviations loaded from Fedlex SPARQL data.
    Falls back to hardcoded codes if registry is not available.
    """

    # Fallback codes if registry file is not available
    FALLBACK_CODES = {
        # Federal
        "CP", "STGB", "CODE PÉNAL", "CODICE PENALE",
        "CC", "ZGB", "CODE CIVIL", "CODICE CIVILE",
        "CO", "OR", "OBLIGATIONENRECHT", "CODICE DELLE OBBLIGAZIONI",
        "LP", "LEF", "SCHKG",
        "CPC", "ZPO", "CPP", "STPO",
        "LEI", "AIG", "LSTR", "AUG",
        "LIFD", "DBG",
        "LAMAL", "KVG",
        "LCA", "VVG",
        "LCSTR", "SVG",
        "LAVS", "AHVG",
        "LAI", "IVG",
        "LPP", "BVG",
        "LAA", "UVG",
        "LAPG", "EOG",
        "LACI", "AVIG",
        "LTF", "BGG",
        "LTAF", "VGG",
        "VGKE",
        "PA", "VWVG", "VWG",
        "VEV",
        "BV", "CST", "COSTE",
        "GWG", "LBA", "LRD",
        "EMRK", "CEDH",
        "ASYLG", "LASI",
        "BETMG", "LSTUP",
        "MWSTG", "LTVA",
        "ATSG", "LPGA",
        "FZA", "ALCP",
        "RPG", "LAT",
        "USG", "LPE",
        "KKG", "LCC",
        "FUSG", "LFUS",
        "STHG", "LHID",
        "KAG", "LPCC",
        "FINMAG", "LFINMA",
        "LTRANS",
        "LEMB",
        "LPERS",
        # Ticino cantonal laws
        "LT", "RLT", "LTB", "LTRAS", "LTSUCC", "LIC",
        "LOG", "LOC", "LASC", "LG", "LTAC", "LORG",
        "CPC/TI", "CPP/TI", "LPAMM", "LPA", "LORD",
        "LE", "LGC", "LSTIP", "LSAN", "LCAMAL", "LFARM",
        "LSCUOLA", "LASOC", "LST", "LALIAS", "LAMB",
        "LACQUE", "LSTR", "LNAV", "LAPP", "LAPPALTI",
        "LCPUBB", "LAGR", "LFOR", "COST./TI", "COST/TI",
        "LAFE", "LAC", "RLC", "LIP", "LPROG", "LALC", "LTAG"
    }

    # Default registry paths (relative to project root)
    FEDLEX_REGISTRY_PATH = Path(__file__).parent.parent.parent / "data" / "fedlex" / "metadata" / "abbreviations.json"
    TICINO_REGISTRY_PATH = Path(__file__).parent.parent.parent / "data" / "ticino" / "metadata" / "abbreviations.json"

    # Keep old name for backward compatibility
    REGISTRY_PATH = FEDLEX_REGISTRY_PATH

    def __init__(self, registry_path: Optional[Path] = None):
        """
        Initialize the MetadataExtractor.

        Args:
            registry_path: Optional path to abbreviations.json.
                          If None, uses default locations (Fedlex + Ticino).
        """
        self._registry_path = registry_path or self.FEDLEX_REGISTRY_PATH
        self._ticino_registry_path = self.TICINO_REGISTRY_PATH
        self._registry: Optional[Dict] = None
        self._valid_codes: Set[str] = set()
        self._abbrev_to_sr: Dict[str, List[str]] = {}
        self._sr_to_abbrev: Dict[str, Dict[str, str]] = {}

        # Load registries
        self._load_registry()
        self._load_ticino_registry()

    def _load_registry(self):
        """Load the Fedlex abbreviation registry from JSON file."""
        if self._registry_path.exists():
            try:
                with open(self._registry_path, 'r', encoding='utf-8') as f:
                    self._registry = json.load(f)

                # Extract valid codes (all abbreviations)
                self._valid_codes = set(self._registry.get("all_codes", []))

                # Build reverse lookup: abbreviation -> SR numbers
                self._abbrev_to_sr = self._registry.get("by_abbrev", {})

                # Build SR -> abbreviations mapping
                self._sr_to_abbrev = self._registry.get("by_sr", {})

                logger.info(f"Loaded {len(self._valid_codes)} law codes from Fedlex registry")

            except Exception as e:
                logger.warning(f"Failed to load abbreviation registry: {e}. Using fallback codes.")
                self._valid_codes = self.FALLBACK_CODES.copy()
        else:
            logger.warning(f"Registry not found at {self._registry_path}. Using fallback codes.")
            self._valid_codes = self.FALLBACK_CODES.copy()

    def _load_ticino_registry(self):
        """Load the Ticino cantonal abbreviation registry."""
        if self._ticino_registry_path.exists():
            try:
                with open(self._ticino_registry_path, 'r', encoding='utf-8') as f:
                    ticino_registry = json.load(f)

                # Add Ticino codes to valid codes
                ticino_codes = set(ticino_registry.get("all_codes", []))
                self._valid_codes.update(ticino_codes)

                # Merge Ticino abbreviation lookups
                ticino_abbrev = ticino_registry.get("by_abbrev", {})
                for abbrev, refs in ticino_abbrev.items():
                    if abbrev in self._abbrev_to_sr:
                        # Extend existing list
                        self._abbrev_to_sr[abbrev].extend(refs)
                    else:
                        self._abbrev_to_sr[abbrev] = refs

                logger.info(f"Added {len(ticino_codes)} Ticino law codes to registry")

            except Exception as e:
                logger.warning(f"Failed to load Ticino registry: {e}")
        else:
            logger.debug(f"Ticino registry not found at {self._ticino_registry_path}")

    @property
    def VALID_CODES(self) -> Set[str]:
        """Property for backward compatibility. Returns set of valid law codes."""
        return self._valid_codes

    def get_sr_for_abbreviation(self, abbrev: str) -> List[str]:
        """
        Get SR numbers for a given abbreviation.

        Args:
            abbrev: Law abbreviation (e.g., "OR", "CO", "ZGB")

        Returns:
            List of SR numbers that use this abbreviation
        """
        abbrev_upper = abbrev.upper().replace(".", "").strip()
        return self._abbrev_to_sr.get(abbrev_upper, [])

    def get_abbreviations_for_sr(self, sr: str, language: str = None) -> Dict[str, str]:
        """
        Get abbreviations for a given SR number.

        Args:
            sr: SR number (e.g., "220", "210", "311.0")
            language: Optional language filter ("de", "fr", "it")

        Returns:
            Dict with language -> abbreviation mapping, or specific abbreviation if language specified
        """
        entry = self._sr_to_abbrev.get(sr, {})
        if language:
            return {language: entry.get(language, "")}
        return {k: v for k, v in entry.items() if k in ("de", "fr", "it")}

    def extract_metadata(self, text: str) -> Dict[str, Any]:
        """
        Main entry point called by Parsers.
        """
        return {
            "case_id": self._extract_case_id(text),
            "date": self._extract_date(text),
            "outcome": self._extract_outcome(text),
            "judges": self._extract_judges(text),
            "citations": {
                "laws": self._extract_legal_citations(text),
                "cases": self._extract_case_citations(text)
            },
            "regeste": self._extract_regeste(text),
            "lower_court": self._extract_lower_court(text)
        }

    def _extract_legal_citations(self, text: str) -> List[str]:
        """
        Extracts valid law citations using the registry/whitelist.
        Handles: "Art. 337 OR", "art. 4 et 5 LAA", "art. 123 CP", "Art. 23 ... (GwG)"
        Also handles lists: "Art. 113, 117 LTF", "Art. 10, 11 und 12 StGB"

        Returns citations with optional SR number enrichment.
        """
        clean_citations = set()

        # Dynamically build the code pattern from the registry to avoid false positives
        # Sort by length descending to match longest first
        # Use word boundaries (\b) to avoid partial matches
        sorted_codes = sorted([re.escape(c) for c in self._valid_codes], key=len, reverse=True)

        # Codes in group 1 (wrapped in \b), SR codes in group 2
        code_pattern_str = r"\b(?:" + "|".join(sorted_codes) + r")\b|SR\s*\d[\d\.]*"

        # Helper patterns for multi-article matching
        # Improved number_re to capture "Abs.", "al.", "Bst.", "lit.", "let." etc.
        # E.g. "5 Abs. 1 Bst. c"
        # Allows digits, letters (bis/ter), and section markers
        number_re = r"\d+(?:[a-z]|\s+(?:bis|ter|quater|quinquies))?(?:\s+(?:[A-Z][a-z]+\.?|Abs\.?|al\.?|cpv\.?|Bst\.?|lit\.?|let\.?|Ziff\.?)\s*\d+[a-z]*)*"
        # Separators: comma, &, hyphen, or words (et, e, und, and)
        sep_re = r"(?:\s*[,&\-]\s*|\s+(?:et|e|und|and)\s+)"

        # Group 1: The Multi-Number match. Match Number followed optionally by (Separator + Number) repeated.
        multi_number_re = f"({number_re}(?:{sep_re}{number_re})*)"

        pattern = re.compile(
            r"(?:Art\.?|§|art\.)\s*"                 # Prefix
            + multi_number_re +                      # Group 1: Multi-numbers
            r"(?:(?!(?:Art\.?|§|art\.))[\s\S]){0,400}?"  # Gap: any char, don't cross Art boundaries, max 400
            r"\s+"                                   # Space before Code
            r"[\[\(]?(" + code_pattern_str + r")[\]\)]?",    # Code (Group 2): match valid codes with boundary, allow [ or (
            re.IGNORECASE | re.DOTALL
        )

        matches = pattern.findall(text)

        for art_group, code in matches:
            # Basic cleanup of code
            if code.upper().strip().startswith("SR"):
                code_clean = code.upper().strip()  # Keep dots for SR
            else:
                code_clean = code.upper().replace(".", "").strip()

            # THE FILTER: Only accept if in whitelist or starts with SR
            if code_clean in self._valid_codes or code_clean.startswith("SR"):
                # Split the captured article group into individual numbers
                # We use the same separator pattern for splitting
                raw_arts = re.split(sep_re, art_group, flags=re.IGNORECASE)

                for art in raw_arts:
                    art = art.strip()
                    # Clean newlines/spaces in the article string itself (e.g. "33 \n")
                    art = re.sub(r'\s+', ' ', art).strip()
                    if art:
                        citation = f"Art. {art} {code_clean}"
                        clean_citations.add(citation)

        return sorted(list(clean_citations))

    def _extract_case_citations(self, text: str) -> List[str]:
        """
        Extracts references to:
        1. Official Collection: BGE 140 III 348, ATF 132 I 12
        2. Federal File Numbers: 6B_123/2020, 4A_55/2019
        """
        citations = set()

        # 1. Official Collection (BGE/ATF/DTF)
        # Pattern: BGE/ATF + Volume + Part (I-V) + Page
        pattern_off = re.compile(r"(?:BGE|ATF|DTF)\s+(\d+)\s+([IV]+)\s+(\d+)", re.IGNORECASE)
        matches_off = pattern_off.findall(text)
        for vol, part, page in matches_off:
            citations.add(f"BGE {vol} {part} {page}")

        # 2. Administrative Court (BVGE/ATAF)
        # Pattern: BVGE 2011/1 or 2007/41
        pattern_bvge = re.compile(r"(?:BVGE|ATAF)\s+(\d{4}/\d+)", re.IGNORECASE)
        matches_bvge = pattern_bvge.findall(text)
        for ref in matches_bvge:
            citations.add(f"BVGE {ref}")

        # 3. Federal File Numbers (e.g. 6B_489/2021)
        # Pattern: digit+Letter(opt)_digit+/digit{4}
        # Also handles BVGer file numbers like A-2682/2007
        pattern_file = re.compile(r"\b([A-Z0-9]+[\-_\.]\d+/\d{4})\b", re.IGNORECASE)
        matches_file = pattern_file.findall(text)
        for c in matches_file:
            citations.add(c.upper())  # Normalize to uppercase

        return sorted(list(citations))

    def _extract_case_id(self, text: str) -> str:
        # Try to find standard ID format in text if filename didn't have it
        # 1. Federal format often appears in header "Geschäftsnummer: 6B_..."
        match_fed = re.search(r"(?:Geschäftsnummer|Reference|Numéro de dossier|Incarto)\s*[:]?\s*([0-9A-Z_/\.\-]+)", text, re.IGNORECASE)
        if match_fed:
            return match_fed.group(1).strip()

        # 2. Fallback: Urteil vom ... (Ref)
        match = re.search(r"(?:Urteil|Arrêt|Sentenza)\s+(?:vom|du|del)\s+\d+\.?\s+\w+\.?\s+\d{4}\s+\(([^)]+)\)", text, re.IGNORECASE)
        if match:
            return match.group(1).replace(" ", "_")

        return None

    def _extract_date(self, text: str) -> str:
        # Matches DD.MM.YYYY, DD. MM. YYYY, D.M.YYYY
        # BE CAREFUL: "2. Kammer" could look like a date part. We need full date.
        match = re.search(r"(\d{1,2})\.?\s+(\d{1,2}|[a-zA-Zäöüéà]+)\.?\s+(\d{4})", text)
        if match:
            day, month_raw, year = match.groups()

            # Map month names if present
            month_map = {
                "januar": "01", "janvier": "01", "gennaio": "01",
                "februar": "02", "février": "02", "febbraio": "02",
                "märz": "03", "mars": "03", "marzo": "03",
                "april": "04", "avril": "04", "aprile": "04",
                "mai": "05", "mai": "05", "maggio": "05",
                "juni": "06", "juin": "06", "giugno": "06",
                "juli": "07", "juillet": "07", "luglio": "07",
                "august": "08", "août": "08", "agosto": "08",
                "september": "09", "septembre": "09", "settembre": "09",
                "oktober": "10", "octobre": "10", "ottobre": "10",
                "november": "11", "novembre": "11", "novembre": "11",
                "dezember": "12", "décembre": "12", "dicembre": "12"
            }

            month = month_raw
            if month_raw.lower() in month_map:
                month = month_map[month_raw.lower()]
            elif not month_raw.isdigit():
                # If we captured text that isn't a known month, this might not be a date
                pass

            if month.isdigit():
                return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        return None

    def _extract_outcome(self, text: str) -> str:
        # Look for the decision keywords near the end of the text
        text_end = text[-3000:].lower()  # Look at last 3000 chars

        outcomes = {
            "approved": ["gutgeheissen", "gutzuheissen", "admis", "accolto", "accoglie"],
            "dismissed": ["abgewiesen", "rejeté", "respinto", "respinge"],
            "inadmissible": ["nichteintreten", "irrecevable", "inammissibile", "non entra nel merito"]
        }

        for result, keywords in outcomes.items():
            for kw in keywords:
                if kw in text_end:
                    return result
        return "unknown"

    def _extract_judges(self, text: str) -> List[str]:
        """
        Extract judge names from court decision text.
        Handles German, French, and Italian naming conventions.
        """
        # Role words to filter out (not names)
        role_words = {
            # German (singular and plural)
            'richter', 'richterin', 'richterinnen',
            'bundesrichter', 'bundesrichterin', 'bundesrichterinnen',
            'präsident', 'präsidentin', 'präsidenten',
            'vorsitzende', 'vorsitzender', 'vorsitzenden',
            'gerichtsschreiber', 'gerichtsschreiberin', 'kanzler',
            'oberrichter', 'oberrichterin', 'oberrichterinnen',
            # French (singular and plural)
            'juge', 'juges', 'président', 'présidente', 'présidents',
            'greffier', 'greffière', 'greffiers',
            'fédéral', 'fédérale', 'fédéraux',
            'pénal', 'pénale', 'pénaux', 'civil', 'civile', 'civils',
            'suppléant', 'suppléante',
            # Italian (singular and plural)
            'giudice', 'giudici', 'presidente', 'presidenti',
            'cancelliere', 'cancelliera', 'cancellieri',
            'federale', 'federali', 'penale', 'penali', 'civile', 'civili',
            'supplente', 'supplenti',
            # Common titles/noise
            'mm', 'mme', 'mmes', 'mr', 'dr', 'prof', 'me', 'herr', 'frau',
            'et', 'und', 'e', 'la', 'le', 'il', 'der', 'die', 'das',
            'les', 'des', 'della', 'del', 'di', 'den', 'dem',
        }

        judges = []

        # Try multiple extraction strategies

        # 1. Look for "Besetzung/Composition" section
        # End at clerk/greffier section OR participants section
        match = re.search(
            r"(?:Besetzung|Composition|Composizione)\s*[:\n]?\s*(.*?)(?:Greffièr|Greffier|Gerichtsschreiber|Cancellier|Parteien|Parties|Parti|Participants|Verfahrensbeteiligte)",
            text, re.DOTALL | re.IGNORECASE
        )

        if match:
            raw = match.group(1).strip()
            judges = self._parse_judge_names(raw, role_words)

        # 2. If no judges found, try French pattern "La juge ... Nom"
        if not judges:
            # Pattern: "La/Le juge (fédéral(e)) (pénal(e)) Prénom Nom"
            fr_pattern = r"(?:La|Le|Les)\s+juges?\s+(?:[\w\s]+\s+)?([A-Z][a-zéèêëàâäùûüôöîïç\-]+(?:\s+[A-Z][a-zéèêëàâäùûüôöîïç\-]+)+)"
            fr_matches = re.findall(fr_pattern, text[:3000])
            for name in fr_matches:
                name = name.strip()
                if self._is_valid_judge_name(name, role_words):
                    judges.append(name)

        # 3. Try to find names after "juges:" or similar
        if not judges:
            match2 = re.search(
                r"(?:juges?|richter|giudici)\s*[:\n]\s*(.+?)(?:\n\n|Greffier|Gerichtsschreiber|Cancellier)",
                text[:3000], re.DOTALL | re.IGNORECASE
            )
            if match2:
                raw = match2.group(1).strip()
                judges = self._parse_judge_names(raw, role_words)

        return judges[:6]  # Limit to reasonable number

    def _parse_judge_names(self, raw_text: str, role_words: set) -> List[str]:
        """Parse judge names from raw text, filtering out role words."""
        # Normalize whitespace
        raw_text = re.sub(r'\s+', ' ', raw_text).strip()

        # Split by various separators
        # Handle "et", "und", "e" (and), plus commas, semicolons, newlines
        parts = re.split(r'[,;\n]+|\s+(?:et|und|e)\s+', raw_text)

        judges = []
        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Extract actual names from phrases like "Bundesrichter Müller"
            # Look for capitalized words that aren't role words
            words = part.split()
            name_parts = []

            for word in words:
                # Clean punctuation
                word_clean = re.sub(r'[,;:\.]$', '', word).strip()
                if not word_clean:
                    continue

                # Check if it's a role word
                if word_clean.lower() in role_words:
                    continue

                # Check if it looks like a name (starts with uppercase, reasonable length)
                if len(word_clean) >= 2 and word_clean[0].isupper():
                    # Skip common non-name patterns
                    if word_clean.lower() in {'ii', 'iii', 'iv', 'ab', 'vom', 'am', 'im', 'zu'}:
                        continue
                    name_parts.append(word_clean)

            # Combine name parts
            if name_parts:
                name = ' '.join(name_parts)
                if self._is_valid_judge_name(name, role_words):
                    judges.append(name)

        return judges

    def _is_valid_judge_name(self, name: str, role_words: set) -> bool:
        """Check if a string looks like a valid judge name."""
        if not name or len(name) < 3:
            return False

        # Must start with uppercase
        if not name[0].isupper():
            return False

        # Should not be a single role word
        if name.lower() in role_words:
            return False

        # Should have at least 2 characters that aren't punctuation
        alpha_chars = sum(1 for c in name if c.isalpha())
        if alpha_chars < 3:
            return False

        # Should not be all uppercase (likely an acronym)
        if name.isupper() and len(name) > 3:
            return False

        return True

    def _extract_lower_court(self, text: str) -> str:
        # Enhanced regex to catch more variations
        # German: "Gegen das Urteil des/der ...", "Vorinstanz: Obergericht ..."
        # French: "Contre l'arrêt de la Cour ...", "Instance précédente: ..."
        # Italian: "Contro la sentenza della Camera ...", "Istanza precedente: ..."

        # 1. Narrative context ("Gegen das Urteil des...") - TRY THIS FIRST
        match_narrative = re.search(r"(?:gegen\s+(?:das|den)\s+(?:Urteil|Entscheid|Beschluss)|contre\s+(?:l'arrêt|le jugement|la décision)|contro\s+(?:la sentenza|il giudizio|la decisione))\s+(?:des|della|du|de la|der|di)\s+(.*?)(?:vom|du|del|\.|,)", text, re.IGNORECASE | re.DOTALL)
        if match_narrative:
            cand = match_narrative.group(1).strip().replace("\n", " ")
            if len(cand) < 200:
                return cand

        # 2. Explicit labels
        match_explicit = re.search(r"(?:Vorinstanz|Instance précédente|Istanza precedente)\s*[:]?\s+(.+?)(?:\n|$|Beschwerde|Recourant|Ricorrente)", text, re.IGNORECASE)
        if match_explicit:
            cand = match_explicit.group(1).strip().split('\n')[0]
            if len(cand) > 3 and not cand.lower().startswith(("seien", "ist", "sind", "und", "dass")):
                return cand.strip()

        return None

    def _extract_regeste(self, text: str) -> str:
        # Regeste usually appears before the facts (A.-)
        # Capture text between explicit "Regeste" header and start of facts
        match = re.search(r"(?:Regeste|Regeste|Regesto)(.*?)(?:A\.-|Sachverhalt|Faits|Fatti|F a t t i)", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None
