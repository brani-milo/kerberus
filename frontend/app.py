"""
KERBERUS Chainlit Frontend

Sovereign AI Legal Assistant for Swiss Law

Features:
- AI Legal Assistant: Query laws, decisions, get legal analysis (4-stage pipeline)
- Multi-language support (German, French, Italian)
- Encrypted conversation persistence

Authentication:
- Password-based login with MFA (TOTP)
- Rate limiting per user
- Session management via PostgreSQL

Note: Tabular Review module preserved in review_app.py for future development.
"""

import os
import asyncio
import json
import logging
import base64
from pathlib import Path
from typing import List, Dict, Optional

import chainlit as cl
from chainlit.input_widget import Select, Switch, Slider

# Add project root to Python path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.search.triad_search import TriadSearch
from src.llm import get_pipeline, ContextAssembler

# Document processor for file uploads in assistant mode
from src.review import DocumentProcessor

# Auth imports
from src.database.auth_db import get_auth_db, verify_password, hash_password
from src.auth.mfa import verify_totp, find_matching_backup_code, setup_mfa, generate_backup_codes, hash_backup_codes
from src.api.deps import get_rate_limiter, store_pending_mfa_secret

# Conversation persistence (encrypted)
from src.database.encrypted_data_layer import get_encrypted_data_layer, EncryptedChainlitDataLayer

logger = logging.getLogger(__name__)

# =============================================================================
# ENCRYPTED DATA LAYER FOR CONVERSATION PERSISTENCE
# =============================================================================

# Global data layer instance
_data_layer: Optional[EncryptedChainlitDataLayer] = None


def get_data_layer() -> Optional[EncryptedChainlitDataLayer]:
    """Get data layer singleton, initializing if encryption key is available."""
    global _data_layer
    if _data_layer is None:
        try:
            _data_layer = get_encrypted_data_layer()
            logger.info("Encrypted conversation data layer initialized")
        except ValueError as e:
            logger.warning(f"Conversation persistence disabled: {e}")
            _data_layer = None
    return _data_layer


# Enable encrypted conversation persistence
@cl.data_layer
def data_layer():
    """Chainlit data layer provider - returns encrypted PostgreSQL storage."""
    return get_data_layer()

# Global instances (initialized lazily)
triad_search = None
pipeline = None
context_assembler = None
doc_processor = None

# Auth components
auth_db = None
rate_limiter = None


# =============================================================================
# AUTHENTICATION
# =============================================================================

def get_auth_components():
    """Initialize auth components lazily."""
    global auth_db, rate_limiter
    if auth_db is None:
        auth_db = get_auth_db()
        # Run migrations to ensure schema is up to date
        try:
            auth_db.migrate_add_backup_codes_column()
        except Exception as e:
            logger.warning(f"Migration check failed (may be OK): {e}")
    if rate_limiter is None:
        rate_limiter = get_rate_limiter()
    return auth_db, rate_limiter


@cl.password_auth_callback
async def auth_callback(username: str, password: str) -> Optional[cl.User]:
    """
    Authenticate user with email/password.

    - If user exists: verify password and log in
    - If user doesn't exist: register them automatically
    - If MFA is enabled: trigger MFA verification flow
    """
    db, _ = get_auth_components()

    try:
        user = db.get_user_by_email(username)

        # User doesn't exist - register them
        if user is None:
            # Validate password length
            if len(password) < 8:
                logger.warning(f"Registration failed: password too short for {username}")
                return None

            # Validate email format (basic check)
            if "@" not in username or "." not in username:
                logger.warning(f"Registration failed: invalid email {username}")
                return None

            # Create new user
            try:
                password_hash_value = hash_password(password)
                user_id = db.create_user(username, password_hash_value)
                # Don't create session yet - require MFA setup first
                db.update_last_login(user_id)

                logger.info(f"New user registered: {username}")

                return cl.User(
                    identifier=username,
                    metadata={
                        "user_id": user_id,
                        "email": username,
                        "mfa_required": False,
                        "mfa_verified": True,
                        "mfa_setup_required": True,
                        "is_new_user": True,
                    }
                )
            except Exception as reg_error:
                logger.error(f"Registration error: {reg_error}")
                return None

        # User exists - verify password
        if not user["is_active"]:
            return None

        if not verify_password(password, user["password_hash"]):
            return None

        user_id = str(user["user_id"])

        # If MFA is enabled, we need to verify TOTP before granting access
        if user["mfa_enabled"]:
            # Return user with MFA pending flag
            # The on_chat_start will handle MFA verification
            return cl.User(
                identifier=user["email"],
                metadata={
                    "user_id": user_id,
                    "email": user["email"],
                    "mfa_required": True,
                    "mfa_verified": False,
                    "totp_secret": user["totp_secret"],
                }
            )

        # No MFA enabled - user must set up MFA before accessing the app
        # Don't create session yet - require MFA setup first
        logger.info(f"User {user['email']} logged in but MFA not enabled - requiring setup")

        return cl.User(
            identifier=user["email"],
            metadata={
                "user_id": user_id,
                "email": user["email"],
                "mfa_required": False,
                "mfa_verified": True,
                "mfa_setup_required": True,  # Flag to require MFA setup
                "is_new_user": False,
            }
        )

    except Exception as e:
        logger.error(f"Auth error: {e}")
        return None


