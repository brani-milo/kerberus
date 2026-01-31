"""
KERBERUS Chainlit Frontend

Sovereign AI Legal Assistant for Swiss Law
Powered by BGE-M3 Hybrid Search + Qdrant
"""

import chainlit as cl
import sys
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.search.hybrid_search import HybridSearchEngine

# Search engines for different collections
codex_engine = None   # Laws (Fedlex)
library_engine = None  # Court decisions (BGE, cantonal)


@cl.on_chat_start
async def on_chat_start():
    global codex_engine, library_engine

    # Initialize search engines lazily
    if codex_engine is None:
        codex_engine = HybridSearchEngine(collection_name="codex")
    if library_engine is None:
        library_engine = HybridSearchEngine(collection_name="library")

    # Set up filters as settings
    settings = await cl.ChatSettings(
        [
            cl.input_widget.Select(
                id="search_scope",
                label="Search Scope",
                values=["Both", "Laws (Codex)", "Decisions (Library)"],
                initial_value="Both",
            ),
            cl.input_widget.Switch(
                id="multilingual",
                label="Cross-Language Mode",
                initial=False,
                description="Enable for conceptual queries across DE/FR/IT (e.g., 'what is unfair dismissal'). Keep OFF for exact citations (e.g., 'Art. 337 OR', 'BGE 119 III 63')."
            ),
            cl.input_widget.Slider(
                id="year_min",
                label="Min Year (Decisions only)",
                min=1900,
                max=2026,
                initial=1950,
                step=1,
            ),
            cl.input_widget.Slider(
                id="year_max",
                label="Max Year (Decisions only)",
                min=1900,
                max=2026,
                initial=2026,
                step=1,
            ),
            cl.input_widget.Select(
                id="language",
                label="Filter by Language",
                values=["All", "German (DE)", "French (FR)", "Italian (IT)"],
                initial_value="All",
            ),
        ]
    ).send()


def format_law_result(payload: dict, score: float, rank: int) -> str:
    """Format a law article result."""
    abbrev = payload.get('abbreviation', 'SR')
    art_num = payload.get('article_number', '?')
    art_title = payload.get('article_title', '')
    sr_num = payload.get('sr_number', '')
    lang = payload.get('language', '').upper()

    # Build citation
    citation = f"{abbrev} Art. {art_num}"
    if art_title:
        citation += f" - {art_title}"

    # Get text preview
    text = payload.get('article_text', payload.get('text_preview', ''))
    preview = text[:400] + "..." if len(text) > 400 else text

    return f"""### {rank}. {citation}
**SR {sr_num}** • `{lang}` • Score: **{score:.3f}**

> {preview}

---
"""


def format_decision_result(payload: dict, score: float, rank: int) -> str:
    """Format a court decision result."""
    # Try to get case ID - prefer decision_id for cleaner display
    decision_id = payload.get('decision_id', '')
    case_id = payload.get('_original_id', payload.get('id', 'Unknown'))

    # Clean up case ID for display
    if decision_id and 'BGE-' in decision_id:
        # BGE-119-III-63 -> BGE 119 III 63
        parts = decision_id.split('BGE-')[-1].split('_')[0]
        citation = f"BGE {parts.replace('-', ' ')}"
    elif isinstance(case_id, str):
        if 'BGE-' in case_id:
            parts = case_id.split('BGE-')[-1].split('_')[0]
            citation = f"BGE {parts.replace('-', ' ')}"
        elif case_id.startswith('CH_'):
            citation = case_id.replace('CH_', '').replace('_', ' ')
        else:
            citation = case_id
    else:
        citation = str(case_id)

    # Remove chunk suffix from citation
    if '_chunk_' in citation:
        citation = citation.split('_chunk_')[0]

    # Get metadata
    year = payload.get('year', 'Unknown')
    court = payload.get('court', '')
    language = payload.get('language', '').upper()
    chunk_type = payload.get('chunk_type', '')

    # Map court codes to readable names
    court_names = {
        'CH_BGE': 'Federal Supreme Court',
        'CH_BGer': 'Federal Supreme Court',
        'CH_BVGer': 'Federal Administrative Court',
        'CH_BStGer': 'Federal Criminal Court',
        'CH_TI': 'Canton Ticino',
        'CH_TI_CATI': 'Ticino Tax Court',
    }
    court_display = court_names.get(court, court or 'Unknown Court')

    # Get preview text - check text_preview first (main field for library)
    text = payload.get('text_preview', '')
    if not text:
        content = payload.get('content', {})
        if isinstance(content, dict):
            text = content.get('regeste') or content.get('reasoning', '')
        else:
            text = payload.get('text', '')

    # Clean and truncate preview
    if text:
        preview = str(text)[:400]
        if len(str(text)) > 400:
            preview += "..."
    else:
        preview = "_No preview available_"

    # Add chunk type indicator if present
    type_label = f" [{chunk_type}]" if chunk_type else ""

    return f"""### {rank}. {citation}{type_label}
**{year}** • _{court_display}_ • `{language}` • Score: **{score:.3f}**

> {preview}

---
"""


