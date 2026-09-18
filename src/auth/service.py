"""
AuthService: the ONE implementation of authentication rules.

Used by the REST API (src/api/routes/auth.py) and the Chainlit UI
(frontend/app.py) so lockout, MFA, password policy and dossier key rotation
behave identically in both.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..database.auth_db import AuthDB, hash_password, verify_password
from ..security.dossier_keys import DossierKeyManager, DossierKeyError
from .mfa import (
    setup_mfa, verify_totp, generate_backup_codes, hash_backup_codes, find_matching_backup_code,
)

logger = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LENGTH = 8
SESSION_HOURS = 24


@dataclass
class AuthResult:
    ok: bool
    reason: str                      # ok | not_found | inactive | bad_password | locked | mfa_required | mfa_invalid
    user: Optional[dict] = None
    session_token: Optional[str] = None

    @property
    def mfa_required(self) -> bool:
        return self.reason == "mfa_required"


def validate_password(password: str) -> Optional[str]:
    """Return an error message when the password violates the policy, else None."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    return None


class AuthService:
    def __init__(self, db: AuthDB, key_manager: Optional[DossierKeyManager] = None):
        self.db = db
        self.keys = key_manager or DossierKeyManager(db)

    # --- accounts -------------------------------------------------------------

    def register(self, email: str, password: str) -> str:
        """Create an account. Raises ValueError for policy violations or duplicates."""
        error = validate_password(password)
        if error:
            raise ValueError(error)
        if "@" not in email or "." not in email:
            raise ValueError("Invalid email address")
        user_id = self.db.create_user(email=email, password_hash=hash_password(password))
        self.db.update_last_login(user_id)
        logger.info(f"New user registered: {email}")
        return user_id

    def authenticate(self, email: str, password: str) -> AuthResult:
        """Password check with lockout tracking. Does NOT create a session."""
        if self.db.get_failed_login_count(email, LOCKOUT_MINUTES) >= MAX_FAILED_ATTEMPTS:
            return AuthResult(False, "locked")

        user = self.db.get_user_by_email(email)
        if user is None:
            self.db.record_failed_login(email)
            return AuthResult(False, "not_found")
        if not user.get("is_active", True):
            return AuthResult(False, "inactive", user)
        if not verify_password(password, user["password_hash"]):
            self.db.record_failed_login(email)
            return AuthResult(False, "bad_password")

        self.db.clear_failed_logins(email)
        if user.get("mfa_enabled"):
            return AuthResult(False, "mfa_required", user)
        return AuthResult(True, "ok", user)

    def login(self, email: str, password: str, totp_code: Optional[str] = None,
              backup_code: Optional[str] = None) -> AuthResult:
        """Full login: password, MFA (if enabled) and session creation."""
        result = self.authenticate(email, password)
        if result.reason == "mfa_required":
            if not totp_code and not backup_code:
                return result
            if not self.verify_mfa(str(result.user["user_id"]), totp_code=totp_code, backup_code=backup_code):
                return AuthResult(False, "mfa_invalid", result.user)
            result = AuthResult(True, "ok", result.user)
        if not result.ok:
            return result
        result.session_token = self.create_session(str(result.user["user_id"]))
        logger.info(f"User logged in: {email}")
        return result

    def create_session(self, user_id: str) -> str:
        token = self.db.create_session(user_id, expires_hours=SESSION_HOURS)
        self.db.update_last_login(user_id)
        return token

    # --- MFA ------------------------------------------------------------------

    def verify_mfa(self, user_id: str, totp_code: Optional[str] = None, backup_code: Optional[str] = None) -> bool:
        """TOTP first, then a one-time backup code (consumed on success)."""
        user = self.db.get_user_by_id(user_id)
        if not user:
            return False
        secret = user.get("totp_secret")
        if totp_code and secret and verify_totp(secret, totp_code.replace(" ", "")):
            return True
        if backup_code:
            hashed = self.db.get_backup_codes(user_id)
            index = find_matching_backup_code(backup_code, hashed)
            if index is not None:
                self.db.remove_backup_code(user_id, index)
                logger.info(f"Backup code used for user {user_id}")
                return True
        return False

    def verify_mfa_code(self, user_id: str, code: str) -> bool:
        """Single-field variant (UI): 6 digits => TOTP, anything else => backup code."""
        clean = (code or "").strip()
        digits = clean.replace(" ", "").replace("-", "")
        if digits.isdigit() and len(digits) == 6:
            return self.verify_mfa(user_id, totp_code=digits)
        return self.verify_mfa(user_id, backup_code=clean)

    def begin_mfa_setup(self, user_id: str, email: str, issuer: str = "KERBERUS") -> Tuple[str, str, str]:
        """Returns (secret, provisioning_uri, qr_base64). Raises ValueError if MFA is already on."""
        user = self.db.get_user_by_id(user_id)
        if user and user.get("mfa_enabled"):
            raise ValueError("MFA is already enabled. Disable it first to set up a new authenticator.")
        return setup_mfa(email, issuer=issuer)

    def complete_mfa_setup(self, user_id: str, secret: str, code: str) -> Optional[List[str]]:
        """Verify the first code; on success enable MFA and return fresh backup codes."""
        if not verify_totp(secret, (code or "").replace(" ", "")):
            return None
        self.db.update_totp_secret(user_id, secret)
        backup_codes = generate_backup_codes(count=8)
        self.db.store_backup_codes(user_id, hash_backup_codes(backup_codes))
        logger.info(f"MFA enabled for user {user_id}")
        return backup_codes

    def disable_mfa(self, user_id: str, code: str) -> bool:
        user = self.db.get_user_by_id(user_id)
        if not user or not user.get("mfa_enabled"):
            raise ValueError("MFA is not enabled")
        if not verify_totp(user["totp_secret"], code):
            return False
        self.db.update_totp_secret(user_id, None)
        self.db.store_backup_codes(user_id, [])
        logger.info(f"MFA disabled for user {user_id}")
        return True

    # --- password change -------------------------------------------------------

    def change_password(self, user_id: str, current_password: str, new_password: str,
                        keep_session_token: Optional[str] = None) -> bool:
        """
        Verify the current password, store the new hash, re-wrap the dossier key
        and invalidate every other session. Returns False on a wrong current password.
        """
        error = validate_password(new_password)
        if error:
            raise ValueError(error)
        user = self.db.get_user_by_id(user_id)
        if not user or not verify_password(current_password, user["password_hash"]):
            return False

        # Rotate the dossier key wrapper FIRST: if it fails the password stays unchanged
        try:
            self.keys.rewrap(user_id, current_password, new_password)
        except DossierKeyError as e:
            logger.error(f"Dossier key re-wrap failed for {user_id}: {e}")
            raise

        self.db.update_password(user_id, hash_password(new_password))
        self.db.invalidate_all_sessions(user_id, except_token=keep_session_token)
        logger.info(f"Password changed for user {user_id}")
        return True
