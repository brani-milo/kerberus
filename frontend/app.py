"""
KERBERUS Chainlit Frontend - Unified App

Sovereign AI Legal Assistant for Swiss Law

Two Modes:
1. AI Legal Assistant: Query laws, decisions, get legal analysis (4-stage pipeline)
2. Tabular Review: Upload docs, extract table, chat with data

Switch modes using the action buttons.
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Optional

import chainlit as cl
from chainlit.input_widget import Select, Switch, Slider

# Add project root to Python path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.search.triad_search import TriadSearch
from src.llm import get_pipeline, ContextAssembler

# Review imports
from src.review import (
    list_presets,
    get_preset,
    DocumentProcessor,
    SchemaExtractor,
    ReviewManager,
    ExcelExporter,
    ReviewChatHandler
)
from src.review.review_manager import Review

logger = logging.getLogger(__name__)

# Global instances (initialized lazily)
triad_search = None
pipeline = None
context_assembler = None

# Review components
doc_processor = None
review_manager = None
excel_exporter = None

MAX_DOCUMENTS = 30


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_consistency_indicator(consistency: str, confidence: str) -> str:
    """Generate traffic light indicator for response consistency."""
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


def get_search_confidence_indicator(confidence: str) -> str:
    """Map search confidence to emoji."""
    return {
        "HIGH": "🟢",
        "MEDIUM": "🟡",
        "LOW": "🔴",
        "NONE": "⚪",
    }.get(confidence, "⚪")


def format_law_result(result: dict, rank: int) -> str:
    """Format a law article result."""
    payload = result.get('payload', {})
    abbrev = payload.get('abbreviation', 'SR')
    art_num = payload.get('article_number', '?')
    art_title = payload.get('article_title', '')
    sr_num = payload.get('sr_number', '')
    lang = payload.get('language', '').upper()
    final_score = result.get('final_score', result.get('score', 0))

    citation = f"{abbrev} Art. {art_num}"
    if art_title:
        citation += f" - {art_title}"

    text = payload.get('article_text', payload.get('text_preview', ''))
    preview = text[:300] + "..." if len(text) > 300 else text

    return f"""**{rank}. {citation}**
SR {sr_num} • `{lang}` • Score: {final_score:.2f}

> {preview}
"""


def format_decision_result(result: dict, rank: int) -> str:
    """Format a court decision result."""
    payload = result.get('payload', {})
    decision_id = payload.get('decision_id', '')
    case_id = payload.get('_original_id', payload.get('id', 'Unknown'))
    final_score = result.get('final_score', result.get('score', 0))
    year = result.get('year', payload.get('year', ''))

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

    court = payload.get('court', '')
    court_names = {
        'CH_BGE': 'BGer', 'CH_BGer': 'BGer', 'CH_BVGer': 'BVGer',
        'CH_BStGer': 'BStGer', 'CH_TI': 'TI',
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
    meta += f" • Score: {final_score:.2f}"

    return f"""**{rank}. {citation}**
{meta}