async def verify_mfa_code(user_metadata: dict, code: str) -> bool:
    """
    Verify MFA code (TOTP or backup code).

    Returns True if verified, False otherwise.
    """
    db, _ = get_auth_components()
    user_id = user_metadata["user_id"]
    totp_secret = user_metadata.get("totp_secret")

    # Try TOTP first
    if verify_totp(totp_secret, code):
        return True

    # Try backup code
    hashed_codes = db.get_backup_codes(user_id)
    code_index = find_matching_backup_code(code, hashed_codes)
    if code_index is not None:
        db.remove_backup_code(user_id, code_index)
        logger.info(f"Backup code used for user {user_id}")
        return True

    return False


async def complete_mfa_login(user: cl.User) -> None:
    """Complete login after MFA verification."""
    db, _ = get_auth_components()
    user_id = user.metadata["user_id"]

    # Create session
    session_token = db.create_session(user_id, expires_hours=24)
    db.update_last_login(user_id)

    # Update user metadata
    user.metadata["session_token"] = session_token
    user.metadata["mfa_verified"] = True

    logger.info(f"MFA verified for user: {user.identifier}")


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


async def persist_messages(user_message: str, assistant_message: str) -> None:
    """
    Persist conversation messages to encrypted storage.

    Creates a new thread if needed, or appends to existing thread.
    """
    data_layer = get_data_layer()
    if not data_layer:
        return  # Persistence disabled (no encryption key)

    try:
        user = cl.user_session.get("user")
        if not user:
            return

        user_id = user.metadata.get("user_id", user.identifier)
        thread_id = cl.user_session.get("thread_id")

        # Create new thread if needed
        if not thread_id:
            # Generate thread name from first message
            thread_name = user_message[:50] + "..." if len(user_message) > 50 else user_message
            thread_id = await data_layer.create_thread(
                user_id=user_id,
                name=thread_name,
                metadata={"mode": "assistant"}
            )
            cl.user_session.set("thread_id", thread_id)
            logger.debug(f"Created new thread: {thread_id}")

        # Save messages
        await data_layer.create_message(
            thread_id=thread_id,
            role="user",
            content=user_message
        )
        await data_layer.create_message(
            thread_id=thread_id,
            role="assistant",
            content=assistant_message
        )

        logger.debug(f"Persisted messages to thread {thread_id}")

    except Exception as e:
        logger.error(f"Failed to persist messages: {e}")
        # Don't raise - persistence failure shouldn't break the chat


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

    # Extract year from multiple sources
    year = ''
    full_doc = result.get('full_document', {})
    if full_doc and full_doc.get('date'):
        # Date format: "2009-02-20" -> extract year
        year = str(full_doc.get('date', ''))[:4]
    if not year or year == '2000':  # 2000 is often a placeholder
        year = result.get('year', payload.get('year', ''))
    if not year or year == '2000':
        # Try to extract from decision_id like "BGer 001 1C-346-2008 2009-02-20"
        import re
        date_match = re.search(r'(\d{4})-\d{2}-\d{2}', str(decision_id) or str(case_id))
        if date_match:
            year = date_match.group(1)

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

    # Remove chunk suffixes from citation
    if '_chunk_' in citation:
        citation = citation.split('_chunk_')[0]
    elif ' chunk ' in citation.lower():
        import re
        citation = re.split(r'\s+chunk\s+\d+', citation, flags=re.IGNORECASE)[0].strip()

    court = payload.get('court', '')
    court_names = {
        'CH_BGE': 'BGer', 'CH_BGer': 'BGer', 'CH_BVGer': 'BVGer',
        'CH_BStGer': 'BStGer', 'CH_TI': 'TI',
    }
    court_display = court_names.get(court, court or '')

    # Priority: proper regeste > facts summary > reasoning first paragraph > text_preview
    # full_document is added by enrich_results_with_full_content
    text = ''
    full_doc = result.get('full_document', {})
    full_content = full_doc.get('content', {}) if full_doc else {}

    if isinstance(full_content, dict):
        regeste = full_content.get('regeste', '')
        # Check if regeste is a proper summary (not just header metadata)
        # Proper regestes don't start with "Bundesgericht" or court metadata
        if regeste and not regeste.startswith(('Bundesgericht', 'Tribunal fédéral', 'Tribunale federale')):
            text = regeste
        elif full_content.get('facts'):
            # Use first part of facts as summary
            text = full_content.get('facts', '')[:500]
        elif full_content.get('reasoning'):
            # Use first paragraph of reasoning
            reasoning = full_content.get('reasoning', '')
            # Find first paragraph (after any header)
            import re
            # Skip headers like "Erwägungen" or "Considérant en fait"
            clean_reasoning = re.sub(r'^(Erwägungen|Considérant|Diritto|Considerando)[^\n]*\n?', '', reasoning)
            text = clean_reasoning[:500]

    # Fallback to payload content
    if not text:
        content = payload.get('content', {})
        if isinstance(content, dict):
            text = content.get('regeste') or content.get('facts') or content.get('reasoning', '')

    # Last resort: text_preview (the chunk that was retrieved)
    if not text:
        text = payload.get('text_preview', '')

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
        parts.append(f"**Laws (Codex)** ({len(codex_results)} sources)\n")
        for i, res in enumerate(codex_results[:25], 1):  # Show up to 25 (15 laws + 10 ordinances)
            parts.append(format_law_result(res, i))

    if library_results:
        parts.append(f"\n**{library_emoji} Case Law (Library) - {library_conf}** ({len(library_results)} sources)\n")
        seen_ids = set()
        seen_texts = set()  # Also dedupe by text content
        rank = 1
        for res in library_results:
            if rank > 15:  # Show up to 15 unique decisions
                break

            payload = res.get('payload', {})
            decision_id = payload.get('decision_id', '') or payload.get('_original_id', '')

            import re

            # Skip entries with invalid IDs (single words, no numbers, not case identifiers)
            if not decision_id or decision_id == '-':
                continue
            # Must contain a number to be a valid case ID (e.g., BGE 102, 5A-190-2013)
            if not re.search(r'\d', decision_id):
                continue
            # Skip single words that don't look like case numbers
            if len(decision_id) < 5 or (len(decision_id) < 15 and not any(x in decision_id.upper() for x in ['BGE', 'BGER', 'CH_', '-', '/'])):
                continue

            # Normalize for deduplication
            normalized_id = decision_id

            # Remove chunk suffix (handle both "_chunk_" and " chunk " formats)
            if '_chunk_' in normalized_id:
                normalized_id = normalized_id.split('_chunk_')[0]
            if ' chunk ' in normalized_id.lower():
                normalized_id = re.split(r'\s+chunk\s+\d+', normalized_id, flags=re.IGNORECASE)[0]

            # Extract BGE number if present for better deduplication
            bge_match = re.search(r'BGE[-\s]*(\d+)[-\s]*([IVX]+)[-\s]*(\d+)', normalized_id, re.IGNORECASE)
            if bge_match:
                normalized_id = f"BGE-{bge_match.group(1)}-{bge_match.group(2).upper()}-{bge_match.group(3)}"
            else:
                # General normalization
                normalized_id = normalized_id.upper().strip()
                normalized_id = re.sub(r'[\s_-]+', '-', normalized_id)

            # Also check text similarity to catch true duplicates
            # Use first 150 chars, normalized (no whitespace variations)
            text_preview = payload.get('text_preview', '') or ''
            text_key = re.sub(r'\s+', ' ', text_preview[:150]).strip()

            if normalized_id not in seen_ids and text_key not in seen_texts:
                seen_ids.add(normalized_id)
                if text_key:
                    seen_texts.add(text_key)
                parts.append(format_decision_result(res, rank))
                rank += 1

    return "\n".join(parts) if parts else "No sources found."


