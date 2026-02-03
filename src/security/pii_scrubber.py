"""
PII Detection and Scrubbing for KERBERUS.

Provides Swiss-specific PII detection using Microsoft Presidio,
with custom recognizers for:
- AHV numbers (Swiss social security)
- Swiss phone numbers
- Swiss postcodes and addresses

Usage:
    from src.security.pii_scrubber import PIIScrubber

    scrubber = PIIScrubber()

    # Detect PII
    entities = scrubber.detect("My AHV is 756.1234.5678.90")

    # Scrub PII
    clean_text = scrubber.scrub("Call me at +41 79 123 45 67")
    # Returns: "Call me at <PHONE_NUMBER>"
"""
import os
import re
import logging
from typing import List, Dict, Optional, Set
from dataclasses import dataclass

from presidio_analyzer import AnalyzerEngine, RecognizerResult, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

logger = logging.getLogger(__name__)


# ============================================
# Swiss-Specific Pattern Recognizers
# ============================================

class SwissAHVRecognizer(PatternRecognizer):
    """
    Recognizer for Swiss AHV/AVS numbers (social security).

    Format: 756.XXXX.XXXX.XX (13 digits with country code 756)
    Can appear with dots, spaces, or no separators.
    """

    PATTERNS = [
        Pattern(
            "AHV_WITH_DOTS",
            r"\b756[.\s]?\d{4}[.\s]?\d{4}[.\s]?\d{2}\b",
            0.95
        ),
        Pattern(
            "AHV_NO_SEPARATOR",
            r"\b756\d{10}\b",
            0.85
        ),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="SWISS_AHV",
            patterns=self.PATTERNS,
            context=["ahv", "avs", "sozialversicherung", "social security", "assurance sociale"],
            supported_language="de",
        )


class SwissPhoneRecognizer(PatternRecognizer):
    """
    Recognizer for Swiss phone numbers.

    Formats:
    - +41 XX XXX XX XX
    - 0XX XXX XX XX
    - +41XXXXXXXXX
    """

    PATTERNS = [
        Pattern(
            "SWISS_INTL",
            r"\+41\s?[\d\s]{9,12}",
            0.85
        ),
        Pattern(
            "SWISS_NATIONAL",
            r"\b0[1-9]\d[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}\b",
            0.80
        ),
        Pattern(
            "SWISS_MOBILE",
            r"\b07[5-9][\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}\b",
            0.85
        ),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="SWISS_PHONE",
            patterns=self.PATTERNS,
            context=["telefon", "phone", "tel", "mobile", "handy", "natel"],
            supported_language="de",
        )


class SwissIBANRecognizer(PatternRecognizer):
    """
    Recognizer for Swiss IBAN numbers.

    Format: CH## #### #### #### #### # (21 characters)
    """

    PATTERNS = [
        Pattern(
            "SWISS_IBAN",
            r"\bCH\d{2}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{1}\b",
            0.95
        ),
        Pattern(
            "SWISS_IBAN_COMPACT",
            r"\bCH\d{19}\b",
            0.90
        ),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="SWISS_IBAN",
            patterns=self.PATTERNS,
            context=["iban", "konto", "account", "bank", "überweisung"],
            supported_language="de",
        )


class SwissPostcodeRecognizer(PatternRecognizer):
    """
    Recognizer for Swiss postcodes (PLZ).

    Format: 4 digits (1000-9999), often followed by city name.
    Lower confidence as 4-digit numbers are common.
    """

    PATTERNS = [
        Pattern(
            "PLZ_WITH_CITY",
            r"\b[1-9]\d{3}\s+[A-ZÄÖÜ][a-zäöüéèê]+\b",
            0.75
        ),
    ]

    def __init__(self):
        super().__init__(
            supported_entity="SWISS_POSTCODE",
            patterns=self.PATTERNS,
            context=["plz", "postleitzahl", "code postal", "npa", "adresse", "wohnort"],
            supported_language="de",
        )


# ============================================
# PII Entity Types
# ============================================

@dataclass
class PIIEntity:
    """Detected PII entity."""
    entity_type: str
    text: str
    start: int
    end: int
    score: float


# ============================================
# Main PII Scrubber Class
# ============================================

