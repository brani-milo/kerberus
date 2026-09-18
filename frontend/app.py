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
import html
import logging
import base64
import re
from pathlib import Path
from typing import List, Optional

import chainlit as cl
from chainlit.input_widget import Select, Switch, Slider

# Add project root to Python path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pipeline import get_query_service, PipelineOptions

# Document processor for file uploads in assistant mode
from src.review import DocumentProcessor

# Auth imports
from src.database.auth_db import get_auth_db
from src.auth.service import AuthService
from src.api.deps import get_rate_limiter

# Conversation persistence (encrypted)
from src.database.encrypted_data_layer import get_encrypted_data_layer, EncryptedChainlitDataLayer

logger = logging.getLogger(__name__)

# =============================================================================
# ENCRYPTED DATA LAYER FOR CONVERSATION PERSISTENCE
# =============================================================================

# Allow unknown emails to self-register via the login form (default: enabled for demos).
# Set ALLOW_SELF_REGISTRATION=false in production to restrict access to existing accounts.
ALLOW_SELF_REGISTRATION = os.getenv("ALLOW_SELF_REGISTRATION", "true").lower() == "true"
if ALLOW_SELF_REGISTRATION:
    logger.warning("Self-registration is ENABLED: any unknown email can create an account via the login form")

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


_auth_service = None


def get_auth_service() -> AuthService:
    """AuthService shared with the REST API (same lockout/MFA/password rules)."""
    global _auth_service
    if _auth_service is None:
        db, _ = get_auth_components()
        _auth_service = AuthService(db)
    return _auth_service


@cl.password_auth_callback
async def auth_callback(username: str, password: str) -> Optional[cl.User]:
    """
    Authenticate with email/password via the shared AuthService.

    - Existing user: password check with lockout; MFA verification happens in chat.
    - Unknown user: registered automatically only when ALLOW_SELF_REGISTRATION is true.
    """
    auth = get_auth_service()
    try:
        result = auth.authenticate(username, password)

        if result.reason == "not_found":
            if not ALLOW_SELF_REGISTRATION:
                logger.warning(f"Login attempt for unknown user {username} (self-registration disabled)")
                return None
            try:
                user_id = auth.register(username, password)
            except ValueError as reg_error:
                logger.warning(f"Registration failed for {username}: {reg_error}")
                return None
            return cl.User(
                identifier=username,
                metadata={
                    "user_id": user_id, "email": username,
                    "mfa_required": False, "mfa_verified": True,
                    "mfa_setup_required": True, "is_new_user": True,
                },
            )

        if result.reason in ("locked", "inactive", "bad_password"):
            logger.info(f"Login rejected for {username}: {result.reason}")
            return None

        user = result.user
        user_id = str(user["user_id"])
        if result.reason == "mfa_required":
            # NOTE: the TOTP secret is deliberately NOT stored here. Chainlit
            # serializes user metadata into the auth JWT sent to the browser.
            return cl.User(
                identifier=user["email"],
                metadata={"user_id": user_id, "email": user["email"], "mfa_required": True, "mfa_verified": False},
            )

        # Password OK but MFA not enabled yet: force setup before use
        logger.info(f"User {user['email']} logged in but MFA not enabled - requiring setup")
        return cl.User(
            identifier=user["email"],
            metadata={
                "user_id": user_id, "email": user["email"],
                "mfa_required": False, "mfa_verified": True,
                "mfa_setup_required": True, "is_new_user": False,
            },
        )

    except Exception as e:
        logger.error(f"Auth error: {e}")
        return None


async def verify_mfa_code(user_metadata: dict, code: str) -> bool:
    """Verify a TOTP or backup code (secret is looked up server-side)."""
    return get_auth_service().verify_mfa_code(user_metadata["user_id"], code)


async def complete_mfa_login(user: cl.User) -> None:
    """Complete login after MFA verification."""
    user.metadata["session_token"] = get_auth_service().create_session(user.metadata["user_id"])
    user.metadata["mfa_verified"] = True
    logger.info(f"MFA verified for user: {user.identifier}")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def escape_untrusted(text) -> str:
    """
    HTML-escape text that comes from retrieved documents.

    Chainlit renders messages with `unsafe_allow_html = true` (needed for the
    welcome banner and the <details> sources block), so a raw '<' inside scraped
    court text would otherwise be interpreted as HTML (stored-XSS vector).
    """
    return html.escape(str(text or ""), quote=False)


_DANGEROUS_TAGS = re.compile(
    r"<\s*/?\s*(script|iframe|object|embed|style|link|meta|form|input|button|svg|math)\b[^>]*>",
    re.IGNORECASE,
)
_EVENT_HANDLER_ATTR = re.compile(
    r"(<[^>]*?)\s+on\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.IGNORECASE,
)
_JS_URL = re.compile(r"(href|src)\s*=\s*([\"']?)\s*javascript:", re.IGNORECASE)


