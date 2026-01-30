import re
from typing import Dict, Any, List

class MetadataExtractor:
    """
    Central Logic for extracting metadata from Swiss Legal Texts.
    Handles Multilingual Regex (DE/FR/IT).
    """

    # We only accept these codes. This prevents "Art. 2 al." from being treated as a law.
    VALID_CODES = {
        # Federal
        "CP", "STGB", "CODE PÉNAL", "CODICE PENALE",
        "CC", "ZGB", "CODE CIVIL", "CODICE CIVILE",
        "CO", "OR", "OBLIGATIONENRECHT", "CODICE DELLE OBBLIGAZIONI",
        "LP", "LEF", "SCHKG",
        "CPC", "ZPO", "CPP", "STPO",
        "LEI", "AIG", "LSTR", "AUG",
        "LIFD", "DBG", "LIFD",
        "LAMAL", "KVG",
        "LCA", "VVG",
        "LCSTR", "SVG",
        "LAVS", "AHVG",
        "LAI", "IVG",
        "LPP", "BVG",
        "LAA", "UVG",
        "LAPG", "EOG",
        "LACI", "AVIG",
        "LTF", "BGG", # Bundesgerichtsgesetz
        "LTAF", "VGG", # Verwaltungsgerichtsgesetz
        "VGKE", # Kostenreglement BVGer
        "PA", "VWVG", "VWG", # Verwaltungsverfahrensgesetz
        "VEV", # Visa Ordinance
        "BV", "CST", "COSTE", # Constitution
        "GWG", "LBA", "LRD", # Money Laundering
        "EMRK", "CEDH", # Human Rights Convention
        "ASYLG", "LASI", # Asylum
        "BETMG", "LSTUP", # Narcotics
        "MWSTG", "LTVA", # VAT
        "ATSG", "LPGA", # Social Security General Part
        "FZA", "ALCP", # Free Movement
        "RPG", "LAT", # Spatial Planning
        "USG", "LPE", # Environment
        "KKG", "LCC", # Consumer Credit
        "FUSG", "LFUS", # Merger
        "STHG", "LHID", # Tax Harmonization
        "KAG", "LPCC", # Collective Investment
        "FINMAG", "LFINMA", # Financial Market Supervision
        "LTRANS", # Transparency Law
        "LEMB", # Embargo Law
        "LPERS", # Personnel Law
        # Cantonal & Ticino specific
        "LPA", "LPAMM", "LT", "LAFE", "LALC", # Removed "LE" (conflicts with French "Le")
        "LIP", "LOC", "LOG", "LPROG", "LAC", "RLC"
    }

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
        Extracts valid law citations using the Whitelist.
        Handles: "Art. 337 OR", "art. 4 et 5 LAA", "art. 123 CP", "Art. 23 ... (GwG)"
        Also handles lists: "Art. 113, 117 LTF", "Art. 10, 11 und 12 StGB"
        """
        clean_citations = set()
        
        # Dynamically build the code pattern from the whitelist to avoid false positives (e.g. "Abs")
        # Sort by length descending to match longest first
        # Use word boundaries (\b) to avoid partial matches
        sorted_codes = sorted([re.escape(c) for c in self.VALID_CODES], key=len, reverse=True)
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
                code_clean = code.upper().strip() # Keep dots for SR
            else:
                code_clean = code.upper().replace(".", "").strip()
            
            # THE FILTER: Only accept if in whitelist or starts with SR
            if code_clean in self.VALID_CODES or code_clean.startswith("SR"):
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
            citations.add(c.upper()) # Normalize to uppercase

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
                # But let's check if it's digit
                pass
            
            if month.isdigit():
                 return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        return None

    def _extract_outcome(self, text: str) -> str:
        # Look for the decision keywords near the end of the text
        text_end = text[-3000:].lower() # Look at last 3000 chars
        
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
        # Heuristic: Look for "Besetzung" line
        match = re.search(r"(?:Besetzung|Composition|Composizione|Giudici|Richter)(.*?)(?:Parteien|Parties|Parti|Cancellieri|Gerichtsschreiber)", text, re.DOTALL | re.IGNORECASE)
        if match:
            raw = match.group(1).strip()
            # Clean up newlines and common titles
            # Remove "Bundesrichter", "Mme la Juge", etc. to clean up could be hard, 
            # so we just split by comma or newline
            
            # Simple approach: split by typical separators
            names = re.split(r'[,;\n]+', raw)
            
            clean_names = []
            for n in names:
                n = n.strip()
                # Filtering noise: "MM.", "Mme", "Präsident"
                # We keep the name if it looks like a name (uppercase start, length)
                if len(n) > 3 and not n.lower().startswith("partei"):
                    clean_names.append(n)
            
            return clean_names[:5] # Limit to avoid garbage
        return []

    def _extract_lower_court(self, text: str) -> str:
        # Enhanced regex to catch more variations
        # German: "Gegen das Urteil des/der ...", "Vorinstanz: Obergericht ..."
        # French: "Contre l'arrêt de la Cour ...", "Instance précédente: ..."
        # Italian: "Contro la sentenza della Camera ...", "Istanza precedente: ..."
        
        # 1. Narrative context ("Gegen das Urteil des...") - TRY THIS FIRST as it's often more specific in the header
        # Added 'der' for feminine/plural genitive (e.g. "der Strafkammer")
        match_narrative = re.search(r"(?:gegen\s+(?:das|den)\s+(?:Urteil|Entscheid|Beschluss)|contre\s+(?:l'arrêt|le jugement|la décision)|contro\s+(?:la sentenza|il giudizio|la decisione))\s+(?:des|della|du|de la|der|di)\s+(.*?)(?:vom|du|del|\.|,)", text, re.IGNORECASE | re.DOTALL)
        if match_narrative:
             cand = match_narrative.group(1).strip().replace("\n", " ")
             if len(cand) < 200:
                return cand

        # 2. Explicit labels
        # Look for "Vorinstanz:" followed by text, usually at start of line or distinct block
        match_explicit = re.search(r"(?:Vorinstanz|Instance précédente|Istanza precedente)\s*[:]?\s+(.+?)(?:\n|$|Beschwerde|Recourant|Ricorrente)", text, re.IGNORECASE)
        if match_explicit:
            cand = match_explicit.group(1).strip().split('\n')[0] # Only take the first line
            # Heuristic: Lower courts are usually not "seien aufzuheben" (verbs)
            # If it starts with a verb or conjunction, ignore
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