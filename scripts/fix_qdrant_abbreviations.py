"""
Fix outdated abbreviations in Qdrant Codex collection.

Updates payload fields directly without re-embedding.
"""

import logging
from qdrant_client import QdrantClient, models

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Corrections for outdated abbreviations
# Format: sr_number -> { language: (old_abbrev, new_abbrev, new_title) }
CORRECTIONS = {
    "142.20": {
        "de": ("ANAG", "AIG", "Bundesgesetz über die Ausländerinnen und Ausländer und über die Integration (AIG)"),
        "fr": ("LSEE", "LEI", "Loi fédérale sur les étrangers et l'intégration (LEI)"),
        "it": ("LDDS", "LStrI", "Legge federale sugli stranieri e la loro integrazione (LStrI)"),
    },
    "142.201": {
        "de": ("ANAV", "VZAE", "Verordnung über Zulassung, Aufenthalt und Erwerbstätigkeit (VZAE)"),
        "fr": ("RSEE", "OASA", "Ordonnance relative à l'admission, au séjour et à l'exercice d'une activité lucrative (OASA)"),
        "it": ("ODDS", "OASA", "Ordinanza sull'ammissione, il soggiorno e l'attività lucrativa (OASA)"),
    }
}


def fix_qdrant_abbreviations(host: str = "localhost", port: int = 6333):
    """Update abbreviations in Qdrant payloads."""

    client = QdrantClient(host=host, port=port)
    collection = "codex"

    for sr_number, lang_corrections in CORRECTIONS.items():
        logger.info(f"Processing SR {sr_number}...")

        # Get all points with this SR number
        offset = None
        total_updated = 0

        while True:
            results = client.scroll(
                collection_name=collection,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="sr_number",
                            match=models.MatchValue(value=sr_number)
                        )
                    ]
                ),
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )

            points, next_offset = results

            if not points:
                break

            # Update each point
            for point in points:
                payload = point.payload
                language = payload.get("language", "")
                old_abbrev = payload.get("abbreviation", "")

                if language in lang_corrections:
                    expected_old, new_abbrev, new_title = lang_corrections[language]

                    if old_abbrev == expected_old:
                        # Update the payload with new abbreviation and title
                        client.set_payload(
                            collection_name=collection,
                            payload={
                                "abbreviation": new_abbrev,
                                "sr_name": new_title,
                                "law_title": new_title
                            },
                            points=[point.id]
                        )
                        total_updated += 1

            if next_offset is None:
                break
            offset = next_offset

        logger.info(f"  Updated {total_updated} points for SR {sr_number}")

    logger.info("Done!")


if __name__ == "__main__":
    fix_qdrant_abbreviations()