> {preview}
"""


def format_sources_collapsible(codex_results: list, library_results: list, codex_conf: str, library_conf: str) -> str:
    """Format search results as a collapsible sources section."""
    parts = []
    codex_emoji = get_search_confidence_indicator(codex_conf)
    library_emoji = get_search_confidence_indicator(library_conf)

    if codex_results:
        parts.append(f"**Laws (Codex)**\n")
        for i, res in enumerate(codex_results[:10], 1):
            parts.append(format_law_result(res, i))

    if library_results:
        parts.append(f"\n**{library_emoji} Case Law (Library) - {library_conf}**\n")
        seen = set()
        rank = 1
        for res in library_results:
            payload = res.get('payload', {})
            decision_id = payload.get('decision_id', '')
            base_id = decision_id.split('_chunk_')[0] if '_chunk_' in str(decision_id) else decision_id
            if base_id not in seen and rank <= 15:
                seen.add(base_id)
                parts.append(format_decision_result(res, rank))
                rank += 1

    return "\n".join(parts) if parts else "No sources found."


def get_mode_buttons(current_mode: str = None) -> List[cl.Action]:
    """Get mode switching buttons."""
    buttons = []
    if current_mode != "assistant":
        buttons.append(cl.Action(
            name="mode_assistant",
            payload={"mode": "assistant"},
            label="⚖️ AI Legal Assistant"
        ))
    if current_mode != "review":
        buttons.append(cl.Action(
            name="mode_review",
            payload={"mode": "review"},
            label="📊 Tabular Review"
        ))
    return buttons


# =============================================================================
# CHAT START
# =============================================================================

@cl.on_chat_start
async def on_chat_start():
    global triad_search, pipeline, context_assembler
    global doc_processor, review_manager, excel_exporter

    # Initialize search components lazily
    if triad_search is None:
        triad_search = TriadSearch()
    if pipeline is None:
        pipeline = get_pipeline()
    if context_assembler is None:
        context_assembler = ContextAssembler()

    # Initialize review components
    if doc_processor is None:
        doc_processor = DocumentProcessor()
    if review_manager is None:
        review_manager = ReviewManager(storage_path="data/reviews")
    if excel_exporter is None:
        excel_exporter = ExcelExporter()

    # Initialize session state
    cl.user_session.set("mode", "start")
    cl.user_session.set("chat_history", [])
    cl.user_session.set("current_review", None)
    cl.user_session.set("documents", [])

    # Welcome message with mode buttons
    actions = [
        cl.Action(
            name="mode_assistant",
            payload={"mode": "assistant"},
            label="⚖️ AI Legal Assistant"
        ),
        cl.Action(
            name="mode_review",
            payload={"mode": "review"},
            label="📊 Tabular Review"
        ),
    ]

    await cl.Message(
        content="""# 🛡️ **KERBERUS** - Swiss Legal Intelligence

Welcome! I am your AI legal assistant for Swiss law.

---

## Choose Your Mode:

### ⚖️ AI Legal Assistant
Ask legal questions in German, French, or Italian. Get answers with citations from Swiss laws and court decisions.

### 📊 Tabular Review
Upload up to 30 documents (PDF, DOCX, TXT). AI extracts structured data into a table. Chat with your data and export to Excel.

---

_Click a button below to begin:_""",
        actions=actions
    ).send()

    # Set up settings (for search mode)
    await cl.ChatSettings(
        [
            Select(
                id="search_scope",
                label="Search Scope",
                values=["Both", "Laws (Codex)", "Decisions (Library)"],
                initial_value="Both",
            ),
            Switch(
                id="show_sources",
                label="Show Sources",
                initial=True,
            ),
            Switch(
                id="web_search",
                label="Web Search",
                initial=False,
            ),
            Slider(
                id="year_min",
                label="Min Year (Decisions)",
                min=1900,
                max=2026,
                initial=1950,
                step=1,
            ),
            Slider(
                id="year_max",
                label="Max Year (Decisions)",
                min=1900,
                max=2026,
                initial=2026,
                step=1,
            ),
            Select(
                id="language",
                label="Filter by Language",
                values=["All", "German (DE)", "French (FR)", "Italian (IT)"],
                initial_value="All",
            ),
        ]
    ).send()


@cl.on_settings_update
async def on_settings_update(settings):
    cl.user_session.set("filters", settings)


# =============================================================================
# ACTION CALLBACKS (Button Handlers)
# =============================================================================

@cl.action_callback("mode_assistant")
async def on_action_assistant(action: cl.Action):
    """Handle AI Legal Assistant button click."""
    # Remove the action button after click
    await action.remove()
    await switch_to_assistant_mode()


@cl.action_callback("mode_review")
async def on_action_review(action: cl.Action):
    """Handle Tabular Review button click."""
    # Remove the action button after click
    await action.remove()
    await switch_to_review_mode()


# =============================================================================
# MESSAGE HANDLER
# =============================================================================

@cl.on_message
async def on_message(message: cl.Message):
    mode = cl.user_session.get("mode", "start")
    text = message.content.strip()
    lower_text = text.lower()

    # If still at start, prompt to select mode
    if mode == "start":
        actions = get_mode_buttons()
        await cl.Message(
            content="Please select a mode to begin:",
            actions=actions
        ).send()
        return

    # Handle mode switching commands (still supported as fallback)
    if lower_text in ["/assistant", "/search", "assistant"]:
        await switch_to_assistant_mode()
        return

    if lower_text in ["/review", "review"]:
        await switch_to_review_mode()
        return

    # Handle file uploads (only in review mode)
    if message.elements:
        if mode != "review":
            actions = [cl.Action(name="mode_review", payload={"mode": "review"}, label="📊 Switch to Tabular Review")]
            await cl.Message(
                content="📎 File uploads are only available in **Tabular Review** mode.",
                actions=actions
            ).send()
        else:
            await handle_file_uploads(message.elements)
        return

    # Route to appropriate handler based on mode
    if mode == "assistant":
        await handle_assistant_message(message)
    elif mode == "review":
        await handle_review_message(message)
    else:
        actions = get_mode_buttons()
        await cl.Message(content="Please select a mode:", actions=actions).send()


# =============================================================================
# MODE SWITCHING
# =============================================================================

async def switch_to_assistant_mode():
    cl.user_session.set("mode", "assistant")

    actions = [cl.Action(name="mode_review", payload={"mode": "review"}, label="📊 Switch to Tabular Review")]

    await cl.Message(
        content="""# ⚖️ AI Legal Assistant

