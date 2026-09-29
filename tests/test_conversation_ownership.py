"""
Encrypted data layer against a real SQL engine (SQLite), exercising the calls Chainlit
makes: user lookup, thread list, first interaction, resume, rename, session metadata.
The login token is issued before the TOTP step, so nothing reachable with it may reveal
conversation content.
"""
import asyncio
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from src.database.encrypted_data_layer import EncryptedChainlitDataLayer

USER_ID = str(uuid.uuid4())
EMAIL = "lawyer@example.ch"
OTHER_ID = str(uuid.uuid4())


@pytest.fixture
def layer(tmp_path):
    dl = EncryptedChainlitDataLayer(database_url=f"sqlite:///{tmp_path / 'conv.db'}",
                                    encryption_key=Fernet.generate_key().decode())
    now = datetime.now(timezone.utc).isoformat()
    with dl._engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (user_id TEXT PRIMARY KEY, email TEXT, mfa_enabled BOOLEAN, "
                          "created_at TEXT, is_active BOOLEAN)"))
        conn.execute(text("INSERT INTO users VALUES (:u, :e, 1, :c, 1)"), {"u": USER_ID, "e": EMAIL, "c": now})
        conn.execute(text("INSERT INTO users VALUES (:u, 'other@example.ch', 0, :c, 1)"), {"u": OTHER_ID, "c": now})
    return dl


def run(coro):
    return asyncio.run(coro)


def page(first=20, cursor=None):
    return SimpleNamespace(first=first, cursor=cursor)


def chainlit_first_interaction(layer, thread_id, typed_text, user_id=USER_ID):
    """What Chainlit's emitter does on the first message of a session."""
    return run(layer.update_thread(thread_id=thread_id, name=typed_text, user_id=user_id, tags=None))


# ---------------------------------------------------------------- users ---

def test_get_user_is_the_account_with_server_side_mfa_state(layer):
    user = run(layer.get_user(EMAIL.upper()))
    assert user.id == USER_ID and user.identifier == EMAIL
    assert user.metadata["mfa_required"] is True and user.metadata["mfa_verified"] is False
    other = run(layer.get_user("other@example.ch"))
    assert other.metadata["mfa_required"] is False and other.metadata["mfa_setup_required"] is True
    assert run(layer.get_user("nobody@example.ch")) is None
    assert run(layer.create_user(SimpleNamespace(identifier=EMAIL))).id == USER_ID


# -------------------------------------------------------------- threads ---

def test_thread_author_is_the_login_identifier(layer):
    tid = str(uuid.uuid4())
    chainlit_first_interaction(layer, tid, "Darf meine Frau arbeiten?")
    thread = run(layer.get_thread(tid))
    assert thread["userIdentifier"] == EMAIL and run(layer.get_thread_author(tid)) == EMAIL
    assert thread["userId"] == USER_ID


def test_first_interaction_text_never_becomes_the_name(layer):
    tid = str(uuid.uuid4())
    chainlit_first_interaction(layer, tid, "123456")                       # a TOTP code
    assert run(layer.get_thread(tid))["name"].startswith("Conversation ")
    tid2 = str(uuid.uuid4())
    chainlit_first_interaction(layer, tid2, "Vertrauliche Frage zum Mandat Meier")
    run(layer.update_thread(thread_id=tid2, name="Something typed later", user_id=USER_ID, tags=None))
    assert "Meier" not in run(layer.get_thread(tid2))["name"]
    # an explicit rename from the sidebar (name only) is honoured
    run(layer.update_thread(thread_id=tid2, name="Mandat Meier"))
    assert run(layer.get_thread(tid2))["name"] == "Mandat Meier"


def test_session_metadata_is_filtered(layer):
    tid = str(uuid.uuid4())
    chainlit_first_interaction(layer, tid, "q")
    run(layer.update_thread(thread_id=tid, metadata={
        "chat_settings": {"language": "de"}, "client_type": "webapp",
        "chat_history": [{"role": "user", "content": "secret"}], "pending_mfa_secret": "JBSWY3DP",
        "previous_context": {"codex_results": []},
    }))
    meta = run(layer.get_thread(tid))["metadata"]
    assert meta == {"chat_settings": {"language": "de"}, "client_type": "webapp"}