def get_start_button() -> cl.Action:
    """Get start button for AI Legal Assistant."""
    return cl.Action(
        name="mode_assistant",
        payload={"mode": "assistant"},
        label="⚖️ Start AI Legal Assistant"
    )


async def handle_mfa_verification(code: str):
    """Handle MFA code verification."""
    user = cl.user_session.get("user")

    if not user:
        await cl.Message(content="❌ Session expired. Please log in again.").send()
        return

    # Clean the code (remove spaces, dashes for TOTP)
    clean_code = code.strip()

    # Verify the code
    if await verify_mfa_code(user.metadata, clean_code):
        # Complete the login
        await complete_mfa_login(user)

        # Show success and continue to main app
        await cl.Message(content="✅ **Authentication successful!**\n\n_Loading KERBERUS..._").send()

        # Reset mode and show welcome
        cl.user_session.set("mode", "start")

        # Initialize components and show welcome
        global triad_search, pipeline, context_assembler, doc_processor

        if triad_search is None:
            triad_search = TriadSearch()
        if pipeline is None:
            pipeline = get_pipeline()
        if context_assembler is None:
            context_assembler = ContextAssembler()
        if doc_processor is None:
            doc_processor = DocumentProcessor()

        cl.user_session.set("chat_history", [])

        # Show welcome message
        await cl.Message(
            content=f"""# 🛡️ **KERBERUS** - Swiss Legal Intelligence

Welcome, **{user.identifier}**!

---

## ⚖️ AI Legal Assistant

Ask legal questions in German, French, or Italian. Get answers with citations from Swiss laws and court decisions.

**Examples:**
- "What are the requirements for divorce in Switzerland?"
- "Quels sont les délais de prescription en droit suisse?"
- "Quali sono i diritti del lavoratore in caso di licenziamento?"

---

_Click the button below to begin:_""",
            actions=[get_start_button()]
        ).send()

    else:
        # Invalid code
        await cl.Message(
            content="""❌ **Invalid code**

Please check your authenticator app and try again.

_Make sure to enter the current 6-digit code, or use a backup code (format: XXXX-XXXX)._"""
        ).send()


