"""
Fedlex PDF parser - extracts articles with hierarchical metadata.

Uses PyMuPDF (fitz) to extract text, then parses structure using regex patterns.
Implements article-based chunking with rich hierarchical metadata for optimal retrieval.

Cross-language support:
- Each article has a language-neutral base_id for cross-language linking
- Abbreviations are loaded from the registry for citation matching
- A query in Italian can find German articles and return Italian equivalents
"""

import json
import logging
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


class FedlexParser:
    """
    Parser for Fedlex PDF files.

    Extracts articles with full hierarchical context and metadata.
    Uses article-based chunking with rich metadata for optimal retrieval.

    Swiss law structure:
    Law (Gesetz/Loi/Legge)
    └─ Part (Teil/Partie/Parte)
       └─ Title (Titel/Titre/Titolo)
          └─ Chapter (Kapitel/Chapitre/Capitolo)
             └─ Section (Abschnitt/Section/Sezione)
                └─ Article (Artikel/Article/Articolo)
    """

    # Regex patterns for article detection
    ARTICLE_PATTERNS = {
        "de": r'^Art\.\s*(\d+[a-z]*(?:bis|ter|quater|quinquies)?)\s*$',
        "fr": r'^Art\.\s*(\d+[a-z]*(?:bis|ter|quater|quinquies)?)\s*$',
        "it": r'^Art\.\s*(\d+[a-z]*(?:bis|ter|quater|quinquies)?)\s*$'
    }

    # Inline article pattern (Art. X Title on same line)
    ARTICLE_INLINE_PATTERNS = {
        "de": r'^Art\.\s*(\d+[a-z]*(?:bis|ter|quater|quinquies)?)\s+(.+)$',
        "fr": r'^Art\.\s*(\d+[a-z]*(?:bis|ter|quater|quinquies)?)\s+(.+)$',
        "it": r'^Art\.\s*(\d+[a-z]*(?:bis|ter|quater|quinquies)?)\s+(.+)$'
    }

    # Hierarchical structure patterns
    PART_PATTERNS = {
        "de": r'^(\d+)\.\s*Teil[:\s]+(.+)$',
        "fr": r'^(\d+)[eè]?\s*[Pp]artie[:\s]+(.+)$',
        "it": r'^[Pp]arte\s+(\d+)[aª]?[:\s]+(.+)$'
    }

    TITLE_PATTERNS = {
        "de": r'^(\d+)\.\s*Titel[:\s]+(.+)$',
        "fr": r'^[Tt]itre\s+(\d+)[eè]?[:\s]+(.+)$',
        "it": r'^[Tt]itolo\s+(\d+)[oº]?[:\s]+(.+)$'
    }

    CHAPTER_PATTERNS = {
        "de": r'^(\d+)\.\s*Kapitel[:\s]+(.+)$',
        "fr": r'^[Cc]hapitre\s+(\d+)[eè]?[:\s]+(.+)$',
        "it": r'^[Cc]apitolo\s+(\d+)[oº]?[:\s]+(.+)$'
    }

    SECTION_PATTERNS = {
        "de": r'^(?:(\d+)\.\s*)?Abschnitt[:\s]+(.+)$',
        "fr": r'^[Ss]ection\s+(\d+)?[:\s]*(.+)$',
        "it": r'^[Ss]ezione\s+(\d+)?[:\s]*(.+)$'
    }

    # Domain classification keywords
    DOMAIN_KEYWORDS = {
        "employment": [
            "arbeit", "anstellung", "arbeitnehmer", "arbeitgeber", "kündigung", "lohn",
            "travail", "employé", "employeur", "salaire", "licenciement",
            "lavoro", "lavoratore", "datore", "salario", "licenziamento"
        ],
        "contract": [
            "vertrag", "obligation", "schuld", "leistung", "erfüllung",
            "contrat", "obligation", "dette", "prestation",
            "contratto", "obbligazione", "debito", "prestazione"
        ],
        "property": [
            "eigentum", "besitz", "grundbuch", "pfand", "hypothek",
            "propriété", "possession", "registre", "gage", "hypothèque",
            "proprietà", "possesso", "registro", "pegno", "ipoteca"
        ],
        "tort": [
            "schaden", "haftung", "unerlaubt", "delikt",
            "dommage", "responsabilité", "illicite", "délit",
            "danno", "responsabilità", "illecito", "delitto"
        ],
        "family": [
            "familie", "ehe", "kind", "vormund", "adoption",
            "famille", "mariage", "enfant", "tutelle",
            "famiglia", "matrimonio", "figlio", "tutela", "adozione"
        ],
        "inheritance": [
            "erbrecht", "erbe", "testament", "nachlass",
            "succession", "héritier", "testament",
            "successione", "erede", "testamento"
        ],
        "criminal": [
            "straf", "verbrechen", "vergehen", "schuld", "vorsatz",
            "pénal", "crime", "délit", "faute", "intention",
            "penale", "reato", "delitto", "colpa", "dolo"
        ],
        "tax": [
            "steuer", "abgabe", "fiskal",
            "impôt", "fiscal", "taxe",
            "imposta", "fiscale", "tassa"
        ],
        "asylum": [
            "asyl", "flüchtling", "ausweis",
            "asile", "réfugié",
            "asilo", "rifugiato"
        ],
        "social_security": [
            "versicherung", "rente", "altersvorsorge", "invalidität",
            "assurance", "rente", "prévoyance", "invalidité",
            "assicurazione", "rendita", "previdenza", "invalidità"
        ],
    }

    # Maximum words per chunk before splitting
    MAX_WORDS_PER_CHUNK = 500

    # Default path to abbreviations registry
    ABBREVIATIONS_REGISTRY_PATH = Path(__file__).parent.parent.parent / "data" / "fedlex" / "metadata" / "abbreviations.json"

    def __init__(self, abbreviations_path: Optional[Path] = None):
        """
        Initialize parser.

        Args:
            abbreviations_path: Optional path to abbreviations.json registry.
                              If None, uses default location.
        """
        self._abbreviations_path = abbreviations_path or self.ABBREVIATIONS_REGISTRY_PATH
        self._abbreviations_registry: Optional[Dict] = None
        self._load_abbreviations_registry()

    def _load_abbreviations_registry(self):
        """Load the abbreviations registry for SR -> abbreviation mapping."""
        if self._abbreviations_path.exists():
            try:
                with open(self._abbreviations_path, 'r', encoding='utf-8') as f:
                    self._abbreviations_registry = json.load(f)
                logger.info(f"Loaded abbreviations registry with {len(self._abbreviations_registry.get('by_sr', {}))} laws")
            except Exception as e:
                logger.warning(f"Failed to load abbreviations registry: {e}")
                self._abbreviations_registry = None
        else:
            logger.warning(f"Abbreviations registry not found at {self._abbreviations_path}")
            self._abbreviations_registry = None

    def get_abbreviations_for_sr(self, sr_number: str) -> Dict[str, str]:
        """
        Get abbreviations for a given SR number in all languages.

        Args:
            sr_number: SR number (e.g., "220", "311.0")

        Returns:
            Dict with language -> abbreviation mapping, e.g., {"de": "OR", "fr": "CO", "it": "CO"}
        """
        if not self._abbreviations_registry:
            return {}

        by_sr = self._abbreviations_registry.get("by_sr", {})
        entry = by_sr.get(sr_number, {})

        # Extract only the abbreviation fields (de, fr, it), not titles
        return {
            lang: entry.get(lang)
            for lang in ("de", "fr", "it")
            if entry.get(lang)
        }

    def parse_pdf(self, pdf_path: Path, sr_number: str, language: str) -> List[Dict]:
        """
        Parse a Fedlex PDF file into structured articles.

        Args:
            pdf_path: Path to PDF file
            sr_number: SR number (e.g., "220", "311.0")
            language: Language code (de, fr, it)

        Returns:
            List of article dictionaries
        """
        logger.info(f"Parsing {pdf_path.name} (SR {sr_number}, {language})")

        try:
            # Extract text from PDF
            doc = fitz.open(pdf_path)
            full_text = ""

            for page in doc:
                page_text = page.get_text()
                # Remove page headers like "1 / 100"
                page_text = re.sub(r'^\d+\s*/\s*\d+\s*\n', '', page_text)
                full_text += page_text + "\n"

            doc.close()

            # Clean up text
            full_text = self._clean_text(full_text)

            # Extract law name
            sr_name = self._extract_law_name(full_text, language)

            # Classify law type
            law_type = self._classify_law_type(sr_number)

            # Parse hierarchical structure and articles
            articles = self._parse_articles(
                full_text, sr_number, sr_name, language, law_type, str(pdf_path)
            )

            logger.info(f"Extracted {len(articles)} articles from {pdf_path.name}")

            return articles

        except Exception as e:
            logger.error(f"Failed to parse {pdf_path.name}: {e}")
            raise

    def _clean_text(self, text: str) -> str:
        """Clean and normalize PDF text."""
        # Remove multiple consecutive blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)

        # Remove page numbers at start of lines
        text = re.sub(r'^\d+\s*$', '', text, flags=re.MULTILINE)

        # Normalize whitespace within lines (but preserve newlines)
        lines = text.split('\n')
        cleaned_lines = []
        for line in lines:
            # Collapse multiple spaces
            line = re.sub(r'  +', ' ', line)
            cleaned_lines.append(line.strip())

        return '\n'.join(cleaned_lines)

    def _extract_law_name(self, text: str, language: str) -> str:
        """Extract the law name from PDF text."""
        lines = text.split('\n')

        # Skip empty lines and look for first substantial line
        for line in lines[:15]:
            line = line.strip()
            # Skip SR numbers, page numbers, empty lines
            if not line:
                continue
            if re.match(r'^\d+(\.\d+)*$', line):  # SR number
                continue
            if re.match(r'^\d+\s*/\s*\d+$', line):  # Page number
                continue
            if len(line) < 10:
                continue
            if len(line) > 200:
                continue
            # This is likely the title
            return line

        return "Unknown"

    def _classify_law_type(self, sr_number: str) -> str:
        """Classify law type based on SR number ranges."""
        try:
            # Handle both "220" and "311.0" formats
            sr_parts = sr_number.split('.')
            sr_int = int(sr_parts[0])

            if sr_int < 100:
                return "international"
            elif 100 <= sr_int < 200:
                return "constitutional"
            elif 200 <= sr_int < 300:
                return "civil"
            elif 300 <= sr_int < 400:
                return "criminal"
            elif 400 <= sr_int < 500:
                return "education"
            elif 500 <= sr_int < 600:
                return "defense"
            elif 600 <= sr_int < 700:
                return "finance"
            elif 700 <= sr_int < 800:
                return "infrastructure"
            elif 800 <= sr_int < 900:
                return "social"
            elif 900 <= sr_int < 1000:
                return "economic"
            else:
                return "other"
        except (ValueError, IndexError):
            return "other"

    def _parse_articles(
        self,
        text: str,
        sr_number: str,
        sr_name: str,
        language: str,
        law_type: str,
        file_path: str
    ) -> List[Dict]:
        """
        Parse articles from text with hierarchical context.

        Returns:
            List of article dictionaries
        """
        articles = []
        lines = text.split('\n')

        # Track current hierarchy context
        current_part = None
        current_title = None
        current_chapter = None
        current_section = None

        # Find all article positions
        article_positions = []

        for i, line in enumerate(lines):
            line_stripped = line.strip()

            # Update hierarchy tracking
            part_match = re.match(self.PART_PATTERNS.get(language, self.PART_PATTERNS["de"]), line_stripped, re.IGNORECASE)
            if part_match:
                current_part = f"{part_match.group(1)}. Teil: {part_match.group(2)}"
                current_title = None  # Reset lower levels
                current_chapter = None
                current_section = None
                continue

            title_match = re.match(self.TITLE_PATTERNS.get(language, self.TITLE_PATTERNS["de"]), line_stripped, re.IGNORECASE)
            if title_match:
                current_title = f"{title_match.group(1)}. Titel: {title_match.group(2)}"
                current_chapter = None  # Reset lower levels
                current_section = None
                continue

            chapter_match = re.match(self.CHAPTER_PATTERNS.get(language, self.CHAPTER_PATTERNS["de"]), line_stripped, re.IGNORECASE)
            if chapter_match:
                current_chapter = f"{chapter_match.group(1)}. Kapitel: {chapter_match.group(2)}"
                current_section = None  # Reset lower level
                continue

            section_match = re.match(self.SECTION_PATTERNS.get(language, self.SECTION_PATTERNS["de"]), line_stripped, re.IGNORECASE)
            if section_match:
                num = section_match.group(1) or ""
                name = section_match.group(2)
                current_section = f"Abschnitt {num}: {name}".strip() if num else f"Abschnitt: {name}"
                continue

            # Check for article
            # First try inline pattern (Art. X Title)
            inline_match = re.match(self.ARTICLE_INLINE_PATTERNS.get(language, self.ARTICLE_INLINE_PATTERNS["de"]), line_stripped)
            if inline_match:
                article_positions.append({
                    "line_idx": i,
                    "article_number": inline_match.group(1),
                    "article_title": inline_match.group(2).strip(),
                    "part": current_part,
                    "title": current_title,
                    "chapter": current_chapter,
                    "section": current_section
                })
                continue

            # Then try standalone pattern (Art. X on its own line)
            standalone_match = re.match(self.ARTICLE_PATTERNS.get(language, self.ARTICLE_PATTERNS["de"]), line_stripped)
            if standalone_match:
                # Title is on next line
                article_title = None
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    # Check if next line is a title (not numbered paragraph, not empty, not another Art.)
                    if next_line and not re.match(r'^[\d¹²³]+', next_line) and not next_line.startswith('Art.'):
                        article_title = next_line

                article_positions.append({
                    "line_idx": i,
                    "article_number": standalone_match.group(1),
                    "article_title": article_title,
                    "part": current_part,
                    "title": current_title,
                    "chapter": current_chapter,
                    "section": current_section
                })
                continue

        if not article_positions:
            logger.warning(f"No articles found in SR {sr_number} ({language})")
            return []

        # Extract article texts
        for i, pos in enumerate(article_positions):
            start_line = pos["line_idx"]

            # Find end of article (start of next article or end of text)
            if i < len(article_positions) - 1:
                end_line = article_positions[i + 1]["line_idx"]
            else:
                end_line = len(lines)

            # Extract article content
            article_lines = lines[start_line:end_line]

            # Skip the article number line and optional title line
            content_start = 1
            if pos["article_title"] and len(article_lines) > 1:
                # Check if second line matches the title
                if article_lines[1].strip() == pos["article_title"]:
                    content_start = 2

            article_text = '\n'.join(article_lines[content_start:]).strip()

            # Clean up article text
            article_text = self._clean_article_text(article_text)

            if not article_text:
                continue

            # Build hierarchy path
            hierarchy_path = self._build_hierarchy_path(
                sr_name, pos["part"], pos["title"], pos["chapter"],
                pos["section"], pos["article_number"]
            )

            # Extract cross-references
            cites_articles = self._extract_citations(article_text)

            # Classify domain
            domain = self._classify_domain(pos["article_title"] or "", article_text, language)

            # Check word count for chunking
            word_count = len(article_text.split())

            if word_count > self.MAX_WORDS_PER_CHUNK:
                # Split into paragraphs
                paragraphs = self._split_into_paragraphs(article_text)

                for para_num, para_text in enumerate(paragraphs, 1):
                    if not para_text.strip():
                        continue

                    article_obj = self._create_article_object(
                        sr_number=sr_number,
                        sr_name=sr_name,
                        article_number=pos["article_number"],
                        article_title=pos["article_title"],
                        article_text=para_text,
                        hierarchy_path=hierarchy_path,
                        part=pos["part"],
                        title=pos["title"],
                        chapter=pos["chapter"],
                        section=pos["section"],
                        law_type=law_type,
                        domain=domain,
                        cites_articles=cites_articles,
                        language=language,
                        file_path=file_path,
                        paragraph_number=para_num,
                        is_partial=True
                    )
                    articles.append(article_obj)
            else:
                # Single article chunk
                article_obj = self._create_article_object(
                    sr_number=sr_number,
                    sr_name=sr_name,
                    article_number=pos["article_number"],
                    article_title=pos["article_title"],
                    article_text=article_text,
                    hierarchy_path=hierarchy_path,
                    part=pos["part"],
                    title=pos["title"],
                    chapter=pos["chapter"],
                    section=pos["section"],
                    law_type=law_type,
                    domain=domain,
                    cites_articles=cites_articles,
                    language=language,
                    file_path=file_path
                )
                articles.append(article_obj)

        return articles

    def _clean_article_text(self, text: str) -> str:
        """Clean article text content."""
        # Remove footnote markers (superscript numbers)
        text = re.sub(r'[¹²³⁴⁵⁶⁷⁸⁹⁰]+', '', text)

        # Remove standalone SR number references at end
        text = re.sub(r'\n\d+(\.\d+)*\s*$', '', text)

        # Remove "Fassung gemäss..." footnotes
        text = re.sub(r'Fassung gemäss.+?(?=\n|$)', '', text, flags=re.IGNORECASE)
        text = re.sub(r'Eingefügt durch.+?(?=\n|$)', '', text, flags=re.IGNORECASE)
        text = re.sub(r'Aufgehoben durch.+?(?=\n|$)', '', text, flags=re.IGNORECASE)

        # Collapse multiple newlines
        text = re.sub(r'\n{2,}', '\n', text)

        return text.strip()

    def _build_hierarchy_path(
        self,
        sr_name: str,
        part: Optional[str],
        title: Optional[str],
        chapter: Optional[str],
        section: Optional[str],
        article_number: str
    ) -> str:
        """Build hierarchical path string."""
        parts = [sr_name]

        if part:
            # Extract just the number and type
            match = re.match(r'(\d+)\.\s*(Teil|Partie|Parte)', part, re.IGNORECASE)
            if match:
                parts.append(f"{match.group(1)}. {match.group(2)}")
            else:
                parts.append(part.split(':')[0].strip())

        if title:
            match = re.match(r'(\d+)\.\s*(Titel|Titre|Titolo)', title, re.IGNORECASE)
            if match:
                parts.append(f"{match.group(1)}. {match.group(2)}")
            else:
                parts.append(title.split(':')[0].strip())

        if chapter:
            match = re.match(r'(\d+)\.\s*(Kapitel|Chapitre|Capitolo)', chapter, re.IGNORECASE)
            if match:
                parts.append(f"{match.group(1)}. {match.group(2)}")
            else:
                parts.append(chapter.split(':')[0].strip())

        if section:
            parts.append(section.split(':')[0].strip())

        parts.append(f"Art. {article_number}")

        return " > ".join(parts)

    def _extract_citations(self, text: str) -> List[str]:
        """Extract article citations from text."""
        # Pattern: Art. 123, Art. 45bis, Art. 12 Abs. 3, etc.
        pattern = r'Art\.\s*(\d+[a-z]*(?:bis|ter|quater|quinquies)?)'
        matches = re.findall(pattern, text)
        return sorted(list(set(matches)))  # Remove duplicates and sort

    def _classify_domain(self, title: str, text: str, language: str) -> str:
        """Classify legal domain based on keywords."""
        combined = (title + " " + text).lower()

        for domain, keywords in self.DOMAIN_KEYWORDS.items():
            for keyword in keywords:
                if keyword in combined:
                    return domain

        return "general"

    def _split_into_paragraphs(self, text: str) -> List[str]:
        """Split long article into semantic paragraphs."""
        paragraphs = []

        # Try splitting on numbered paragraphs first (1, 2, 3 or ¹, ², ³)
        numbered_pattern = r'(?:^|\n)(\d+)\s+'
        parts = re.split(numbered_pattern, text)

        if len(parts) > 2:
            # Recombine with numbers
            current = parts[0].strip()
            if current:
                paragraphs.append(current)

            for i in range(1, len(parts), 2):
                if i + 1 < len(parts):
                    para = f"{parts[i]} {parts[i + 1]}".strip()
                    if para:
                        paragraphs.append(para)
        else:
            # Try splitting on lettered items (a), b), c) or a., b., c.)
            lettered_pattern = r'(?:^|\n)([a-z][\.\)])\s+'
            parts = re.split(lettered_pattern, text, flags=re.IGNORECASE)

            if len(parts) > 2:
                current = parts[0].strip()
                if current:
                    paragraphs.append(current)

                for i in range(1, len(parts), 2):
                    if i + 1 < len(parts):
                        para = f"{parts[i]} {parts[i + 1]}".strip()
                        if para:
                            paragraphs.append(para)
            else:
                # Fallback: split on double newlines or just return as is
                raw_paragraphs = text.split('\n\n')
                paragraphs = [p.strip() for p in raw_paragraphs if p.strip()]

        # If still only one paragraph and it's long, split by sentences
        if len(paragraphs) == 1 and len(paragraphs[0].split()) > self.MAX_WORDS_PER_CHUNK:
            # Simple sentence split (approximate)
            sentences = re.split(r'(?<=[.!?])\s+', paragraphs[0])

            current_chunk = []
            current_words = 0
            new_paragraphs = []

            for sentence in sentences:
                words = len(sentence.split())
                if current_words + words > self.MAX_WORDS_PER_CHUNK and current_chunk:
                    new_paragraphs.append(' '.join(current_chunk))
                    current_chunk = [sentence]
                    current_words = words
                else:
                    current_chunk.append(sentence)
                    current_words += words

            if current_chunk:
                new_paragraphs.append(' '.join(current_chunk))

            paragraphs = new_paragraphs

        return paragraphs if paragraphs else [text]

    def _create_article_object(
        self,
        sr_number: str,
        sr_name: str,
        article_number: str,
        article_title: Optional[str],
        article_text: str,
        hierarchy_path: str,
        part: Optional[str],
        title: Optional[str],
        chapter: Optional[str],
        section: Optional[str],
        law_type: str,
        domain: str,
        cites_articles: List[str],
        language: str,
        file_path: str,
        paragraph_number: Optional[int] = None,
        is_partial: bool = False
    ) -> Dict:
        """Create article dictionary object with cross-language support."""
        # Clean SR number for ID (replace dots with underscores)
        sr_clean = sr_number.replace(".", "_")
        article_clean = article_number.replace(".", "_")

        # Build language-neutral base_id for cross-language linking
        # This allows finding the same article in different languages
        base_id = f"SR_{sr_clean}_Art_{article_clean}"
        if paragraph_number:
            base_id += f"_p{paragraph_number}"

        # Build language-specific unique ID
        article_id = f"{base_id}_{language}"

        # Get abbreviations from registry
        abbreviations_all = self.get_abbreviations_for_sr(sr_number)
        abbreviation = abbreviations_all.get(language)  # Current language abbreviation

        return {
            "id": article_id,
            "base_id": base_id,  # Language-neutral ID for cross-language linking
            "sr_number": sr_number,
            "sr_name": sr_name,
            "abbreviation": abbreviation,  # e.g., "OR" for German, "CO" for French
            "abbreviations_all": abbreviations_all,  # {"de": "OR", "fr": "CO", "it": "CO"}
            "article_number": article_number,
            "article_title": article_title,
            "article_text": article_text,

            "hierarchy_path": hierarchy_path,
            "part": part,
            "title": title,
            "chapter": chapter,
            "section": section,

            "law_type": law_type,
            "domain": domain,
            "subdomain": None,  # Can be enhanced later

            "cites_articles": cites_articles,
            "paragraph_number": paragraph_number,
            "is_partial": is_partial,

            "language": language,
            "source": "fedlex",
            "file_path": file_path
        }
