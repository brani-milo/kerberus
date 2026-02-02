"""
KERBERUS Tabular Review - Chainlit Frontend.

Provides a web UI for:
- Uploading documents (max 30)
- Selecting review preset
- Viewing extracted data as a table
- Chatting with the review data
- Exporting to Excel
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Optional

import chainlit as cl
from chainlit.input_widget import Select, TextInput

# Add project root to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

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

# Initialize components
doc_processor = DocumentProcessor()
review_manager = ReviewManager(storage_path="data/reviews")
excel_exporter = ExcelExporter()

# Max documents per review
MAX_DOCUMENTS = 30


# =============================================================================
# CHAINLIT LIFECYCLE
# =============================================================================

@cl.on_chat_start
async def on_start():
    """Initialize the review session."""
    
    # Get available presets
    presets = list_presets()
    preset_options = {f"{p['icon']} {p['name']}": p['id'] for p in presets}
    
    # Store in session
    cl.user_session.set("preset_options", preset_options)
    cl.user_session.set("current_review", None)
    cl.user_session.set("documents", [])
    cl.user_session.set("mode", "setup")  # setup, reviewing, chatting
    
    # Welcome message
    welcome = """# 📊 KERBERUS Tabular Review

Welcome to the document review system. I can help you extract structured information from multiple documents.

## How it works:
1. **Select a preset** - Choose the type of review (Contract, Due Diligence, etc.)
2. **Upload documents** - Up to 30 PDF, DOCX, or TXT files
3. **Review the table** - I'll extract key fields with citations
4. **Ask questions** - Chat with your data for insights

## Available Presets:
"""
    
    for preset in presets:
        welcome += f"- {preset['icon']} **{preset['name']}** ({preset['field_count']} fields)\n  _{preset['description']}_\n"
    
    welcome += "\n**To begin, select a preset below:**"
    
    await cl.Message(content=welcome).send()
    
    # Preset selection
    settings = await cl.ChatSettings([
        Select(
            id="preset",
            label="Review Preset",
            values=list(preset_options.keys()),
            initial_value=list(preset_options.keys())[0]
        )
    ]).send()
    
    # Ask for review name
    res = await cl.AskUserMessage(
        content="What would you like to name this review? (e.g., 'Q1 2026 Contract Review')",
        timeout=300
    ).send()
    
    if res:
        review_name = res.get("output", "Untitled Review")
        preset_display = list(preset_options.keys())[0]
        preset_id = preset_options[preset_display]
        
        # Create review
        user_id = cl.user_session.get("user", {}).get("id", "demo_user")
        review = review_manager.create_review(
            user_id=user_id,
            name=review_name,
            preset_id=preset_id
        )
        
        cl.user_session.set("current_review", review)
        cl.user_session.set("mode", "uploading")
        
        preset = get_preset(preset_id)
        
        await cl.Message(
            content=f"""✅ Created review: **{review_name}**
📋 Preset: {preset.icon} {preset.name}

Now upload your documents (PDF, DOCX, or TXT).
Maximum: {MAX_DOCUMENTS} files per review.

_Tip: You can drag and drop multiple files at once._"""
        ).send()


@cl.on_settings_update
async def on_settings_update(settings):
    """Handle preset selection changes."""
    preset_options = cl.user_session.get("preset_options", {})
    
    if "preset" in settings:
        preset_display = settings["preset"]
        preset_id = preset_options.get(preset_display)
        
        if preset_id:
            preset = get_preset(preset_id)
            await cl.Message(
                content=f"📋 Selected preset: **{preset.name}**\n\n_{preset.description}_"
            ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """Handle user messages and file uploads."""
    
    mode = cl.user_session.get("mode", "setup")
    review = cl.user_session.get("current_review")
    
    # Handle file uploads
    if message.elements:
        await handle_file_uploads(message.elements, review)
        return
    
    # Check for commands
    text = message.content.strip().lower()
    
    if text == "/extract" or text == "extract" or text == "start extraction":
        await start_extraction()
        return
    
    if text == "/export" or text == "export" or text == "download excel":
        await export_excel()
        return
    
    if text == "/table" or text == "show table":
        await show_table()
        return
    
    if text == "/help" or text == "help":
        await show_help()
        return
    
    if text == "/citation" or text.startswith("citation "):
        parts = message.content.split()
        if len(parts) >= 3:
            await show_citation(parts[1], parts[2])
        return
    
    # If we're in chat mode and have a review, treat as a question
    if mode == "chatting" and review:
        await handle_chat_question(message.content, review)
        return
    
    # Default: guide user
    if mode == "uploading":
        await cl.Message(
            content="""📎 Upload documents by clicking the attachment button or dragging files.