# =============================================================================
# CHAT RESUME (Restore Previous Conversations)
# =============================================================================

@cl.on_chat_resume
async def on_chat_resume(thread):
    """
    Resume a previous conversation from encrypted storage.

    This is called when a user clicks on a previous thread in the sidebar.
    The thread parameter contains the thread data from the data layer.
    """
    global triad_search, pipeline, context_assembler, doc_processor

    logger.info(f"Resuming thread: {thread.get('id', 'unknown')}")

    # Initialize components lazily
    if triad_search is None:
        triad_search = TriadSearch()
    if pipeline is None:
        pipeline = get_pipeline()
    if context_assembler is None:
        context_assembler = ContextAssembler()
    if doc_processor is None:
        doc_processor = DocumentProcessor()

    # Restore chat history from thread messages
    chat_history = []
    data_layer = get_data_layer()

    if data_layer:
        try:
            messages = await data_layer.get_messages(thread.get("id", ""))
            for msg in messages:
                if msg["role"] in ["user", "assistant"]:
                    chat_history.append({
                        "role": msg["role"],
                        "content": msg["content"]
                    })
        except Exception as e:
            logger.error(f"Failed to restore messages: {e}")

    # Set session state
    cl.user_session.set("mode", "assistant")
    cl.user_session.set("chat_history", chat_history[-10:])  # Keep last 10 turns
    cl.user_session.set("thread_id", thread.get("id"))

    # Show resume message
    thread_name = thread.get("name", "previous conversation")
    msg_count = len(chat_history)

    await cl.Message(
        content=f"""# 🛡️ **KERBERUS** - Conversation Resumed

_Restored **{msg_count}** messages from "{thread_name}"._

---

Continue your legal research below. Just type your question."""
    ).send()


# =============================================================================
# CHAT START
# =============================================================================