Ask your legal questions in German, French, or Italian.

**Examples:**
- "What are the requirements for divorce?"
- "Quels sont les délais de prescription en droit suisse?"
- "Quali sono i diritti del lavoratore in caso di licenziamento?"

_Answers are backed by citations from Swiss laws and court decisions._""",
        actions=actions
    ).send()


async def switch_to_review_mode():
    cl.user_session.set("mode", "review")
    cl.user_session.set("documents", [])
    cl.user_session.set("current_review", None)

    presets = list_presets()

    actions = [cl.Action(name="mode_assistant", payload={"mode": "assistant"}, label="⚖️ Switch to AI Legal Assistant")]

    msg = """# 📊 Tabular Review

Select a preset for your document review:

"""
    for i, preset in enumerate(presets, 1):
        msg += f"**{i}.** {preset['icon']} **{preset['name']}** ({preset['field_count']} fields)\n"
        msg += f"   _{preset['description']}_\n"
        # Add note for Court Case Summary
        if preset['id'] == 'court_case_summary':
            msg += f"   💡 _You can also upload your own case files for comparison_\n"
        msg += "\n"

    msg += """---
Type the **number** or **name** of the preset to start (e.g., "1" or "Contract Review")"""

    await cl.Message(content=msg, actions=actions).send()


# =============================================================================
# AI LEGAL ASSISTANT HANDLER
# =============================================================================

async def handle_assistant_message(message: cl.Message):
    global triad_search, pipeline

    settings = cl.user_session.get("filters") or {}
    chat_history = cl.user_session.get("chat_history") or []

    # Build filters
    filters = {}
    lang_setting = settings.get("language", "All")
    if lang_setting != "All":
        lang_map = {"German (DE)": "de", "French (FR)": "fr", "Italian (IT)": "it"}
        if lang_setting in lang_map:
            filters["language"] = lang_map[lang_setting]

    year_min = settings.get("year_min", 1950)
    year_max = settings.get("year_max", 2026)
    if year_min or year_max:
        filters["year_range"] = {"min": int(year_min), "max": int(year_max)}

    msg = cl.Message(content="")
    await msg.send()

    try:
        # Create a status message that we'll update
        status_msg = cl.Message(content="_🛡️ Security check and query optimization..._")
        await status_msg.send()

        # STAGE 1: Guard & Enhance
        try:
            guard_result = pipeline.guard_and_enhance(message.content)
            
            if guard_result.status == "BLOCKED":
                msg.content = f"""⚠️ **Request blocked**

{guard_result.block_reason}

