#!/usr/bin/env python3
"""Test semantic search for Art. 321a"""
import sys
sys.path.insert(0, "/app")
import numpy as np
from src.embedder.bge_embedder import get_embedder
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

client = QdrantClient(host="qdrant", port=6333)
embedder = get_embedder()

# Test queries
queries = [
    "Darf ein Mitarbeiter vertrauliche Kundendaten an Dritte weitergeben?",
    "Treuepflicht Arbeitnehmer Interessen Arbeitgeber wahren",
    "OR Art. 321a Sorgfaltspflicht Geheimhaltung",
    "Pflichten des Arbeitnehmers Arbeitsvertrag"
]

# Get Art. 321a (DE) with vector
results = client.scroll(
    collection_name="codex",
    scroll_filter=Filter(must=[
        FieldCondition(key="article_number", match=MatchValue(value="321a")),
        FieldCondition(key="language", match=MatchValue(value="de"))
    ]),
    limit=1,
    with_vectors=True,
    with_payload=True
)

if not results[0]:
    print("Art. 321a not found!")
    sys.exit(1)

point = results[0][0]
art_text = point.payload.get("text_preview", "N/A")
print(f"Art. 321a text: {art_text[:150]}...")
print()

# Get Art. 321a vector
art_vec = np.array(point.vector["dense"])

for i, q in enumerate(queries, 1):
    qvec = np.array(embedder._encode_single(q)["dense"])
    sim = float(np.dot(qvec, art_vec) / (np.linalg.norm(qvec) * np.linalg.norm(art_vec)))
    print(f"Query {i}: {q}")
    print(f"  Similarity to Art. 321a: {sim:.4f}")

    # What rank does Art. 321a get?
    search_results = client.query_points(
        collection_name="codex",
        query=qvec.tolist(),
        using="dense",
        limit=100,
        with_payload=True
    ).points

    rank = None
    for j, p in enumerate(search_results):
        if p.payload.get("article_number") == "321a":
            rank = j + 1
            break

    if rank:
        print(f"  Art. 321a rank: #{rank}/100")
    else:
        print(f"  Art. 321a rank: NOT IN TOP 100")

    # Show top 3
    top3 = []
    for p in search_results[:3]:
        sr = p.payload.get("sr_number", "?")
        art = p.payload.get("article_number", "?")
        abbr = p.payload.get("abbreviation", "?")
        top3.append(f"SR {sr} Art. {art} ({abbr}) [{p.score:.3f}]")
    print(f"  Top 3: {'; '.join(top3)}")
    print()
