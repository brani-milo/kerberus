"""Fedlex parser: the trailing table of contents must not become heading-only duplicate articles."""
from src.parsers.fedlex_parser import FedlexParser

SAMPLE = """Bundesgesetz
betreffend die Ergänzung des Schweizerischen Zivilgesetzbuches
(Fünfter Teil: Obligationenrecht)

Art. 339d
1 Erhält der Arbeitnehmer Leistungen von einer Personalfürsorgeeinrichtung, so können sie
ihm auf die Abgangsentschädigung angerechnet werden.

Art. 340
1 Der handlungsfähige Arbeitnehmer kann sich gegenüber dem Arbeitgeber schriftlich verpflichten,
nach Beendigung des Arbeitsverhältnisses sich jeder konkurrenzierenden Tätigkeit zu enthalten.
2 Das Konkurrenzverbot ist nur verbindlich, wenn das Arbeitsverhältnis dem Arbeitnehmer
Einblick in den Kundenkreis gewährt.

Art. 340a
1 Das Verbot ist nach Ort, Zeit und Gegenstand angemessen zu begrenzen.

Art. 340b
1 Übertritt der Arbeitnehmer das Konkurrenzverbot, so hat er den Schaden zu ersetzen.

Art. 127
Mit Ablauf von zehn Jahren verjähren alle Forderungen, für die das
Bundeszivilrecht nicht etwas anderes bestimmt.
""" + "\nFüllzeile\n" * 60 + """
Inhaltsverzeichnis
Das Obligationenrecht
Zehnter Titel: Der Arbeitsvertrag
c. Ersatzleistungen
Art. 339d
VII. Konkurrenzverbot
1. Voraussetzungen
Art. 340
2. Beschränkungen
Art. 340a
3. Folgen der Übertretung
Art. 340b
1. Zehn Jahre
Art. 127
"""


def test_toc_is_not_parsed_as_articles_and_titles_are_attached():
    parser = FedlexParser()
    articles = parser.parse_text(SAMPLE, "220", "de", "SR_220_de.pdf")
    by = {a["article_number"]: a for a in articles}

    assert len(articles) == len(by) == 5                    # no duplicates
    assert by["340"]["article_text"].startswith("1 Der handlungsfähige Arbeitnehmer")
    assert by["340a"]["article_text"].startswith("1 Das Verbot ist nach Ort")
    assert "Beschränkungen" not in by["340"]["article_text"]  # the old bug
    assert by["340"]["article_title"] == "VII. Konkurrenzverbot 1. Voraussetzungen"
    assert by["340a"]["article_title"] == "2. Beschränkungen"
    assert by["339d"]["article_title"] == "c. Ersatzleistungen"
    # single-paragraph article: first body line must not be swallowed as a title
    assert by["127"]["article_title"] == "1. Zehn Jahre"
    assert by["127"]["article_text"].startswith("Mit Ablauf von zehn Jahren")


def test_dedupe_keeps_longest_entry():
    parser = FedlexParser()
    arts = [{"id": "x", "article_number": "1", "article_text": "2. Heading"},
            {"id": "x", "article_number": "1", "article_text": "1 Real body text of the article"},
            {"id": "y", "article_number": "2", "article_text": "short"}]
    out = parser._dedupe_articles(arts, "220", "de")
    assert [a["id"] for a in out] == ["x", "y"] and out[0]["article_text"].startswith("1 Real")


def test_no_toc_marker_means_no_split():
    parser = FedlexParser()
    body, toc = parser._split_toc("Art. 1\nText\nInhaltsverzeichnis\n" + "x\n" * 100, "de")
    assert toc == ""   # marker too early in the document to be the trailing TOC