Please rephrase your question or contact support."""
                await msg.update()
                # Remove status message if blocked
                await status_msg.remove()
                return

            if guard_result.enhanced_query != guard_result.original_query:
                # We can show this in the sidebar or just log it
                pass

            detected_language = guard_result.detected_language
            enhanced_query = guard_result.enhanced_query
            legal_concepts = guard_result.legal_concepts
            
        except Exception as guard_error:
            logger.warning(f"Guard stage failed: {guard_error}")
            detected_language = "de"
            enhanced_query = message.content
            legal_concepts = []

        # STAGE 2: TriadSearch
        status_msg.content = "_🔍 Hybrid-Search + MMR + Reranking..._"
        await status_msg.update()
        
        search_scope = settings.get("search_scope", "Both")
        show_sources = settings.get("show_sources", True)
        
        search_results = await triad_search.search(
            query=enhanced_query,
            user_id=None,
            firm_id=None,
            filters=filters if filters else None,
            top_k=50
        )

        codex_results = []
        library_results = []
        codex_conf = "NONE"
        library_conf = "NONE"
        
        if search_scope in ["Both", "Laws (Codex)"]:
            codex_data = search_results.get('codex', {})
            codex_results = codex_data.get('results', [])
            codex_conf = codex_data.get('confidence', 'NONE')

        if search_scope in ["Both", "Decisions (Library)"]:
            library_data = search_results.get('library', {})
            library_results = library_data.get('results', [])
            library_conf = library_data.get('confidence', 'NONE')

        if not codex_results and not library_results:
            msg.content = """No relevant legal sources found.

Please try:
- Different wording
- More general terms"""
            await msg.update()
            await status_msg.remove()
            return

        # Prepare sources but don't send yet
        sources_element = None
        if show_sources:
            sources_text = format_sources_collapsible(codex_results, library_results, codex_conf, library_conf)
            sources_element = cl.Text(name="📚 Legal Sources", content=sources_text, display="side")

        # STAGE 3: Reformulate
        status_msg.content = "_📝 Structuring request..._"
        await status_msg.update()
        
        topics = legal_concepts if legal_concepts else ["general legal question"]

        reformulated_query, reformulate_response = pipeline.reformulate(
            original_query=message.content,
            enhanced_query=enhanced_query,
            language=detected_language,
            law_count=len(codex_results),
            decision_count=len(library_results),
            topics=topics,
        )

        # STAGE 4: Build Context
        status_msg.content = "_📄 Loading full documents..._"
        await status_msg.update()

        codex_for_context = [
            {"id": r.get("id"), "score": r.get("final_score", r.get("score", 0)), "payload": r.get("payload", {})}
            for r in codex_results
        ]
        library_for_context = [
            {"id": r.get("id"), "score": r.get("final_score", r.get("score", 0)), "payload": r.get("payload", {})}
            for r in library_results
        ]

        laws_context, decisions_context, context_meta = pipeline.build_context(
            codex_results=codex_for_context,
            library_results=library_for_context,
            max_laws=10,
            max_decisions=15,
        )

        # STAGE 5: Legal Analysis
        status_msg.content = "_⚖️ Generating legal analysis..._"
        await status_msg.update()
        
        web_search_enabled = settings.get("web_search", False)

        # Remove status message before streaming answer
        await status_msg.remove()

        # Send sources as a clean, collapsible message (no side element, no code block)
        if show_sources and (codex_results or library_results):
            sources_text = format_sources_collapsible(codex_results, library_results, codex_conf, library_conf)
            
            # Use HTML details tag for clean native folding without "code block" styling
            content = f"""<details>
<summary>📚 **Legal Sources** (Click to expand)</summary>

{sources_text}
</details>"""
            
            await cl.Message(content=content, author="system").send()

        # Update the main message to start streaming
        msg.content = ""
        await msg.update()

        full_response = []
        try:
            stream_gen = pipeline.analyze(
                reformulated_query=reformulated_query,
                laws_context=laws_context,
                decisions_context=decisions_context,
                language=detected_language,
                web_search=web_search_enabled,
            )

            for chunk in stream_gen:
                full_response.append(chunk)
                msg.content = "".join(full_response)
                await msg.stream_token(chunk)

            try:
                final_response = stream_gen.send(None)
            except StopIteration as e:
                final_response = e.value

        except Exception as e:
            msg.content = f"""**Analysis Error:** {str(e)}

