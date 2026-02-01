"""
KERBERUS Chainlit Frontend

Sovereign AI Legal Assistant for Swiss Law
Three-Stage LLM Pipeline:
1. Mistral 1: Guard & Enhance
2. Search + Rerank
3. Mistral 2: Query Reformulator
4. Qwen: Full Legal Analysis
"""

import chainlit as cl
import sys
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.search.hybrid_search import HybridSearchEngine
from src.llm import get_pipeline, ContextAssembler

# Global instances (initialized lazily)
codex_engine = None
library_engine = None
pipeline = None
context_assembler = None


def get_consistency_indicator(consistency: str, confidence: str) -> str:
    """
    Generate traffic light indicator for response consistency.

    Returns:
        Emoji indicator with label
    """
    indicators = {
        "CONSISTENT": "🟢 Einheitliche Rechtslage",
        "MIXED": "🟡 Gemischte Rechtslage",
        "DIVERGENT": "🔴 Widersprüchliche Rechtslage",
    }

    confidence_labels = {
        "high": "hohe Konfidenz",
        "medium": "mittlere Konfidenz",
        "low": "niedrige Konfidenz",
    }

    indicator = indicators.get(consistency, "🟡 Gemischte Rechtslage")
    conf = confidence_labels.get(confidence, "mittlere Konfidenz")

    return f"{indicator} ({conf})"