@cl.on_chat_start
async def on_chat_start():
    global triad_search, pipeline, context_assembler, doc_processor

    # Check if user needs MFA verification
    user = cl.user_session.get("user")
    if user and user.metadata.get("mfa_required") and not user.metadata.get("mfa_verified"):
        cl.user_session.set("mode", "mfa_pending")
        await cl.Message(
            content="""# 🔐 Two-Factor Authentication Required

Please enter your 6-digit code from your authenticator app.

_Or enter a backup code (format: XXXX-XXXX) if you don't have access to your authenticator._"""
        ).send()
        return

    # Initialize components lazily
    if triad_search is None:
        triad_search = TriadSearch()
    if pipeline is None:
        pipeline = get_pipeline()
    if context_assembler is None:
        context_assembler = ContextAssembler()
    if doc_processor is None:
        doc_processor = DocumentProcessor()

    # Initialize session state
    cl.user_session.set("mode", "start")
    cl.user_session.set("chat_history", [])

    # Get user info for personalized welcome
    user = cl.user_session.get("user")
    user_email = user.identifier if user else "Guest"
    is_new_user = user.metadata.get("is_new_user", False) if user else False
    mfa_setup_required = user.metadata.get("mfa_setup_required", False) if user else False
    has_mfa_enabled = user.metadata.get("mfa_required", False) if user else False

    # ALL USERS WITHOUT MFA MUST SET IT UP FIRST
    if user and (is_new_user or mfa_setup_required) and not has_mfa_enabled:
        cl.user_session.set("mode", "mfa_setup_required")

        if is_new_user:
            welcome_text = f"""# 🛡️ **KERBERUS** - Swiss Legal Intelligence

## Welcome, {user_email}! 🎉

Your account has been created successfully.

---

## 🔐 Security Setup Required

To protect your account and comply with legal data security requirements, **Two-Factor Authentication (2FA) is mandatory**.

Please set up 2FA now to continue using KERBERUS."""
        else:
            welcome_text = f"""# 🛡️ **KERBERUS** - Swiss Legal Intelligence

## Welcome back, {user_email}!

---

## 🔐 Security Setup Required

Your account does not have Two-Factor Authentication (2FA) enabled.

To protect your account and comply with legal data security requirements, **2FA is mandatory**.

Please set up 2FA now to continue using KERBERUS."""

        await cl.Message(
            content=welcome_text,
            actions=[
                cl.Action(name="setup_mfa", payload={"action": "setup_mfa"}, label="🔐 Setup Two-Factor Authentication")
            ]
        ).send()
        return

    # Welcome message for authenticated users
    welcome_text = f"""# 🛡️ **KERBERUS** - Swiss Legal Intelligence

Welcome back, **{user_email}**!

---

## ⚖️ AI Legal Assistant

Ask legal questions in German, French, or Italian. Get answers with citations from Swiss laws and court decisions.

**Examples:**
- "What are the requirements for divorce in Switzerland?"
- "Quels sont les délais de prescription en droit suisse?"
- "Quali sono i diritti del lavoratore in caso di licenziamento?"

---

_Click the button below to begin:_"""

    await cl.Message(
        content=welcome_text,
        actions=[get_start_button()]
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




@cl.action_callback("setup_mfa")
async def on_action_setup_mfa(action: cl.Action):
    """Handle MFA setup button click."""
    await action.remove()
    await start_mfa_setup()


@cl.action_callback("confirm_mfa")
async def on_action_confirm_mfa(action: cl.Action):
    """Handle MFA confirmation."""
    await action.remove()
    cl.user_session.set("mode", "mfa_setup_verify")
    await cl.Message(
        content="Please enter the **6-digit code** from your authenticator app to verify setup:"
    ).send()


@cl.action_callback("cancel_mfa")
async def on_action_cancel_mfa(action: cl.Action):
    """Handle MFA setup cancellation."""
    await action.remove()
    cl.user_session.set("mode", "start")
    cl.user_session.set("pending_mfa_secret", None)
    await cl.Message(content="MFA setup cancelled. You can set it up later from the welcome screen.").send()


async def handle_mfa_setup_verification(code: str):
    """Handle MFA setup code verification."""
    user = cl.user_session.get("user")
    pending_secret = cl.user_session.get("pending_mfa_secret")

    if not user or not pending_secret:
        await cl.Message(content="❌ MFA setup session expired. Please try again.").send()
        cl.user_session.set("mode", "start")
        return

    # Clean the code
    clean_code = code.strip().replace(" ", "")

    # Verify the code
    if verify_totp(pending_secret, clean_code):
        db, _ = get_auth_components()
        user_id = user.metadata["user_id"]

        # Enable MFA
        db.update_totp_secret(user_id, pending_secret)

        # Generate and store backup codes
        backup_codes = generate_backup_codes(count=8)
        hashed_codes = hash_backup_codes(backup_codes)
        db.store_backup_codes(user_id, hashed_codes)

        # Clear pending secret
        cl.user_session.set("pending_mfa_secret", None)
        cl.user_session.set("mode", "start")

        # Create session now that MFA is set up
        session_token = db.create_session(user_id, expires_hours=24)

        # Update user metadata
        user.metadata["mfa_required"] = True
        user.metadata["mfa_verified"] = True
        user.metadata["mfa_setup_required"] = False
        user.metadata["session_token"] = session_token

        # Show success with backup codes
        codes_formatted = "\n".join([f"- `{code}`" for code in backup_codes])

        await cl.Message(
            content=f"""# ✅ Two-Factor Authentication Enabled!

Your account is now protected with 2FA.

## 🔑 Backup Codes

**Save these codes in a safe place!** You can use them to log in if you lose access to your authenticator app. Each code can only be used once.

{codes_formatted}

---

## ⚖️ AI Legal Assistant

Ask legal questions in German, French, or Italian. Get answers with citations from Swiss laws and court decisions.

_Click the button below to begin:_""",
            actions=[get_start_button()]
        ).send()

        logger.info(f"MFA enabled for user: {user.identifier}")

    else:
        await cl.Message(
            content="❌ **Invalid code.** Please check your authenticator app and try again."
        ).send()


async def start_mfa_setup():
    """Start the MFA setup process."""
    user = cl.user_session.get("user")
    if not user:
        await cl.Message(content="❌ Please log in first.").send()
        return

    db, _ = get_auth_components()
    user_id = user.metadata["user_id"]

    # Check if MFA already enabled
    full_user = db.get_user_by_id(user_id)
    if full_user and full_user["mfa_enabled"]:
        await cl.Message(content="✅ MFA is already enabled on your account.").send()
        return

    # Generate TOTP secret
    secret, uri, qr_base64 = setup_mfa(user.identifier, issuer="KERBERUS")

    # Store pending secret in session
    cl.user_session.set("pending_mfa_secret", secret)
    cl.user_session.set("mode", "mfa_setup")

    # Extract raw bytes from base64 data URI for cl.Image
    # Format: "data:image/png;base64,<base64_data>"
    if qr_base64.startswith("data:image/png;base64,"):
        b64_data = qr_base64.split(",", 1)[1]
        qr_bytes = base64.b64decode(b64_data)
    else:
        qr_bytes = base64.b64decode(qr_base64)

    # Create QR code image element
    qr_image = cl.Image(
        name="mfa_qr_code.png",
        content=qr_bytes,
        display="inline",
        size="large"
    )

    # Show QR code
    actions = [
        cl.Action(name="confirm_mfa", payload={}, label="✅ I've scanned it"),
        cl.Action(name="cancel_mfa", payload={}, label="❌ Cancel"),
    ]

    await cl.Message(
        content=f"""# 🔐 Setup Two-Factor Authentication

Scan this QR code with your authenticator app (Google Authenticator, Authy, etc.):""",
        elements=[qr_image],
        actions=actions
    ).send()

    # Send manual entry instructions separately for clarity
    await cl.Message(
        content=f"""**Or enter this secret manually:**
`{secret}`

---

Once you've added it to your authenticator, click "I've scanned it" above and enter the 6-digit code to verify."""
    ).send()


# =============================================================================
# MESSAGE HANDLER
# =============================================================================

@cl.on_message
async def on_message(message: cl.Message):
    mode = cl.user_session.get("mode", "start")
    logger.info(f"on_message: mode={mode}, text={message.content[:50]}...")
    text = message.content.strip()
    lower_text = text.lower()

    # Handle MFA verification (login)
    if mode == "mfa_pending":
        await handle_mfa_verification(text)
        return

    # Handle MFA setup verification
    if mode == "mfa_setup_verify":
        await handle_mfa_setup_verification(text)
        return

    # Handle MFA setup - allow typing code directly without clicking button
    if mode == "mfa_setup":
        # Check if input looks like a 6-digit code
        clean_code = text.replace(" ", "").replace("-", "")
        if clean_code.isdigit() and len(clean_code) == 6:
            await handle_mfa_setup_verification(text)
            return
        else:
            await cl.Message(
                content="Please enter the **6-digit code** from your authenticator app, or click 'I've scanned it' above."
            ).send()
            return

    # Block users who haven't set up MFA yet
    if mode == "mfa_setup_required":
        await cl.Message(
            content="🔐 Please set up Two-Factor Authentication first to continue.",
            actions=[
                cl.Action(name="setup_mfa", payload={"action": "setup_mfa"}, label="🔐 Setup Two-Factor Authentication")
            ]
        ).send()
        return

    # If still at start, prompt to begin
    if mode == "start":
        await cl.Message(
            content="Please click the button above to start the AI Legal Assistant.",
            actions=[get_start_button()]
        ).send()
        return

    # Handle mode switching commands (still supported as fallback)
    if lower_text in ["/assistant", "/search", "assistant", "/start", "start"]:
        await switch_to_assistant_mode()
        return

    # Handle file uploads in assistant mode
    if message.elements:
        if mode == "assistant":
            # In assistant mode, include file content in the legal analysis
            await handle_assistant_message(message, file_elements=message.elements)
            return
        else:
            await cl.Message(
                content="Please start the AI Legal Assistant first to upload files for analysis.",
                actions=[get_start_button()]
            ).send()
            return

    # Route to assistant handler
    if mode == "assistant":
        await handle_assistant_message(message, file_elements=None)
    else:
        await cl.Message(
            content="Please click the button to begin:",
            actions=[get_start_button()]
        ).send()


# =============================================================================
# MODE SWITCHING
# =============================================================================

async def switch_to_assistant_mode():
    cl.user_session.set("mode", "assistant")

    await cl.Message(
        content="""# ⚖️ AI Legal Assistant

Ask your legal questions in German, French, or Italian.

**Examples:**
- "What are the requirements for divorce?"
- "Quels sont les délais de prescription en droit suisse?"
- "Quali sono i diritti del lavoratore in caso di licenziamento?"

_Answers are backed by citations from Swiss laws and court decisions._

You can also upload documents (PDF, DOCX, TXT) for analysis using the 📎 button."""
    ).send()


# =============================================================================
# AI LEGAL ASSISTANT HANDLER
# =============================================================================

async def handle_assistant_message(message: cl.Message, file_elements: List = None):
    global triad_search, pipeline, doc_processor

    # Check rate limit
    user = cl.user_session.get("user")
    if user and user.metadata.get("user_id"):
        _, rl = get_auth_components()
        user_id = user.metadata["user_id"]
        allowed, hourly_remaining, daily_remaining = rl.check_rate_limit(user_id)

        if not allowed:
            await cl.Message(
                content=f"""⚠️ **Rate limit exceeded**

You have reached your query limit.
- Hourly remaining: {hourly_remaining}
- Daily remaining: {daily_remaining}

Please wait before making more queries, or contact support to increase your limit."""
            ).send()
            return

        # Record this request
        rl.record_request(user_id)

    # Parse uploaded files and include in context
    uploaded_content = ""
    uploaded_files_info = []

    if file_elements:
        if doc_processor is None:
            doc_processor = DocumentProcessor()

        for element in file_elements:
            if hasattr(element, 'path') and element.path:
                try:
                    parsed = doc_processor.parse_file(element.path)
                    file_text = parsed.full_text

                    # Truncate very long documents (keep first 15000 chars)
                    if len(file_text) > 15000:
                        file_text = file_text[:15000] + "\n\n[... Document truncated for analysis ...]"

                    uploaded_content += f"\n\n--- UPLOADED DOCUMENT: {element.name} ---\n{file_text}\n--- END OF {element.name} ---\n"
                    uploaded_files_info.append(f"📄 {element.name} ({parsed.total_pages} pages)")

                except Exception as e:
                    logger.warning(f"Failed to parse uploaded file {element.name}: {e}")
                    uploaded_files_info.append(f"❌ {element.name} (failed to parse)")

        if uploaded_files_info:
            await cl.Message(
                content=f"**Uploaded files included in analysis:**\n" + "\n".join(uploaded_files_info),
                author="system"
            ).send()

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

        # Build full query (user message + uploaded content)
        full_query = message.content
        if uploaded_content:
            full_query = f"{message.content}\n\n[USER UPLOADED THE FOLLOWING DOCUMENT(S) FOR ANALYSIS:]{uploaded_content}"

        # STAGE 1: Guard & Enhance
        try:
            logger.info("STAGE 1: Starting guard_and_enhance...")
            # Run synchronous LLM call in thread to avoid blocking event loop
            guard_result = await asyncio.to_thread(
                pipeline.guard_and_enhance,
                full_query,
                chat_history  # Pass chat history for follow-up detection
            )
            logger.info(f"STAGE 1: guard_and_enhance completed (is_followup={guard_result.is_followup})")

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
            is_followup = guard_result.is_followup
            followup_type = guard_result.followup_type
            # New task detection fields
            tasks = guard_result.tasks
            primary_task = guard_result.primary_task
            search_needed = guard_result.search_needed

        except Exception as guard_error:
            logger.warning(f"Guard stage failed: {guard_error}")
            detected_language = "de"
            enhanced_query = message.content
            legal_concepts = []
            is_followup = False
            followup_type = None
            tasks = ["legal_analysis"]
            primary_task = "legal_analysis"
            search_needed = True

        # Check if this is a follow-up question
        previous_context = cl.user_session.get("previous_context")

        if is_followup and previous_context:
            # FOLLOW-UP: Skip search, use previous context
            logger.info(f"STAGE 2: SKIPPED (follow-up detected: {followup_type})")
            status_msg.content = "_📝 Processing follow-up request..._"
            await status_msg.update()

            codex_results = previous_context.get("codex_results", [])
            library_results = previous_context.get("library_results", [])
            codex_conf = previous_context.get("codex_conf", "NONE")
            library_conf = previous_context.get("library_conf", "NONE")
            show_sources = previous_context.get("show_sources", True)
            search_scope = settings.get("search_scope", "Both")

            # Regenerate sources element (Chainlit objects can't be stored)
            sources_element = None
            if show_sources and (codex_results or library_results):
                sources_text = format_sources_collapsible(codex_results, library_results, codex_conf, library_conf)
                sources_element = cl.Text(name="📚 Legal Sources", content=sources_text, display="side")

            # Append follow-up instruction to the query for analysis
            enhanced_query = f"[FOLLOW-UP REQUEST: {followup_type}]\nOriginal analysis topic: {previous_context.get('original_query', '')}\nUser's follow-up: {message.content}"
        else:
            # NEW QUESTION: Run full search
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

            # Store context for potential follow-ups (don't store Chainlit objects)
            cl.user_session.set("previous_context", {
                "codex_results": codex_results,
                "library_results": library_results,
                "codex_conf": codex_conf,
                "library_conf": library_conf,
                "show_sources": show_sources,
                "original_query": message.content,
                "detected_language": detected_language,
            })

        # STAGE 3: Reformulate
        status_msg.content = "_📝 Structuring request..._"
        await status_msg.update()

        logger.info("STAGE 3: Starting reformulate...")
        topics = legal_concepts if legal_concepts else ["general legal question"]

        # Run synchronous LLM call in thread to avoid blocking event loop
        reformulated_query, reformulate_response = await asyncio.to_thread(
            pipeline.reformulate,
            original_query=message.content,
            enhanced_query=enhanced_query,
            language=detected_language,
            law_count=len(codex_results),
            decision_count=len(library_results),
            topics=topics,
            tasks=tasks,
            primary_task=primary_task,
        )
        logger.info("STAGE 3: Reformulate completed")

        # STAGE 4: Build Context
        status_msg.content = "_📄 Loading full documents..._"
        await status_msg.update()

        logger.info("STAGE 4: Starting build_context...")
        codex_for_context = [
            {"id": r.get("id"), "score": r.get("final_score", r.get("score", 0)), "payload": r.get("payload", {})}
            for r in codex_results
        ]
        library_for_context = [
            {"id": r.get("id"), "score": r.get("final_score", r.get("score", 0)), "payload": r.get("payload", {})}
            for r in library_results
        ]

        # Run synchronous Qdrant calls in thread to avoid blocking event loop
        # Feed Qwen with 25 law articles (15 laws + 10 ordinances) and 10 decisions = 35 inputs
        laws_context, decisions_context, context_meta = await asyncio.to_thread(
            pipeline.build_context,
            codex_results=codex_for_context,
            library_results=library_for_context,
            max_laws=25,
            max_decisions=10,
        )
        logger.info(f"STAGE 4: build_context completed - laws={context_meta.get('laws_count', 0)}, decisions={context_meta.get('decisions_count', 0)}")

        # STAGE 5: Legal Analysis
        status_msg.content = "_⚖️ Generating legal analysis..._"
        await status_msg.update()

        logger.info("STAGE 5: Starting legal analysis...")
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
            logger.info("STAGE 5: Starting analysis (non-streaming to avoid event loop blocking)...")

            # Use non-streaming version to avoid event loop blocking
            # The streaming version blocks the event loop during HTTP iteration
            # Run analysis in background task so we can send heartbeat updates
            analysis_task = asyncio.create_task(asyncio.to_thread(
                pipeline.analyze_sync,
                reformulated_query=reformulated_query,
                laws_context=laws_context,
                decisions_context=decisions_context,
                language=detected_language,
                web_search=web_search_enabled,
            ))

            # Send heartbeat updates while waiting for analysis
            dots = 0
            while not analysis_task.done():
                dots = (dots % 3) + 1
                msg.content = f"_⚖️ Analyzing{'.' * dots}_"
                await msg.update()
                await asyncio.sleep(2)  # Update every 2 seconds

            # Get the result
            analysis_text, final_response = await analysis_task

            # Clean up JSON consistency block from response and extract it
            import re
            consistency_match = re.search(r'```json\s*(\{[^}]+\})\s*```', analysis_text)
            consistency_info = ""
            if consistency_match:
                try:
                    consistency_data = json.loads(consistency_match.group(1))
                    consistency = consistency_data.get("consistency", "MIXED")
                    confidence = consistency_data.get("confidence", "medium")
                    consistency_info = get_consistency_indicator(consistency, confidence)
                    # Remove the JSON block from the text
                    analysis_text = re.sub(r'```json\s*\{[^}]+\}\s*```', '', analysis_text).strip()
                except json.JSONDecodeError:
                    pass

            # Stream the already-received response to the UI
            logger.info("STAGE 5: Analysis received, streaming to UI...")
            full_response = [analysis_text]
            chunk_size = 50  # Characters per chunk
            for i in range(0, len(analysis_text), chunk_size):
                chunk = analysis_text[i:i + chunk_size]
                msg.content = analysis_text[:i + chunk_size]
                await msg.stream_token(chunk)
                await asyncio.sleep(0.01)  # Small delay for UI rendering

            # Add consistency indicator at the end if found
            if consistency_info:
                msg.content = analysis_text + f"\n\n---\n**{consistency_info}**"
                await msg.update()

            logger.info("STAGE 5: Analysis complete")

        except Exception as e:
            logger.error(f"STAGE 5: Analysis error: {e}")
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
        chat_history.append({"role": "assistant", "content": "".join(full_response)})
        cl.user_session.set("chat_history", chat_history[-10:])
        logger.info("Chat history updated, persisting messages...")

        # Persist to encrypted storage
        try:
            await persist_messages(
                user_message=message.content,
                assistant_message="".join(full_response)
            )
            logger.info("Messages persisted successfully")
        except Exception as pe:
            logger.error(f"Failed to persist messages: {pe}")
            # Don't raise - persistence failure shouldn't break the chat

        logger.info("handle_assistant_message completed successfully")

    except Exception as e:
        msg.content = f"**Error:** {str(e)}"
        await msg.update()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    from chainlit.cli import run_chainlit
    run_chainlit(__file__)