When ready, type **"extract"** to start the extraction process."""
        ).send()
    else:
        await show_help()


async def handle_file_uploads(elements: List, review: Optional[Review]):
    """Process uploaded files."""
    
    if not review:
        await cl.Message(
            content="❌ No active review. Please start a new review first."
        ).send()
        return
    
    documents = cl.user_session.get("documents", [])
    
    # Check document limit
    remaining = MAX_DOCUMENTS - len(documents)
    if remaining <= 0:
        await cl.Message(
            content=f"❌ Maximum {MAX_DOCUMENTS} documents per review. Please start extraction or create a new review."
        ).send()
        return
    
    new_docs = []
    errors = []
    
    for element in elements[:remaining]:
        if hasattr(element, 'path') and element.path:
            try:
                # Parse document
                parsed = doc_processor.parse_file(element.path)
                parsed.filename = element.name  # Use original name
                new_docs.append(parsed)
                
            except Exception as e:
                errors.append(f"❌ {element.name}: {str(e)}")
    
    # Update session
    documents.extend(new_docs)
    cl.user_session.set("documents", documents)
    
    # Report results
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
    
    # Check for API key
    if not os.environ.get("QWEN_API_KEY"):
        await cl.Message(
            content="❌ QWEN_API_KEY not set. Please set the environment variable."
        ).send()
        return
    
    # Initialize extractor
    try:
        extractor = SchemaExtractor()
    except Exception as e:
        await cl.Message(content=f"❌ Failed to initialize extractor: {e}").send()
        return
    
    # Progress message
    progress_msg = await cl.Message(
        content=f"🔄 Extracting from {len(documents)} documents...\n\nThis may take a few minutes."
    ).send()
    
    # Process each document
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
            
            # Update progress
            progress_text = f"🔄 Processing... ({processed}/{len(documents)})\n"
            progress_text += f"✅ {doc.filename}"
            await progress_msg.update(content=progress_text)
            
        except Exception as e:
            errors.append(f"{doc.filename}: {str(e)}")
            logger.error(f"Extraction error for {doc.filename}: {e}")
    
    # Complete review
    review_manager.complete_review(review.review_id)
    
    # Refresh review from storage
    review = review_manager.get_review(review.review_id)
    cl.user_session.set("current_review", review)
    cl.user_session.set("mode", "chatting")
    
    # Show results
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
- Click ⓘ icons to see citations for any field
"""
    
    await cl.Message(content=result_msg).send()
    
    # Show table preview
    await show_table()


async def show_table():
    """Display the review table."""
    
    review = cl.user_session.get("current_review")
    
    if not review or not review.rows:
        await cl.Message(content="❌ No data to display. Upload and extract documents first.").send()
        return
    
    preset = get_preset(review.preset_id)
    
    # Build markdown table (limited columns for display)
    # Show first 8 columns plus document name
    display_fields = [f for f in preset.fields[:8]]
    
    # Header
    table = "| # | Document |"
    for f in display_fields:
        if f.name != "document_name":
            table += f" {f.display_name} |"
    table += "\n"
    
    # Separator
    table += "|---|---|"
    for f in display_fields:
        if f.name != "document_name":
            table += "---|"
    table += "\n"
    
    # Rows
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
                
                # Add citation indicator
                if field_data.get("citation"):
                    display += " ⓘ"
                
                table += f" {display} |"
        
        table += "\n"
    
    msg = f"""## 📊 Review Table: {review.name}

{table}

_Showing first 8 columns. Type **"export"** for full data with all {len(preset.fields)} fields._

**To see a citation**, type: `citation [row#] [field_name]`
Example: `citation 1 contract_value`

**To ask questions**, just type your question!
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
            raise ValueError("Invalid row number")
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
            content=f"ℹ️ No citation available for **{field_name}** in document #{row_num}.\n\nValue: {field_data.get('value')}"
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
        # Generate Excel
        excel_bytes = excel_exporter.export_review(review)
        filename = excel_exporter.get_filename(review)
        
        # Send as file
        await cl.Message(
            content=f"📥 **Excel Export Ready**\n\nYour review has been exported with:\n- Main data sheet ({len(review.rows)} rows)\n- Citations sheet\n- Summary sheet",
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


async def handle_chat_question(question: str, review: Review):
    """Handle a chat question about the review data."""
    
    try:
        chat_handler = ReviewChatHandler()
    except Exception as e:
        await cl.Message(content=f"❌ Chat unavailable: {e}").send()
        return
    
    # Show thinking indicator
    msg = cl.Message(content="🤔 Analyzing...")
    await msg.send()
    
    try:
        response = await chat_handler.ask(review, question)
        
        # Update with response
        answer = response.answer
        
        # Add citations if any
        if response.citations:
            answer += "\n\n---\n**📄 Documents Referenced:**\n"
            for cite in response.citations:
                answer += f"- Document {cite['document_number']}: {cite['filename']}\n"
        
        # Add follow-up suggestions
        if response.suggested_followups:
            answer += "\n\n---\n**💡 You might also ask:**\n"
            for followup in response.suggested_followups:
                answer += f"- {followup}\n"
        
        await msg.update(content=answer)
        
        # Save updated review
        cl.user_session.set("current_review", review)
        
    except Exception as e:
        await msg.update(content=f"❌ Error: {e}")


async def show_help():
    """Show help message."""
    
    help_text = """## 📚 Commands

| Command | Description |
|---------|-------------|
| **extract** | Start extraction from uploaded documents |
| **show table** | Display the extracted data table |
| **export** | Download review as Excel file |
| **citation [row] [field]** | Show citation for a field |
| **help** | Show this help message |

## 💬 Chat Examples

Once extraction is complete, you can ask questions like:
- "What are the 3 most valuable contracts?"
- "Which documents mention non-compete clauses?"
- "What is the total contract value?"
- "Do any contracts have change of control provisions?"
- "Which cases are most relevant to my matter?"

Just type your question and I'll analyze the data!
"""
    
    await cl.Message(content=help_text).send()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    from chainlit.cli import run_chainlit
    run_chainlit(__file__)