class PIIScrubber:
    """
    PII detection and scrubbing engine for Swiss legal documents.

    Uses Microsoft Presidio with custom Swiss recognizers.

    Attributes:
        enabled: Whether PII scrubbing is active.
        entity_types: Set of entity types to detect.
        min_score: Minimum confidence score for detection.
    """

    # Default entity types to detect
    DEFAULT_ENTITIES = {
        # Standard Presidio entities
        "PERSON",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "IBAN_CODE",
        "CREDIT_CARD",
        "DATE_TIME",
        "LOCATION",
        "NRP",  # National Registration/ID numbers
        # Swiss-specific entities
        "SWISS_AHV",
        "SWISS_PHONE",
        "SWISS_IBAN",
        "SWISS_POSTCODE",
    }

    # Entity types that should NOT be scrubbed in legal context
    # (e.g., dates are essential for legal analysis)
    LEGAL_WHITELIST = {
        "DATE_TIME",  # Dates are critical for legal analysis
    }

    def __init__(
        self,
        enabled: Optional[bool] = None,
        entity_types: Optional[Set[str]] = None,
        min_score: float = 0.7,
        languages: List[str] = None,
    ):
        """
        Initialize PII scrubber.

        Args:
            enabled: Enable/disable scrubbing. Defaults to env var ENABLE_PII_SCRUBBING.
            entity_types: Entity types to detect. Defaults to DEFAULT_ENTITIES.
            min_score: Minimum confidence score (0.0-1.0).
            languages: Languages to support. Defaults to ["de", "fr", "it", "en"].
        """
        if enabled is None:
            enabled = os.getenv("ENABLE_PII_SCRUBBING", "true").lower() == "true"

        self.enabled = enabled
        self.entity_types = entity_types or self.DEFAULT_ENTITIES
        self.min_score = min_score
        self.languages = languages or ["de", "fr", "it", "en"]

        self._analyzer: Optional[AnalyzerEngine] = None
        self._anonymizer: Optional[AnonymizerEngine] = None

        if self.enabled:
            self._initialize_engines()

    def _initialize_engines(self) -> None:
        """Initialize Presidio analyzer and anonymizer engines."""
        try:
            # Create NLP engine with spaCy
            # Use smaller models for efficiency
            nlp_config = {
                "nlp_engine_name": "spacy",
                "models": [
                    {"lang_code": "de", "model_name": "de_core_news_sm"},
                    {"lang_code": "en", "model_name": "en_core_web_sm"},
                ],
            }

            try:
                provider = NlpEngineProvider(nlp_configuration=nlp_config)
                nlp_engine = provider.create_engine()
            except Exception as e:
                logger.warning(f"Could not load spaCy models, using default: {e}")
                nlp_engine = None

            # Create analyzer with custom recognizers
            self._analyzer = AnalyzerEngine(
                nlp_engine=nlp_engine,
                supported_languages=self.languages,
            )

            # Add Swiss-specific recognizers
            self._analyzer.registry.add_recognizer(SwissAHVRecognizer())
            self._analyzer.registry.add_recognizer(SwissPhoneRecognizer())
            self._analyzer.registry.add_recognizer(SwissIBANRecognizer())
            self._analyzer.registry.add_recognizer(SwissPostcodeRecognizer())

            # Create anonymizer
            self._anonymizer = AnonymizerEngine()

            logger.info("PII scrubber initialized with Swiss recognizers")

        except Exception as e:
            logger.error(f"Failed to initialize PII scrubber: {e}")
            self.enabled = False

    def detect(
        self,
        text: str,
        language: str = "de",
        entity_types: Optional[Set[str]] = None,
    ) -> List[PIIEntity]:
        """
        Detect PII entities in text.

        Args:
            text: Text to analyze.
            language: Language code (de, fr, it, en).
            entity_types: Override entity types to detect.

        Returns:
            List of detected PII entities.
        """
        if not self.enabled or not self._analyzer:
            return []

        if not text or not text.strip():
            return []

        entities_to_detect = list(entity_types or self.entity_types)

        try:
            results = self._analyzer.analyze(
                text=text,
                language=language,
                entities=entities_to_detect,
                score_threshold=self.min_score,
            )

            return [
                PIIEntity(
                    entity_type=r.entity_type,
                    text=text[r.start:r.end],
                    start=r.start,
                    end=r.end,
                    score=r.score,
                )
                for r in results
            ]

        except Exception as e:
            logger.error(f"PII detection failed: {e}")
            return []

    def scrub(
        self,
        text: str,
        language: str = "de",
        replacement_format: str = "<{entity_type}>",
        preserve_legal_dates: bool = True,
    ) -> str:
        """
        Scrub PII from text by replacing with placeholders.

        Args:
            text: Text to scrub.
            language: Language code.
            replacement_format: Format for replacements. Use {entity_type} placeholder.
            preserve_legal_dates: If True, don't scrub dates (important for legal context).

        Returns:
            Text with PII replaced by placeholders.
        """
        if not self.enabled or not self._analyzer or not self._anonymizer:
            return text

        if not text or not text.strip():
            return text

        # Determine which entities to scrub
        entities_to_scrub = self.entity_types.copy()
        if preserve_legal_dates:
            entities_to_scrub = entities_to_scrub - self.LEGAL_WHITELIST

        try:
            # Detect entities
            results = self._analyzer.analyze(
                text=text,
                language=language,
                entities=list(entities_to_scrub),
                score_threshold=self.min_score,
            )

            if not results:
                return text

            # Create operator config for replacement
            operators = {}
            for entity_type in entities_to_scrub:
                replacement = replacement_format.format(entity_type=entity_type)
                operators[entity_type] = OperatorConfig("replace", {"new_value": replacement})

            # Anonymize
            anonymized = self._anonymizer.anonymize(
                text=text,
                analyzer_results=results,
                operators=operators,
            )

            return anonymized.text

        except Exception as e:
            logger.error(f"PII scrubbing failed: {e}")
            return text

    def scrub_for_logging(self, text: str, language: str = "de") -> str:
        """
        Scrub PII for safe logging.

        More aggressive scrubbing, includes dates.

        Args:
            text: Text to scrub.
            language: Language code.

        Returns:
            Text safe for logging.
        """
        return self.scrub(
            text=text,
            language=language,
            replacement_format="[REDACTED:{entity_type}]",
            preserve_legal_dates=False,
        )

    def get_pii_summary(self, text: str, language: str = "de") -> Dict[str, int]:
        """
        Get summary of PII types found in text.

        Args:
            text: Text to analyze.
            language: Language code.

        Returns:
            Dict mapping entity type to count.
        """
        entities = self.detect(text, language)
        summary: Dict[str, int] = {}

        for entity in entities:
            summary[entity.entity_type] = summary.get(entity.entity_type, 0) + 1

        return summary

    def has_pii(self, text: str, language: str = "de") -> bool:
        """
        Check if text contains any PII.

        Args:
            text: Text to check.
            language: Language code.

        Returns:
            True if PII detected.
        """
        entities = self.detect(text, language)
        return len(entities) > 0


# ============================================
# Singleton Instance
# ============================================

_scrubber_instance: Optional[PIIScrubber] = None


def get_pii_scrubber() -> PIIScrubber:
    """
    Get singleton PII scrubber instance.

    Returns:
        PIIScrubber instance.
    """
    global _scrubber_instance
    if _scrubber_instance is None:
        _scrubber_instance = PIIScrubber()
    return _scrubber_instance


# ============================================
# Convenience Functions
# ============================================

def scrub_pii(text: str, language: str = "de") -> str:
    """
    Convenience function to scrub PII from text.

    Args:
        text: Text to scrub.
        language: Language code.

    Returns:
        Scrubbed text.
    """
    return get_pii_scrubber().scrub(text, language)


def detect_pii(text: str, language: str = "de") -> List[PIIEntity]:
    """
    Convenience function to detect PII in text.

    Args:
        text: Text to analyze.
        language: Language code.

    Returns:
        List of detected PII entities.
    """
    return get_pii_scrubber().detect(text, language)


def has_pii(text: str, language: str = "de") -> bool:
    """
    Convenience function to check if text contains PII.

    Args:
        text: Text to check.
        language: Language code.

    Returns:
        True if PII detected.
    """
    return get_pii_scrubber().has_pii(text, language)