@cl.on_settings_update
async def setup_agent(settings):
    cl.user_session.set("filters", settings)


@cl.on_message
async def on_message(message: cl.Message):
    global codex_engine, library_engine

    # Get user settings
    settings = cl.user_session.get("filters") or {}

    # Build base filters (applicable to both collections)
    base_filters = {}

    # Language filter (works for both codex and library)
    lang_setting = settings.get("language", "All")
    if lang_setting != "All":
        lang_map = {"German (DE)": "de", "French (FR)": "fr", "Italian (IT)": "it"}
        if lang_setting in lang_map:
            base_filters["language"] = lang_map[lang_setting]

    # Year filter (only for library - codex doesn't have year field)
    library_filters = base_filters.copy()
    year_min = settings.get("year_min", 1950)
    year_max = settings.get("year_max", 2026)
    if year_min or year_max:
        library_filters["year_range"] = {"min": int(year_min), "max": int(year_max)}

    # Codex filters (no year filter - laws don't have publication year in metadata)
    codex_filters = base_filters.copy()

    # Start response
    msg = cl.Message(content="Searching Swiss legal database...")
    await msg.send()

    try:
        search_scope = settings.get("search_scope", "Both")
        response_parts = []

        # Check multilingual mode
        multilingual = settings.get("multilingual", True)

        # Search Codex (Laws) - no year filter
        if search_scope in ["Both", "Laws (Codex)"]:
            try:
                codex_results = codex_engine.search(
                    message.content,
                    limit=5,
                    filters=codex_filters if codex_filters else None,
                    multilingual=multilingual
                )
                if codex_results:
                    mode_label = "cross-language" if multilingual else "hybrid"
                    response_parts.append(f"## Swiss Laws (Codex)\n")
                    for i, res in enumerate(codex_results, 1):
                        response_parts.append(format_law_result(
                            res.get('payload', {}),
                            res.get('score', 0),
                            i
                        ))
            except Exception as e:
                response_parts.append(f"_Codex search error: {str(e)}_\n\n")

        # Search Library (Decisions) - with year filter
        if search_scope in ["Both", "Decisions (Library)"]:
            try:
                library_results = library_engine.search(
                    message.content,
                    limit=5,
                    filters=library_filters if library_filters else None,
                    multilingual=multilingual
                )
                if library_results:
                    response_parts.append(f"## Court Decisions (Library)\n")
                    for i, res in enumerate(library_results, 1):
                        response_parts.append(format_decision_result(
                            res.get('payload', {}),
                            res.get('score', 0),
                            i
                        ))
            except Exception as e:
                response_parts.append(f"_Library search error: {str(e)}_\n\n")

        # Build final response
        if response_parts:
            msg.content = "\n".join(response_parts)
        else:
            msg.content = "No results found. Try a different query or adjust your filters."

        await msg.update()

    except Exception as e:
        msg.content = f"**Error:** {str(e)}"
        await msg.update()
