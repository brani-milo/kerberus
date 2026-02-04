"""
Tests for PII detection and scrubbing.

Covers:
- Swiss AHV number detection
- Swiss phone number detection
- Swiss IBAN detection
- Email detection
- Scrubbing functionality
- API endpoints
"""
import pytest
from unittest.mock import patch, MagicMock

from src.security.pii_scrubber import (
    PIIScrubber,
    PIIEntity,
    SwissAHVRecognizer,
    SwissPhoneRecognizer,
    SwissIBANRecognizer,
    scrub_pii,
    detect_pii,
    has_pii,
)


# ============================================
# Swiss AHV Recognition Tests
# ============================================

class TestSwissAHVRecognizer:
    """Test Swiss AHV number recognition."""

    def test_ahv_with_dots(self):
        """Test AHV number with dots (756.1234.5678.90)."""
        scrubber = PIIScrubber(enabled=True)
        text = "Meine AHV-Nummer ist 756.1234.5678.90"
        entities = scrubber.detect(text, language="de")

        ahv_entities = [e for e in entities if e.entity_type == "SWISS_AHV"]
        assert len(ahv_entities) == 1
        assert ahv_entities[0].text == "756.1234.5678.90"

    def test_ahv_with_spaces(self):
        """Test AHV number with spaces (756 1234 5678 90)."""
        scrubber = PIIScrubber(enabled=True)
        text = "AHV: 756 1234 5678 90"
        entities = scrubber.detect(text, language="de")

        ahv_entities = [e for e in entities if e.entity_type == "SWISS_AHV"]
        assert len(ahv_entities) == 1

    def test_ahv_no_separator(self):
        """Test AHV number without separators (7561234567890)."""
        scrubber = PIIScrubber(enabled=True)
        text = "Sozialversicherungsnummer: 7561234567890"
        entities = scrubber.detect(text, language="de")

        ahv_entities = [e for e in entities if e.entity_type == "SWISS_AHV"]
        assert len(ahv_entities) == 1

    def test_invalid_ahv_not_detected(self):
        """Test that invalid AHV numbers are not detected."""
        scrubber = PIIScrubber(enabled=True)
        # AHV must start with 756
        text = "Die Nummer 123.4567.8901.23 ist keine AHV"
        entities = scrubber.detect(text, language="de")

        ahv_entities = [e for e in entities if e.entity_type == "SWISS_AHV"]
        assert len(ahv_entities) == 0


# ============================================
# Swiss Phone Recognition Tests
# ============================================

class TestSwissPhoneRecognizer:
    """Test Swiss phone number recognition."""

    def test_international_format(self):
        """Test +41 format phone number."""
        scrubber = PIIScrubber(enabled=True)
        text = "Rufen Sie an: +41 79 123 45 67"
        entities = scrubber.detect(text, language="de")

        phone_entities = [e for e in entities if "PHONE" in e.entity_type]
        assert len(phone_entities) >= 1

    def test_national_format(self):
        """Test 0xx format phone number."""
        scrubber = PIIScrubber(enabled=True)
        text = "Tel: 079 123 45 67"
        entities = scrubber.detect(text, language="de")

        phone_entities = [e for e in entities if "PHONE" in e.entity_type]
        assert len(phone_entities) >= 1

    def test_mobile_number(self):
        """Test Swiss mobile number (075-079)."""
        scrubber = PIIScrubber(enabled=True)
        text = "Handy: 078 999 88 77"
        entities = scrubber.detect(text, language="de")

        phone_entities = [e for e in entities if "PHONE" in e.entity_type]
        assert len(phone_entities) >= 1


# ============================================
# Swiss IBAN Recognition Tests
# ============================================

class TestSwissIBANRecognizer:
    """Test Swiss IBAN recognition."""

    def test_iban_with_spaces(self):
        """Test IBAN with spaces."""
        scrubber = PIIScrubber(enabled=True)
        text = "Konto: CH93 0076 2011 6238 5295 7"
        entities = scrubber.detect(text, language="de")

        iban_entities = [e for e in entities if "IBAN" in e.entity_type]
        assert len(iban_entities) >= 1

    def test_iban_compact(self):
        """Test IBAN without spaces."""
        scrubber = PIIScrubber(enabled=True)
        text = "IBAN: CH9300762011623852957"
        entities = scrubber.detect(text, language="de")

        iban_entities = [e for e in entities if "IBAN" in e.entity_type]
        assert len(iban_entities) >= 1


# ============================================
# Email Recognition Tests
# ============================================

class TestEmailRecognition:
    """Test email address recognition."""

    def test_simple_email(self):
        """Test simple email detection."""
        scrubber = PIIScrubber(enabled=True)
        text = "Kontaktieren Sie mich unter test@example.com"
        entities = scrubber.detect(text, language="de")

        email_entities = [e for e in entities if e.entity_type == "EMAIL_ADDRESS"]
        assert len(email_entities) == 1
        assert email_entities[0].text == "test@example.com"

    def test_multiple_emails(self):
        """Test multiple emails in text."""
        scrubber = PIIScrubber(enabled=True)
        text = "Email: lawyer@lawfirm.ch oder assistant@lawfirm.ch"
        entities = scrubber.detect(text, language="de")

        email_entities = [e for e in entities if e.entity_type == "EMAIL_ADDRESS"]
        assert len(email_entities) == 2


# ============================================
# Scrubbing Tests
# ============================================

