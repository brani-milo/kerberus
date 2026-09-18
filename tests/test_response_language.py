from src.llm.pipeline import _response_language_note


def test_language_note_names_the_language():
    assert "English" in _response_language_note("en")
    assert "Deutsch" in _response_language_note("de")
    assert "italiano" in _response_language_note("it") and "français" in _response_language_note("fr")
    assert "RESPONSE LANGUAGE" in _response_language_note("xx")