The search was successful. Please try again."""
            await msg.update()
            return

        if final_response:
            total_tokens = final_response.total_tokens
            cost = final_response.cost_chf
            guard_cost = guard_result.response.cost_chf if guard_result.response else 0
            reformulate_cost = reformulate_response.cost_chf if reformulate_response else 0
            total_cost = cost + guard_cost + reformulate_cost
            await cl.Message(
                content=f"_Tokens: {total_tokens} | Kosten: CHF {total_cost:.4f}_",
                author="system"
            ).send()

        chat_history.append({"role": "user", "content": message.content})
        chat_history.append({"role": "assistant", "content": response_text})
        cl.user_session.set("chat_history", chat_history[-10:])

    except Exception as e:
        msg.content = f"**Error:** {str(e)}"
        await msg.update()


# =============================================================================
# TABULAR REVIEW HANDLERS
# =============================================================================

async def handle_review_message(message: cl.Message):
    """Handle messages in review mode."""
    text = message.content.strip()
    lower_text = text.lower()
    review = cl.user_session.get("current_review")

    # Check if selecting preset
    if not review:
        await handle_preset_selection(text)
        return

    # Check for commands
    if lower_text in ["extract", "start extraction", "/extract"]:
        await start_extraction()
        return

    if lower_text in ["export", "download excel", "/export"]:
        await export_excel()
        return

    if lower_text in ["show table", "table", "/table"]:
        await show_table()
        return

    if lower_text in ["help", "/help"]:
        await show_review_help()
        return

    if lower_text.startswith("citation "):
        parts = text.split()
        if len(parts) >= 3:
            await show_citation(parts[1], parts[2])
        return

    # If review is complete, treat as chat question
    if review.status == "completed":
        await handle_review_chat(text, review)
        return

    # Default guidance
    docs = cl.user_session.get("documents", [])
    await cl.Message(
        content=f"""📎 Upload documents or type a command.

**Current status:**
- Documents uploaded: {len(docs)}/{MAX_DOCUMENTS}
- Review: {review.name}

**Commands:**
- Upload files using the 📎 button
- Type **"extract"** to start extraction"""
    ).send()


async def handle_preset_selection(text: str):
    """Handle preset selection in review mode."""
    presets = list_presets()
    preset_id = None

    # Try to match by number
    try:
        num = int(text)
        if 1 <= num <= len(presets):
            preset_id = presets[num - 1]["id"]
    except ValueError:
        pass

    # Try to match by name
    if not preset_id:
        for preset in presets:
            if text.lower() in preset["name"].lower():
                preset_id = preset["id"]
                break

    if not preset_id:
        await cl.Message(
            content=f"❌ Unknown preset: '{text}'\n\nType a number (1-{len(presets)}) or preset name."
        ).send()
        return

    # Get review name
    res = await cl.AskUserMessage(
        content="What would you like to name this review? (e.g., 'Q1 2026 Contract Review')",
        timeout=300
    ).send()

    if res:
        review_name = res.get("output", "Untitled Review")
    else:
        review_name = "Untitled Review"

    # Create review
    user_id = cl.user_session.get("user", {}).get("id", "demo_user")
    review = review_manager.create_review(
        user_id=user_id,
        name=review_name,
        preset_id=preset_id
    )

    cl.user_session.set("current_review", review)
    preset = get_preset(preset_id)

    await cl.Message(
        content=f"""✅ Created review: **{review_name}**
📋 Preset: {preset.icon} {preset.name} ({len(preset.fields)} fields)

Now upload your documents (PDF, DOCX, or TXT).
Maximum: {MAX_DOCUMENTS} files per review.

