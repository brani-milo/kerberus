"""
Auth endpoints end-to-end over an in-memory AuthDB double:
register -> login -> MFA setup/verify -> login with TOTP/backup -> password change -> lockout.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.deps import get_db, check_register_rate_limit, check_login_rate_limit, _mfa_pending_memory
from src.database.auth_db import verify_password


class MemoryAuthDB:
    """Just enough of AuthDB for the auth routes (no PostgreSQL)."""

    def __init__(self):
        self.users, self.sessions, self.failed, self.keys, self.usage = {}, {}, [], {}, []

    # users
    def create_user(self, email, password_hash, totp_secret=None):
        if any(u["email"] == email for u in self.users.values()):
            raise ValueError("Email already exists")
        uid = str(uuid.uuid4())
        self.users[uid] = {"user_id": uid, "email": email, "password_hash": password_hash, "totp_secret": totp_secret,
                           "is_active": True, "mfa_enabled": False, "backup_codes": [],
                           "created_at": datetime.now(timezone.utc), "last_login": None}
        return uid

    def get_user_by_email(self, email):
        return next((dict(u) for u in self.users.values() if u["email"] == email), None)

    def get_user_by_id(self, uid):
        return dict(self.users[uid]) if uid in self.users else None

    def update_last_login(self, uid):
        self.users[uid]["last_login"] = datetime.now(timezone.utc)

    def update_password(self, uid, h):
        self.users[uid]["password_hash"] = h

    # sessions
    def create_session(self, uid, device_fingerprint=None, expires_hours=24):
        tok = uuid.uuid4().hex
        self.sessions[tok] = {"user_id": uid, "active": True, "expires": datetime.now(timezone.utc) + timedelta(hours=expires_hours)}
        return tok

    def validate_session(self, tok):
        s = self.sessions.get(tok)
        if not s or not s["active"] or s["expires"] < datetime.now(timezone.utc):
            return None
        u = self.users[s["user_id"]]
        return {"user_id": u["user_id"], "email": u["email"], "is_active": True, "mfa_enabled": u["mfa_enabled"]}

    def invalidate_session(self, tok):
        self.sessions[tok]["active"] = False

    def invalidate_all_sessions(self, uid, except_token=None):
        n = 0
        for tok, s in self.sessions.items():
            if s["user_id"] == uid and s["active"] and tok != except_token:
                s["active"] = False; n += 1
        return n

    # lockout
    def record_failed_login(self, email):
        self.failed.append((email, datetime.now(timezone.utc)))

    def get_failed_login_count(self, email, window_minutes=15):
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        return sum(1 for e, t in self.failed if e == email and t > cutoff)

    def clear_failed_logins(self, email):
        self.failed = [(e, t) for e, t in self.failed if e != email]

    # mfa
    def update_totp_secret(self, uid, secret):
        self.users[uid]["totp_secret"] = secret
        self.users[uid]["mfa_enabled"] = secret is not None

    def store_backup_codes(self, uid, hashed):
        self.users[uid]["backup_codes"] = list(hashed)

    def get_backup_codes(self, uid):
        return list(self.users[uid]["backup_codes"])

    def remove_backup_code(self, uid, index):
        self.users[uid]["backup_codes"].pop(index)

    # dossier keys
    def get_dossier_key(self, uid):
        return self.keys.get(uid)

    def store_dossier_key(self, uid, record):
        self.keys[uid] = dict(record)

    def record_token_usage(self, user_id, usage_record):
        self.usage.append(usage_record); return 1

    def get_user_monthly_costs(self, uid, year=None, month=None):
        return {"total_tokens": 0, "total_cost_chf": 0.0, "request_count": 0, "month": "2026-09"}


@pytest.fixture
def client():
    db = MemoryAuthDB()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[check_register_rate_limit] = lambda: None
    app.dependency_overrides[check_login_rate_limit] = lambda: None
    _mfa_pending_memory.clear()
    yield TestClient(app), db
    app.dependency_overrides.clear()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_login_and_me(client):
    c, db = client
    r = c.post("/auth/register", json={"email": "a@b.ch", "password": "password123"})
    assert r.status_code == 201 and r.json()["mfa_enabled"] is False
    assert c.post("/auth/register", json={"email": "a@b.ch", "password": "password123"}).status_code == 409
    assert c.post("/auth/register", json={"email": "c@d.ch", "password": "short"}).status_code in (400, 422)

    r = c.post("/auth/login", json={"email": "a@b.ch", "password": "password123"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert c.get("/auth/me", headers=auth(token)).json()["email"] == "a@b.ch"
    assert c.post("/auth/login", json={"email": "a@b.ch", "password": "nope"}).status_code == 401


def test_mfa_setup_verify_and_login_with_totp_and_backup(client):
    c, db = client
    token = c.post("/auth/register", json={"email": "m@b.ch", "password": "password123"}).json()["access_token"]

    setup = c.post("/auth/mfa/setup", headers=auth(token))
    assert setup.status_code == 200
    secret = setup.json()["secret"]

    assert c.post("/auth/mfa/verify", json={"totp_code": "000000"}, headers=auth(token)).status_code == 400
    ok = c.post("/auth/mfa/verify", json={"totp_code": pyotp.TOTP(secret).now()}, headers=auth(token))
    assert ok.status_code == 200
    backup_codes = ok.json()["backup_codes"]
    assert len(backup_codes) == 8

    # password alone is no longer enough
    r = c.post("/auth/login", json={"email": "m@b.ch", "password": "password123"})
    assert r.status_code == 403 and r.headers["X-MFA-Required"] == "true"
    assert c.post("/auth/login", json={"email": "m@b.ch", "password": "password123", "totp_code": "123456"}).status_code == 401
    assert c.post("/auth/login", json={"email": "m@b.ch", "password": "password123",
                                       "totp_code": pyotp.TOTP(secret).now()}).status_code == 200
    # backup code works once
    assert c.post("/auth/login", json={"email": "m@b.ch", "password": "password123", "backup_code": backup_codes[0]}).status_code == 200
    assert c.post("/auth/login", json={"email": "m@b.ch", "password": "password123", "backup_code": backup_codes[0]}).status_code == 401

    # disable
    assert c.request("DELETE", "/auth/mfa", json={"totp_code": pyotp.TOTP(secret).now()}, headers=auth(token)).status_code == 204
    assert c.post("/auth/login", json={"email": "m@b.ch", "password": "password123"}).status_code == 200


def test_password_change_keeps_current_session_and_rotates_dossier_key(client):
    c, db = client
    token = c.post("/auth/register", json={"email": "p@b.ch", "password": "password123"}).json()["access_token"]
    other = c.post("/auth/login", json={"email": "p@b.ch", "password": "password123"}).json()["access_token"]
    uid = db.validate_session(token)["user_id"]

    # simulate an existing dossier key (wrapped with the current password)
    from src.security.dossier_keys import wrap_dek, unwrap_dek, WrappedKey
    import secrets
    dek = secrets.token_bytes(32)
    db.store_dossier_key(uid, wrap_dek(dek, "password123", iterations=1000, user_id=uid).to_record())

    assert c.post("/auth/password/change", json={"current_password": "wrong", "new_password": "newpassword1"},
                  headers=auth(token)).status_code == 401
    r = c.post("/auth/password/change", json={"current_password": "password123", "new_password": "newpassword1"},
               headers=auth(token))
    assert r.status_code == 204

    assert c.get("/auth/me", headers=auth(token)).status_code == 200      # current session survives
    assert c.get("/auth/me", headers=auth(other)).status_code == 401      # other sessions are gone
    assert verify_password("newpassword1", db.users[uid]["password_hash"])
    assert unwrap_dek(WrappedKey.from_record(db.keys[uid]), "newpassword1") == dek   # dossier still readable
    assert c.post("/auth/login", json={"email": "p@b.ch", "password": "password123"}).status_code == 401


def test_account_lockout_after_failed_attempts(client):
    c, db = client
    c.post("/auth/register", json={"email": "l@b.ch", "password": "password123"})
    for _ in range(5):
        assert c.post("/auth/login", json={"email": "l@b.ch", "password": "bad"}).status_code == 401
    r = c.post("/auth/login", json={"email": "l@b.ch", "password": "password123"})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_logout_all(client):
    c, db = client
    t1 = c.post("/auth/register", json={"email": "o@b.ch", "password": "password123"}).json()["access_token"]
    t2 = c.post("/auth/login", json={"email": "o@b.ch", "password": "password123"}).json()["access_token"]
    assert c.post("/auth/logout/all", headers=auth(t1)).status_code == 204
    assert c.get("/auth/me", headers=auth(t2)).status_code == 401