def test_list_threads_own_non_empty_threads_paginated(layer):
    ids = []
    for i in range(3):
        tid = str(uuid.uuid4()); ids.append(tid)
        chainlit_first_interaction(layer, tid, f"q{i}")
        run(layer.create_message(thread_id=tid, role="user", content=f"frage {i}"))
        time.sleep(0.01)
    empty = str(uuid.uuid4()); chainlit_first_interaction(layer, empty, "654321")    # only an MFA code
    foreign = str(uuid.uuid4()); chainlit_first_interaction(layer, foreign, "x", user_id=OTHER_ID)
    run(layer.create_message(thread_id=foreign, role="user", content="not yours"))

    res = run(layer.list_threads(page(first=2), SimpleNamespace(userId=USER_ID)))
    body = res.to_dict()                                   # what Chainlit's endpoint calls
    assert [t["id"] for t in body["data"]] == [ids[2], ids[1]] and body["pageInfo"]["hasNextPage"] is True
    nxt = run(layer.list_threads(page(first=2, cursor=body["pageInfo"]["endCursor"]), SimpleNamespace(userId=USER_ID)))
    assert [t["id"] for t in nxt.to_dict()["data"]] == [ids[0]]
    assert all(t["userIdentifier"] == EMAIL and t["steps"] == [] for t in body["data"])


def test_get_thread_never_returns_message_content(layer):
    tid = str(uuid.uuid4())
    chainlit_first_interaction(layer, tid, "q")
    run(layer.create_message(thread_id=tid, role="user", content="Vertrauliche Frage"))
    assert run(layer.get_thread(tid))["steps"] == []
    assert [m["content"] for m in run(layer.get_messages(tid))] == ["Vertrauliche Frage"]


def test_unknown_owner_is_adopted_and_rebound(layer):
    tid = str(uuid.uuid4())
    run(layer.update_thread(thread_id=tid, name="Mandat", user_id=None, tags=None))     # before the user was known
    assert run(layer.get_thread(tid))["userId"] == "unknown"
    run(layer.update_thread(thread_id=tid, user_id=USER_ID, tags=None))
    thread = run(layer.get_thread(tid))
    assert thread["userId"] == USER_ID and thread["name"].startswith("Conversation ")   # decrypts under the new owner


def test_messages_roundtrip_and_delete_purges(layer):
    tid = str(uuid.uuid4())
    chainlit_first_interaction(layer, tid, "q")
    run(layer.create_message(thread_id=tid, role="user", content="Vertrauliche Frage"))
    with layer._engine.connect() as conn:
        stored = conn.execute(text("SELECT content_encrypted FROM conversation_messages")).scalar()
    assert stored.startswith("v2:") and "Vertrauliche" not in stored
    assert run(layer.delete_thread(tid)) is True
    assert run(layer.get_thread(tid)) is None and run(layer.get_thread_author(tid)) is None
    with layer._engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM conversation_messages")).scalar() == 0


# ------------------------------------------------------------ MFA store ---

def test_mfa_session_store_binds_token_and_user(monkeypatch):
    from src.auth import mfa_session
    monkeypatch.setattr(mfa_session, "_redis", lambda: None)
    mfa_session.remember_mfa("token-a", USER_ID, ttl=60)
    assert mfa_session.mfa_passed("token-a", USER_ID)
    assert not mfa_session.mfa_passed("token-a", OTHER_ID)       # same token, other user
    assert not mfa_session.mfa_passed("token-b", USER_ID)        # new login
    mfa_session.remember_mfa("token-c", USER_ID, ttl=-1)
    assert not mfa_session.mfa_passed("token-c", USER_ID)        # expired
    mfa_session.forget_mfa("token-a")
    assert not mfa_session.mfa_passed("token-a", USER_ID)


def test_legacy_dossier_point_resolves_by_doc_and_chunk():
    from src.search.dossier_search import DossierSearchService
    legacy = {"id": "6f1c9a2e-0000-5000-8000-000000000000", "payload": {"doc_id": "d1", "chunk_index": 3}}
    current = {"id": "x", "payload": {"doc_id": "d1", "chunk_index": 3, "embedding_id": "d1_chunk_3"}}
    assert DossierSearchService._embedding_id(legacy) == "d1_chunk_3"
    assert DossierSearchService._embedding_id(current) == "d1_chunk_3"
    assert DossierSearchService._embedding_id({"id": "abc", "payload": {}}) == "abc"