_Tip: You can drag and drop multiple files at once._"""
    ).send()


async def handle_file_uploads(elements: List):
    """Process uploaded files in review mode."""
    review = cl.user_session.get("current_review")

    if not review:
        await cl.Message(
            content="❌ No active review. Please select a preset first."
        ).send()
        return

    documents = cl.user_session.get("documents", [])
    remaining = MAX_DOCUMENTS - len(documents)

    if remaining <= 0:
        await cl.Message(
            content=f"❌ Maximum {MAX_DOCUMENTS} documents per review. Type 'extract' to start."
        ).send()
        return

    new_docs = []
    errors = []

    for element in elements[:remaining]:
        if hasattr(element, 'path') and element.path:
            try:
                parsed = doc_processor.parse_file(element.path)
                parsed.filename = element.name
                new_docs.append(parsed)
            except Exception as e:
                errors.append(f"❌ {element.name}: {str(e)}")

    documents.extend(new_docs)
    cl.user_session.set("documents", documents)

    if new_docs:
        msg = f"✅ Added {len(new_docs)} document(s):\n"
        for doc in new_docs:
            msg += f"- 📄 {doc.filename} ({doc.total_pages} pages)\n"
        msg += f"\n**Total documents:** {len(documents)}/{MAX_DOCUMENTS}"

        if len(documents) >= 1:
            msg += "\n\n_Type **'extract'** when ready to start extraction._"

        await cl.Message(content=msg).send()

    if errors:
        await cl.Message(content="\n".join(errors)).send()


async def start_extraction():
    """Start the extraction process."""
    review = cl.user_session.get("current_review")
    documents = cl.user_session.get("documents", [])

    if not review:
        await cl.Message(content="❌ No active review.").send()
        return

    if not documents:
        await cl.Message(content="❌ No documents uploaded. Please upload at least one document.").send()
        return

    if not os.environ.get("QWEN_API_KEY"):
        await cl.Message(
            content="❌ QWEN_API_KEY not set. Please set the environment variable."
        ).send()
        return

    try:
        extractor = SchemaExtractor()
    except Exception as e:
        await cl.Message(content=f"❌ Failed to initialize extractor: {e}").send()
        return

    progress_msg = await cl.Message(
        content=f"🔄 Extracting from {len(documents)} documents...\n\nThis may take a few minutes."
    ).send()

    preset_id = review.preset_id
    processed = 0
    errors = []

    for doc in documents:
        try:
            extraction = await extractor.extract_document(doc, preset_id)

            if extraction.extraction_errors:
                errors.extend(extraction.extraction_errors)
            else:
                review_manager.add_extraction(review.review_id, extraction)

            processed += 1
            progress_text = f"🔄 Processing... ({processed}/{len(documents)})\n"
            progress_text += f"✅ {doc.filename}"
            await progress_msg.update(content=progress_text)

        except Exception as e:
            errors.append(f"{doc.filename}: {str(e)}")
            logger.error(f"Extraction error for {doc.filename}: {e}")

    review_manager.complete_review(review.review_id)
    review = review_manager.get_review(review.review_id)
    cl.user_session.set("current_review", review)

    result_msg = f"""# ✅ Extraction Complete

**{processed} documents** processed successfully.
**{len(review.rows)} rows** in your review table.
"""

    if errors:
        result_msg += f"\n⚠️ **{len(errors)} errors:**\n"
        for err in errors[:5]:
            result_msg += f"- {err}\n"

    result_msg += """
---
## What's next?

- Type **"show table"** to see the extracted data
- **Ask questions** about your data (e.g., "What are the riskiest contracts?")
- Type **"export"** to download as Excel
"""

    await cl.Message(content=result_msg).send()
    await show_table()


async def show_table():
    """Display the review table."""
    review = cl.user_session.get("current_review")

    if not review or not review.rows:
        await cl.Message(content="❌ No data to display.").send()
        return

    preset = get_preset(review.preset_id)
    display_fields = [f for f in preset.fields[:8]]

    # Build markdown table
    table = "| # | Document |"
    for f in display_fields:
        if f.name != "document_name":
            table += f" {f.display_name} |"
    table += "\n|---|---|"
    for f in display_fields:
        if f.name != "document_name":
            table += "---|"
    table += "\n"

    for idx, row in enumerate(review.rows, start=1):
        table += f"| {idx} | {row.filename[:25]}{'...' if len(row.filename) > 25 else ''} |"

        for f in display_fields:
            if f.name != "document_name":
                field_data = row.fields.get(f.name, {})
                value = field_data.get("value")

                if value is None:
                    display = "-"
                elif isinstance(value, bool):
                    display = "✓" if value else "✗"
                else:
                    display = str(value)[:20]
                    if len(str(value)) > 20:
                        display += "..."

                if field_data.get("citation"):
                    display += " ⓘ"

                table += f" {display} |"
        table += "\n"

    msg = f"""## 📊 Review Table: {review.name}

