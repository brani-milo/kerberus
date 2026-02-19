#!/usr/bin/env python3
"""Test the full pipeline: guard enhance → embed → search"""
import sys
sys.path.insert(0, "/app")
import numpy as np

from src.llm.pipeline import get_pipeline
from src.embedder.bge_embedder import get_embedder
from qdrant_client import QdrantClient

pipeline = get_pipeline()
embedder = get_embedder()
client = QdrantClient(host="qdrant", port=6333)

query = "Darf ein Mitarbeiter vertrauliche Kundendaten an Dritte weitergeben?"

print(f"Original query: {query}")
print()

# Step 1: Guard & Enhance
result = pipeline.guard_and_enhance(query)
enhanced = result.enhanced_query
print(f"Enhanced query: {enhanced}")
print(f"Legal concepts: {result.legal_concepts}")
print()

# Step 2: Embed enhanced query
qvec = embedder._encode_single(enhanced)["dense"]

# Step 3: Search
search_results = client.query_points(
    collection_name="codex",
    query=qvec,
    using="dense",
    limit=30,
    with_payload=True
).points

print("Top 30 search results:")
found_321a = False
for i, p in enumerate(search_results):
    sr = p.payload.get("sr_number", "?")
    art = p.payload.get("article_number", "?")
    abbr = p.payload.get("abbreviation", "?")
    domain = p.payload.get("domain", "?")

    marker = ""
    if art == "321a":
        marker = " <-- ART. 321a FOUND!"
        found_321a = True

    print(f"  {i+1}. SR {sr} Art. {art} ({abbr}) [{domain}] - score: {p.score:.4f}{marker}")

print()
if found_321a:
    print("SUCCESS: Art. 321a is now found in the search results!")
else:
    print("WARNING: Art. 321a still not in top 30")