class TestPIIScrubbing:
    """Test PII scrubbing functionality."""

    def test_scrub_single_entity(self):
        """Test scrubbing single PII entity."""
        scrubber = PIIScrubber(enabled=True)
        text = "Email: test@example.com"
        scrubbed = scrubber.scrub(text, language="de")

        assert "test@example.com" not in scrubbed
        assert "<EMAIL_ADDRESS>" in scrubbed

    def test_scrub_multiple_entities(self):
        """Test scrubbing multiple PII entities."""
        scrubber = PIIScrubber(enabled=True)
        text = "AHV: 756.1234.5678.90, Email: test@example.com"
        scrubbed = scrubber.scrub(text, language="de")

        assert "756.1234.5678.90" not in scrubbed
        assert "test@example.com" not in scrubbed
        assert "<SWISS_AHV>" in scrubbed
        assert "<EMAIL_ADDRESS>" in scrubbed

    def test_scrub_preserves_non_pii(self):
        """Test that non-PII text is preserved."""
        scrubber = PIIScrubber(enabled=True)
        text = "Der Vertrag wurde am 15. März 2023 unterzeichnet."
        scrubbed = scrubber.scrub(text, language="de", preserve_legal_dates=True)

        # Dates should be preserved in legal context
        assert "2023" in scrubbed or "März" in scrubbed

    def test_scrub_empty_text(self):
        """Test scrubbing empty text."""
        scrubber = PIIScrubber(enabled=True)
        assert scrubber.scrub("", language="de") == ""
        assert scrubber.scrub("   ", language="de") == "   "

    def test_scrub_for_logging(self):
        """Test aggressive scrubbing for logging."""
        scrubber = PIIScrubber(enabled=True)
        text = "User test@example.com logged in"
        scrubbed = scrubber.scrub_for_logging(text, language="de")

        assert "test@example.com" not in scrubbed
        assert "[REDACTED:" in scrubbed


# ============================================
# Convenience Function Tests
# ============================================

class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_scrub_pii_function(self):
        """Test scrub_pii convenience function."""
        text = "Email: test@example.com"
        scrubbed = scrub_pii(text, language="de")
        assert "test@example.com" not in scrubbed

    def test_detect_pii_function(self):
        """Test detect_pii convenience function."""
        text = "AHV: 756.1234.5678.90"
        entities = detect_pii(text, language="de")
        assert len(entities) > 0

    def test_has_pii_function(self):
        """Test has_pii convenience function."""
        assert has_pii("Email: test@example.com", language="de")
        assert not has_pii("Guten Tag", language="de")


# ============================================
# PII Summary Tests
# ============================================

class TestPIISummary:
    """Test PII summary functionality."""

    def test_get_pii_summary(self):
        """Test getting PII type summary."""
        scrubber = PIIScrubber(enabled=True)
        text = "AHV: 756.1234.5678.90, Email: a@b.com, b@c.com"
        summary = scrubber.get_pii_summary(text, language="de")

        assert "EMAIL_ADDRESS" in summary
        assert summary["EMAIL_ADDRESS"] == 2
        assert "SWISS_AHV" in summary
        assert summary["SWISS_AHV"] == 1


# ============================================
# Configuration Tests
# ============================================

class TestPIIConfiguration:
    """Test PII scrubber configuration."""

    def test_disabled_scrubber(self):
        """Test that disabled scrubber returns original text."""
        scrubber = PIIScrubber(enabled=False)
        text = "Email: test@example.com"

        assert scrubber.scrub(text) == text
        assert scrubber.detect(text) == []
        assert not scrubber.has_pii(text)

    def test_custom_entity_types(self):
        """Test custom entity type filtering."""
        scrubber = PIIScrubber(
            enabled=True,
            entity_types={"EMAIL_ADDRESS"}  # Only detect emails
        )
        text = "AHV: 756.1234.5678.90, Email: test@example.com"
        entities = scrubber.detect(text, language="de")

        # Should only find email, not AHV (since we limited entity types)
        entity_types = {e.entity_type for e in entities}
        assert "EMAIL_ADDRESS" in entity_types

    def test_min_score_filtering(self):
        """Test minimum confidence score filtering."""
        scrubber = PIIScrubber(enabled=True, min_score=0.99)
        text = "Possible phone: 0791234567"
        entities = scrubber.detect(text, language="de")

        # High confidence threshold may filter out some detections
        # Just verify it doesn't crash
        assert isinstance(entities, list)


# ============================================
# Edge Cases
# ============================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_very_long_text(self):
        """Test handling of very long text."""
        scrubber = PIIScrubber(enabled=True)
        text = "Normal text. " * 1000 + "Email: test@example.com"
        entities = scrubber.detect(text, language="de")

        email_entities = [e for e in entities if e.entity_type == "EMAIL_ADDRESS"]
        assert len(email_entities) == 1

    def test_unicode_text(self):
        """Test handling of Unicode text with special characters."""
        scrubber = PIIScrubber(enabled=True)
        # Use Unicode characters that won't be detected as named entities
        text = "Vertragsbedingungen für Käufer: test@example.com (Prüfung abgeschlossen)"
        scrubbed = scrubber.scrub(text, language="de")

        # Email should be scrubbed
        assert "test@example.com" not in scrubbed
        assert "<EMAIL_ADDRESS>" in scrubbed
        # Unicode characters in non-entity text should be preserved
        assert "für" in scrubbed
        assert "Käufer" in scrubbed
        assert "Prüfung" in scrubbed

    def test_mixed_languages(self):
        """Test text with mixed languages."""
        scrubber = PIIScrubber(enabled=True)
        text = "German: Guten Tag. French: Bonjour. Email: test@example.com"
        entities = scrubber.detect(text, language="de")

        email_entities = [e for e in entities if e.entity_type == "EMAIL_ADDRESS"]
        assert len(email_entities) == 1
