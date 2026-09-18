#!/usr/bin/env python3
"""Test the guard & enhance stage to see how queries get transformed."""
import sys
sys.path.insert(0, "/app")

from src.llm.pipeline import get_pipeline

pipeline = get_pipeline()

test_queries = [
    "Darf ein Mitarbeiter vertrauliche Kundendaten an Dritte weitergeben?",
    "Can my employee share confidential data?",
    "can I fire someone?",
]

print("=== Testing Guard & Enhance ===\n")

for query in test_queries:
    print(f"Original: {query}")
    result = pipeline.guard_and_enhance(query)
    print(f"Enhanced: {result.enhanced_query}")
    print(f"Concepts: {result.legal_concepts}")
    print(f"Language: {result.detected_language}")
    print(f"Type: {result.query_type}")
    print()