{table}

_Showing first 8 columns. Type **"export"** for full data with all {len(preset.fields)} fields._

**To see a citation**: `citation [row#] [field_name]`
**To ask questions**: Just type your question!
"""

    await cl.Message(content=msg).send()


async def show_citation(row_num: str, field_name: str):
    """Show citation for a specific field."""
    review = cl.user_session.get("current_review")

    if not review:
        await cl.Message(content="❌ No active review.").send()
        return

    try:
        idx = int(row_num) - 1
        if idx < 0 or idx >= len(review.rows):
            raise ValueError("Invalid row")
    except ValueError:
        await cl.Message(content=f"❌ Invalid row number: {row_num}").send()
        return

    row = review.rows[idx]
    field_data = row.fields.get(field_name, {})

    if not field_data:
        await cl.Message(content=f"❌ Field '{field_name}' not found.").send()
        return

    citation = field_data.get("citation")

    if not citation:
        await cl.Message(
            content=f"ℹ️ No citation for **{field_name}** in document #{row_num}.\n\nValue: {field_data.get('value')}"
        ).send()
        return

    msg = f"""## 📎 Citation

**Document:** {row.filename}
**Field:** {field_name}
**Value:** {field_data.get('value')}

---

**Page:** {citation.get('page', 'N/A')}
**Section:** {citation.get('section', 'N/A')}

> {citation.get('quote', 'No quote available')}
"""

    await cl.Message(content=msg).send()


async def export_excel():
    """Export review to Excel."""
    review = cl.user_session.get("current_review")

    if not review or not review.rows:
        await cl.Message(content="❌ No data to export.").send()
        return

    try:
        excel_bytes = excel_exporter.export_review(review)
        filename = excel_exporter.get_filename(review)

        await cl.Message(
            content=f"📥 **Excel Export Ready**\n\n- {len(review.rows)} rows\n- Citations sheet included\n- Summary sheet included",
            elements=[
                cl.File(
                    name=filename,
                    content=excel_bytes.getvalue(),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            ]
        ).send()

    except Exception as e:
        await cl.Message(content=f"❌ Export failed: {e}").send()


async def handle_review_chat(question: str, review: Review):
    """Handle chat questions about review data."""
    try:
        chat_handler = ReviewChatHandler()
    except Exception as e:
        await cl.Message(content=f"❌ Chat unavailable: {e}").send()
        return

    msg = cl.Message(content="🤔 Analyzing...")
    await msg.send()

    try:
        response = await chat_handler.ask(review, question)
        answer = response.answer

        if response.citations:
            answer += "\n\n---\n**📄 Documents Referenced:**\n"
            for cite in response.citations:
                answer += f"- Document {cite['document_number']}: {cite['filename']}\n"

        if response.suggested_followups:
            answer += "\n\n---\n**💡 You might also ask:**\n"
            for followup in response.suggested_followups:
                answer += f"- {followup}\n"

        await msg.update(content=answer)
        cl.user_session.set("current_review", review)

    except Exception as e:
        await msg.update(content=f"❌ Error: {e}")


async def show_review_help():
    """Show review mode help."""
    actions = [cl.Action(name="mode_assistant", payload={"mode": "assistant"}, label="⚖️ Switch to AI Legal Assistant")]
    
    help_text = """## 📊 Tabular Review Commands

| Command | Description |
|---------|-------------|
| **extract** | Start extraction from uploaded documents |
| **show table** | Display the extracted data table |
| **export** | Download review as Excel file |
| **citation [row] [field]** | Show citation for a field |

## 💬 Chat Examples

Once extraction is complete, ask questions like:
- "What are the 3 most valuable contracts?"
- "Which documents mention change of control?"
- "What is the total contract value?"
"""

    await cl.Message(content=help_text, actions=actions).send()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    from chainlit.cli import run_chainlit
    run_chainlit(__file__)