def sanitize_llm_html(text: str) -> str:
    """
    Strip active HTML (script/iframe tags, inline event handlers, javascript: URLs)
    from LLM output while leaving Markdown untouched.

    The model is fed scraped documents, so its output is not fully trusted either.
    """
    if not text:
        return text
    cleaned = _DANGEROUS_TAGS.sub("", text)
    previous = None
    while previous != cleaned:  # attributes may be repeated inside one tag
        previous = cleaned
        cleaned = _EVENT_HANDLER_ATTR.sub(r"\1", cleaned)
    cleaned = _JS_URL.sub(r"\1=\2", cleaned)
    return cleaned


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
    preview = escape_untrusted(preview)
    citation = escape_untrusted(citation)

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
    preview = escape_untrusted(preview)
    citation = escape_untrusted(citation)
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

        global doc_processor
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
    global doc_processor

    logger.info(f"Resuming thread: {thread.get('id', 'unknown')}")

    # Initialize components lazily
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
    global doc_processor

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

    auth = get_auth_service()
    user_id = user.metadata["user_id"]
    backup_codes = auth.complete_mfa_setup(user_id, pending_secret, code)

    if backup_codes is None:
        await cl.Message(content="❌ **Invalid code.** Please check your authenticator app and try again.").send()
        return

    cl.user_session.set("pending_mfa_secret", None)
    cl.user_session.set("mode", "start")

    user.metadata["mfa_required"] = True
    user.metadata["mfa_verified"] = True
    user.metadata["mfa_setup_required"] = False
    user.metadata["session_token"] = auth.create_session(user_id)

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