@cl.on_chat_start
async def on_chat_start():
    global codex_engine, library_engine, pipeline, context_assembler

    # Initialize search engines lazily
    if codex_engine is None:
        codex_engine = HybridSearchEngine(collection_name="codex")
    if library_engine is None:
        library_engine = HybridSearchEngine(collection_name="library")
    if pipeline is None:
        pipeline = get_pipeline()
    if context_assembler is None:
        context_assembler = ContextAssembler()

    # Initialize chat history
    cl.user_session.set("chat_history", [])

    # Welcome message
    await cl.Message(
        content="""**Willkommen bei KERBERUS** 🛡️

Ich bin Ihr KI-Rechtsassistent für Schweizer Recht. Stellen Sie mir Ihre rechtlichen Fragen auf Deutsch, Französisch oder Italienisch.

**Beispiele:**
- "Unter welchen Umständen ist eine fristlose Kündigung gerechtfertigt?"
- "Quelles sont les conditions du divorce en Suisse?"
- "Cosa dice la legge sul licenziamento immediato?"

_Die Antworten werden mit Gesetzeszitaten und Gerichtsentscheiden belegt._"""
    ).send()

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
                description="Enable for conceptual queries across DE/FR/IT."
            ),
            cl.input_widget.Switch(
                id="show_sources",
                label="Show Sources",
                initial=True,
                description="Show the legal sources found before the AI answer."
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

    citation = f"{abbrev} Art. {art_num}"
    if art_title:
        citation += f" - {art_title}"

    text = payload.get('article_text', payload.get('text_preview', ''))
    preview = text[:300] + "..." if len(text) > 300 else text

    return f"""**{rank}. {citation}**
SR {sr_num} • `{lang}`

> {preview}
"""


def format_decision_result(payload: dict, score: float, rank: int) -> str:
    """Format a court decision result."""
    decision_id = payload.get('decision_id', '')
    case_id = payload.get('_original_id', payload.get('id', 'Unknown'))

    # Clean up case ID for display
    if decision_id and 'BGE-' in decision_id:
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

    if '_chunk_' in citation:
        citation = citation.split('_chunk_')[0]

    year = payload.get('year', '')
    court = payload.get('court', '')

    court_names = {
        'CH_BGE': 'BGer',
        'CH_BGer': 'BGer',
        'CH_BVGer': 'BVGer',
        'CH_BStGer': 'BStGer',
        'CH_TI': 'TI',
    }
    court_display = court_names.get(court, court or '')

    text = payload.get('text_preview', '')
    if not text:
        content = payload.get('content', {})
        if isinstance(content, dict):
            text = content.get('regeste') or content.get('reasoning', '')

    preview = str(text)[:250] + "..." if len(str(text)) > 250 else str(text)

    meta = f"{year}" if year else ""
    if court_display:
        meta += f" • {court_display}" if meta else court_display

    return f"""**{rank}. {citation}**
{meta}

> {preview}
"""


def format_sources_collapsible(codex_results: list, library_results: list) -> str:
    """Format search results as a collapsible sources section."""
    parts = []

    if codex_results:
        parts.append("### Gesetze (Codex)\n")
        for i, res in enumerate(codex_results[:5], 1):
            parts.append(format_law_result(
                res.get('payload', {}),
                res.get('score', 0),
                i
            ))

    if library_results:
        parts.append("\n### Rechtsprechung (Library)\n")
        seen = set()
        rank = 1
        for res in library_results:
            payload = res.get('payload', {})
            decision_id = payload.get('decision_id', '')
            base_id = decision_id.split('_chunk_')[0] if '_chunk_' in str(decision_id) else decision_id
            if base_id not in seen and rank <= 7:
                seen.add(base_id)
                parts.append(format_decision_result(payload, res.get('score', 0), rank))
                rank += 1

    return "\n".join(parts) if parts else "Keine Quellen gefunden."


@cl.on_settings_update
async def setup_agent(settings):
    cl.user_session.set("filters", settings)


@cl.on_message
async def on_message(message: cl.Message):
    global codex_engine, library_engine, pipeline, context_assembler

    settings = cl.user_session.get("filters") or {}
    chat_history = cl.user_session.get("chat_history") or []

    # Build filters
    base_filters = {}
    lang_setting = settings.get("language", "All")
    if lang_setting != "All":
        lang_map = {"German (DE)": "de", "French (FR)": "fr", "Italian (IT)": "it"}
        if lang_setting in lang_map:
            base_filters["language"] = lang_map[lang_setting]

    library_filters = base_filters.copy()
    year_min = settings.get("year_min", 1950)
    year_max = settings.get("year_max", 2026)
    if year_min or year_max:
        library_filters["year_range"] = {"min": int(year_min), "max": int(year_max)}

    codex_filters = base_filters.copy()

    # Prepare response message
    msg = cl.Message(content="")
    await msg.send()

    try:
        # ============================================
        # STAGE 1: Guard & Enhance (Mistral 1)
        # ============================================
        await cl.Message(content="_🛡️ Sicherheitsprüfung und Anfrage-Optimierung..._").send()

        guard_result = pipeline.guard_and_enhance(message.content)

        # Check if blocked
        if guard_result.status == "BLOCKED":
            msg.content = f"""⚠️ **Anfrage blockiert**

{guard_result.block_reason}

Bitte formulieren Sie Ihre Frage um oder kontaktieren Sie den Support."""
            await msg.update()
            return

        # Log enhancement info
        if guard_result.enhanced_query != guard_result.original_query:
            await cl.Message(
                content=f"_Enhanced: {guard_result.enhanced_query[:100]}..._",
                author="system"
            ).send()

        detected_language = guard_result.detected_language
        enhanced_query = guard_result.enhanced_query
        legal_concepts = guard_result.legal_concepts

        # ============================================
        # STAGE 2: Search
        # ============================================
        search_scope = settings.get("search_scope", "Both")
        multilingual = settings.get("multilingual", False)
        show_sources = settings.get("show_sources", True)

        codex_results = []
        library_results = []

        await cl.Message(content="_🔍 Durchsuche Rechtsdatenbank..._").send()

        if search_scope in ["Both", "Laws (Codex)"]:
            try:
                codex_results = codex_engine.search(
                    enhanced_query,
                    limit=5,
                    filters=codex_filters if codex_filters else None,
                    multilingual=multilingual
                )
            except Exception as e:
                await cl.Message(content=f"_Codex search error: {str(e)}_").send()

        if search_scope in ["Both", "Decisions (Library)"]:
            try:
                library_results = library_engine.search(
                    enhanced_query,
                    limit=7,
                    filters=library_filters if library_filters else None,
                    multilingual=multilingual
                )
            except Exception as e:
                await cl.Message(content=f"_Library search error: {str(e)}_").send()

        # Check if we found anything
        if not codex_results and not library_results:
            msg.content = """Keine relevanten Rechtsquellen gefunden.

Bitte versuchen Sie:
- Eine andere Formulierung
- Allgemeinere Begriffe
- Den Cross-Language Modus zu aktivieren"""
            await msg.update()
            return

        # Show sources (if enabled)
        if show_sources:
            sources_text = format_sources_collapsible(codex_results, library_results)
            sources_msg = cl.Message(content=f"**📚 Gefundene Quellen:**\n\n{sources_text}")
            await sources_msg.send()

        # ============================================
        # STAGE 3: Reformulate (Mistral 2)
        # ============================================
        await cl.Message(content="_📝 Strukturiere Anfrage..._").send()

        # Extract topics from legal concepts
        topics = legal_concepts if legal_concepts else ["general legal question"]

        reformulated_query, reformulate_response = pipeline.reformulate(
            original_query=message.content,
            enhanced_query=enhanced_query,
            language=detected_language,
            law_count=len(codex_results),
            decision_count=len(library_results),
            topics=topics,
        )

        # ============================================
        # STAGE 4: Build Context
        # ============================================
        await cl.Message(content="_📄 Lade vollständige Dokumente..._").send()

        laws_context, decisions_context, context_meta = pipeline.build_context(
            codex_results=codex_results,
            library_results=library_results,
            max_laws=5,
            max_decisions=7,
        )

        # ============================================
        # STAGE 5: Legal Analysis (Qwen)
        # ============================================
        msg.content = ""
        await msg.update()

        await cl.Message(content="_⚖️ Generiere rechtliche Analyse..._").send()

        # Stream the response
        full_response = []
        try:
            stream_gen = pipeline.analyze(
                reformulated_query=reformulated_query,
                laws_context=laws_context,
                decisions_context=decisions_context,
                language=detected_language,
            )

            for chunk in stream_gen:
                full_response.append(chunk)
                msg.content = "".join(full_response)
                await msg.stream_token(chunk)

            # Get final response metadata
            try:
                final_response = stream_gen.send(None)
            except StopIteration as e:
                final_response = e.value

        except Exception as e:
            msg.content = f"""**Fehler bei der Analyse:** {str(e)}

Die Suche war erfolgreich. Bitte versuchen Sie es erneut."""
            await msg.update()
            return

        # Finalize message
        await msg.update()

        # Parse consistency from response
        response_text = "".join(full_response)
        consistency, confidence = pipeline.parse_consistency(response_text)

        # Show consistency indicator
        indicator = get_consistency_indicator(consistency, confidence)
        await cl.Message(
            content=f"**{indicator}**",
            author="system"
        ).send()

        # Show token usage (if available)
        if final_response:
            total_tokens = final_response.total_tokens
            cost = final_response.cost_chf

            # Add costs from other stages
            guard_cost = guard_result.response.cost_chf if guard_result.response else 0
            reformulate_cost = reformulate_response.cost_chf if reformulate_response else 0
            total_cost = cost + guard_cost + reformulate_cost

            await cl.Message(
                content=f"_Tokens: {total_tokens} | Kosten: CHF {total_cost:.4f}_",
                author="system"
            ).send()

        # Update chat history
        chat_history.append({"role": "user", "content": message.content})
        chat_history.append({"role": "assistant", "content": response_text})
        cl.user_session.set("chat_history", chat_history[-10:])  # Keep last 5 turns

    except Exception as e:
        msg.content = f"**Error:** {str(e)}"
        await msg.update()
