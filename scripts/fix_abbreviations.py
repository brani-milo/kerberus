"""
Fix outdated abbreviations in abbreviations.json.

The registry had old 1931 law abbreviations (ANAG/LSEE/LDDS)
for SR 142.20 instead of current 2005/2007 law (AIG/LEI/LStrI).
"""

import json
from pathlib import Path

# Path to abbreviations file
ABBREV_FILE = Path(__file__).parent.parent / "data" / "fedlex" / "metadata" / "abbreviations.json"

# Corrections for outdated abbreviations
# Maps SR number -> correct current abbreviations
CORRECTIONS = {
    "142.20": {
        "de": "AIG",
        "title_de": "Bundesgesetz über die Ausländerinnen und Ausländer und über die Integration (Ausländer- und Integrationsgesetz, AIG)",
        "fr": "LEI",
        "title_fr": "Loi fédérale sur les étrangers et l'intégration (Loi sur les étrangers et l'intégration, LEI)",
        "it": "LStrI",
        "title_it": "Legge federale sugli stranieri e la loro integrazione (Legge sugli stranieri e sull'integrazione, LStrI)"
    },
    "142.201": {
        "de": "VZAE",
        "title_de": "Verordnung über Zulassung, Aufenthalt und Erwerbstätigkeit (VZAE)",
        "fr": "OASA",
        "title_fr": "Ordonnance relative à l'admission, au séjour et à l'exercice d'une activité lucrative (OASA)",
        "it": "OASA",
        "title_it": "Ordinanza sull'ammissione, il soggiorno e l'attività lucrativa (OASA)"
    }
}


def fix_abbreviations():
    """Update abbreviations.json with correct current abbreviations."""

    if not ABBREV_FILE.exists():
        print(f"Error: {ABBREV_FILE} not found")
        return False

    # Load current data
    with open(ABBREV_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    by_sr = data.get("by_sr", {})

    # Apply corrections
    for sr_number, correct_abbrev in CORRECTIONS.items():
        old_abbrev = by_sr.get(sr_number, {})
        print(f"SR {sr_number}:")
        print(f"  OLD: de={old_abbrev.get('de')}, fr={old_abbrev.get('fr')}, it={old_abbrev.get('it')}")
        print(f"  NEW: de={correct_abbrev.get('de')}, fr={correct_abbrev.get('fr')}, it={correct_abbrev.get('it')}")

        by_sr[sr_number] = correct_abbrev

    data["by_sr"] = by_sr

    # Save updated data
    with open(ABBREV_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nUpdated {ABBREV_FILE}")
    return True


if __name__ == "__main__":
    fix_abbreviations()