async def start_mfa_setup():
    """Start the MFA setup process."""
    user = cl.user_session.get("user")
    if not user:
        await cl.Message(content="❌ Please log in first.").send()
        return

    try:
        secret, uri, qr_base64 = get_auth_service().begin_mfa_setup(user.metadata["user_id"], user.identifier)
    except ValueError:
        await cl.Message(content="✅ MFA is already enabled on your account.").send()
        return

    # Pending secret lives only in the server-side Chainlit session
    cl.user_session.set("pending_mfa_secret", secret)
    cl.user_session.set("mode", "mfa_setup")

    if qr_base64.startswith("data:image/png;base64,"):
        qr_bytes = base64.b64decode(qr_base64.split(",", 1)[1])
    else:
        qr_bytes = base64.b64decode(qr_base64)

    qr_image = cl.Image(name="mfa_qr_code.png", content=qr_bytes, display="inline", size="large")
    actions = [
        cl.Action(name="confirm_mfa", payload={}, label="✅ I've scanned it"),
        cl.Action(name="cancel_mfa", payload={}, label="❌ Cancel"),
    ]
    await cl.Message(
        content="""# 🔐 Setup Two-Factor Authentication

Scan this QR code with your authenticator app (Google Authenticator, Authy, etc.):""",
        elements=[qr_image],
        actions=actions
    ).send()
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
    """
    One user turn: rate limit -> uploaded files -> LegalQueryService events -> UI.

    The pipeline itself (guard, search, reformulate, context, analysis) lives in
    src/pipeline/service.py and is shared with the REST API.
    """
    global doc_processor

    # Rate limit (atomic; shared with the API)
    user = cl.user_session.get("user")
    if user and user.metadata.get("user_id"):
        _, rl = get_auth_components()
        allowed, hourly_remaining, daily_remaining = rl.consume(user.metadata["user_id"])
        if not allowed:
            await cl.Message(
                content=f"""⚠️ **Rate limit exceeded**

You have reached your query limit.
- Hourly remaining: {hourly_remaining}
- Daily remaining: {daily_remaining}

Please wait before making more queries, or contact support to increase your limit."""
            ).send()
            return

    # Uploaded files become part of the query sent to the models
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
                    if len(file_text) > 15000:
                        file_text = file_text[:15000] + "\n\n[... Document truncated for analysis ...]"
                    uploaded_content += f"\n\n--- UPLOADED DOCUMENT: {element.name} ---\n{file_text}\n--- END OF {element.name} ---\n"
                    uploaded_files_info.append(f"📄 {element.name} ({parsed.total_pages} pages)")
                except Exception as e:
                    logger.warning(f"Failed to parse uploaded file {element.name}: {e}")
                    uploaded_files_info.append(f"❌ {element.name} (failed to parse)")
        if uploaded_files_info:
            await cl.Message(
                content="**Uploaded files included in analysis:**\n" + "\n".join(uploaded_files_info),
                author="system"
            ).send()

    settings = cl.user_session.get("filters") or {}
    chat_history = cl.user_session.get("chat_history") or []

    # Settings -> pipeline options
    filters = {}
    lang_map = {"German (DE)": "de", "French (FR)": "fr", "Italian (IT)": "it"}
    if settings.get("language", "All") in lang_map:
        filters["language"] = lang_map[settings["language"]]
    year_min = settings.get("year_min", 1950)
    year_max = settings.get("year_max", 2026)
    if year_min or year_max:
        filters["year_range"] = {"min": int(year_min), "max": int(year_max)}
    scope_map = {"Both": "both", "Laws (Codex)": "laws", "Decisions (Library)": "decisions"}
    options = PipelineOptions(
        language="auto",
        search_scope=scope_map.get(settings.get("search_scope", "Both"), "both"),
        max_laws=25,
        max_decisions=10,
        web_search=bool(settings.get("web_search", False)),
        filters=filters or None,
        top_k=50,
        stream=True,
    )
    show_sources = settings.get("show_sources", True)

    full_query = message.content
    if uploaded_content:
        full_query = f"{message.content}\n\n[USER UPLOADED THE FOLLOWING DOCUMENT(S) FOR ANALYSIS:]{uploaded_content}"

    msg = cl.Message(content="")
    await msg.send()
    status_msg = cl.Message(content="_🛡️ Security check and query optimization..._")
    await status_msg.send()

    status_texts = {
        ("search", "processing"): "_🔍 Hybrid-Search + MMR + Reranking..._",
        ("search", "skipped"): "_📝 Processing follow-up request..._",
        ("reformulate", "processing"): "_📝 Structuring request..._",
        ("context", "processing"): "_📄 Loading full documents..._",
        ("analyze", "processing"): "_⚖️ Generating legal analysis..._",
    }

    answer = None
    error_text = None
    streamed = False
    try:
        async for event in get_query_service().run(
            full_query,
            display_query=message.content,
            chat_history=chat_history,
            options=options,
            previous_context=cl.user_session.get("previous_context"),
        ):
            key = (event.stage, event.status)
            if key in status_texts:
                status_msg.content = status_texts[key]
                await status_msg.update()

            if event.stage == "search" and event.status in ("complete", "skipped"):
                codex_results = event.data.get("codex_results", [])
                library_results = event.data.get("library_results", [])
                if show_sources and (codex_results or library_results):
                    sources_text = format_sources_collapsible(
                        codex_results, library_results,
                        event.data.get("codex_confidence", "NONE"), event.data.get("library_confidence", "NONE"),
                    )
                    await cl.Message(
                        content=f"""<details>
<summary>📚 **Legal Sources** (Click to expand)</summary>

{sources_text}
</details>""",
                        author="system",
                    ).send()

            elif event.stage == "analyze" and event.status == "processing":
                await status_msg.remove()
                msg.content = "_⚖️ Analyzing..._"
                await msg.update()

            elif event.stage == "analyze" and event.status == "chunk":
                if not streamed:
                    msg.content = ""
                    streamed = True
                await msg.stream_token(event.data["chunk"])

            elif event.stage == "complete":
                answer = event.data["answer_obj"]

            elif event.stage == "error":
                error_text = event.data.get("message", "Unexpected error")
                break

    except Exception as e:
        logger.error(f"Assistant turn failed: {e}", exc_info=True)
        error_text = str(e)

    if error_text or answer is None:
        try:
            await status_msg.remove()
        except Exception:
            pass
        msg.content = f"""⚠️ **{error_text or 'No result'}**

Please rephrase your question or try again."""
        await msg.update()
        return

    # Final text: sanitised, without the consistency JSON block, with the indicator appended
    final_text = sanitize_llm_html(answer.answer)
    indicator = get_consistency_indicator(answer.consistency, answer.confidence)
    msg.content = final_text + (f"\n\n---\n**{indicator}**" if indicator else "")
    await msg.update()

    usage = answer.token_usage
    await cl.Message(
        content=f"_Tokens: {usage.get('total_tokens', 0)} | Kosten: CHF {usage.get('total_cost_chf', 0.0):.4f}_",
        author="system",
    ).send()

    # Context for follow-up questions (only after a fresh search)
    if not answer.followup_used:
        cl.user_session.set("previous_context", answer.context_snapshot(message.content))

    chat_history.append({"role": "user", "content": message.content})
    chat_history.append({"role": "assistant", "content": final_text})
    cl.user_session.set("chat_history", chat_history[-10:])

    try:
        await persist_messages(user_message=message.content, assistant_message=final_text)
    except Exception as pe:
        logger.error(f"Failed to persist messages: {pe}")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    from chainlit.cli import run_chainlit
    run_chainlit(__file__)
