"""
Authentication Endpoints.

Thin HTTP layer over src.auth.service.AuthService (shared with the Chainlit UI).
"""
import logging
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, status

from ..models import (
    UserRegister, UserLogin, TokenResponse, PasswordChangeRequest,
    MFASetupResponse, MFAVerifyRequest, MFAVerifyResponse, UserResponse, UsageStats, ErrorResponse,
)
from ..deps import (
    get_db, get_current_user, store_pending_mfa_secret, get_pending_mfa_secret,
    check_register_rate_limit, check_login_rate_limit,
)
from ...database.auth_db import AuthDB
from ...auth.service import AuthService, SESSION_HOURS, LOCKOUT_MINUTES
from ...security.dossier_keys import DossierKeyError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])


def get_auth_service(db: AuthDB = Depends(get_db)) -> AuthService:
    return AuthService(db)


def _token_response(user_id: str, email: str, token: str, mfa_enabled: bool) -> TokenResponse:
    return TokenResponse(
        access_token=token, token_type="bearer", expires_in=SESSION_HOURS * 3600,
        user_id=user_id, email=email, mfa_enabled=mfa_enabled,
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        409: {"model": ErrorResponse, "description": "Email already exists"},
        429: {"model": ErrorResponse, "description": "Too many registration attempts"},
    },
    dependencies=[Depends(check_register_rate_limit)],
)
async def register(user_data: UserRegister, auth: AuthService = Depends(get_auth_service)):
    """Register a new user account and return an access token."""
    try:
        user_id = auth.register(user_data.email, user_data.password)
    except ValueError as e:
        msg = str(e)
        code = status.HTTP_409_CONFLICT if "exist" in msg.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=msg)

    token = auth.create_session(user_id)
    return _token_response(user_id, user_data.email, token, mfa_enabled=False)


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid credentials"},
        403: {"model": ErrorResponse, "description": "Account disabled or MFA required"},
        429: {"model": ErrorResponse, "description": "Account locked or too many attempts from this IP"},
    },
    dependencies=[Depends(check_login_rate_limit)],
)
async def login(credentials: UserLogin, auth: AuthService = Depends(get_auth_service)):
    """
    Authenticate and return an access token.

    With MFA enabled provide `totp_code` or a one-time `backup_code`.
    The account is locked for 15 minutes after 5 failed attempts.
    """
    result = auth.login(
        credentials.email, credentials.password,
        totp_code=credentials.totp_code, backup_code=credentials.backup_code,
    )
    if result.ok:
        u = result.user
        return _token_response(str(u["user_id"]), u["email"], result.session_token, bool(u.get("mfa_enabled")))

    if result.reason == "locked":
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Account locked due to too many failed attempts. Try again in {LOCKOUT_MINUTES} minutes.",
            headers={"Retry-After": str(LOCKOUT_MINUTES * 60)},
        )
    if result.reason == "inactive":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    if result.reason == "mfa_required":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="MFA code required",
                            headers={"X-MFA-Required": "true"})
    if result.reason == "mfa_invalid":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA code")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(user: Dict = Depends(get_current_user), db: AuthDB = Depends(get_db)):
    """Invalidate the current access token."""
    token = user.get("_session_token")
    if token:
        db.invalidate_session(token)
    logger.info(f"User logged out: {user['email']}")
    return None


@router.post("/logout/all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(user: Dict = Depends(get_current_user), db: AuthDB = Depends(get_db)):
    """Invalidate all sessions of the current user."""
    count = db.invalidate_all_sessions(str(user["user_id"]))
    logger.info(f"User {user['email']} logged out from {count} sessions")
    return None


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(user: Dict = Depends(get_current_user), db: AuthDB = Depends(get_db)):
    full_user = db.get_user_by_id(str(user["user_id"]))
    return UserResponse(
        user_id=str(full_user["user_id"]), email=full_user["email"], mfa_enabled=full_user["mfa_enabled"],
        created_at=full_user["created_at"], last_login=full_user["last_login"],
    )


@router.post(
    "/password/change",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={401: {"model": ErrorResponse, "description": "Current password incorrect"}},
)
async def change_password(
    request: PasswordChangeRequest,
    user: Dict = Depends(get_current_user),
    auth: AuthService = Depends(get_auth_service),
):
    """
    Change the password. Re-wraps the dossier encryption key and invalidates
    all OTHER sessions; the current session stays valid.
    """
    try:
        ok = auth.change_password(
            str(user["user_id"]), request.current_password, request.new_password,
            keep_session_token=user.get("_session_token"),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except DossierKeyError:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Could not rotate the dossier key; password unchanged")
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")
    return None


@router.get("/usage", response_model=UsageStats)
async def get_usage_stats(user: Dict = Depends(get_current_user), db: AuthDB = Depends(get_db)):
    """Token usage statistics for the current month."""
    return UsageStats(**db.get_user_monthly_costs(str(user["user_id"])))


# ============================================
# MFA Management
# ============================================

@router.post("/mfa/setup", response_model=MFASetupResponse)
async def setup_mfa_endpoint(user: Dict = Depends(get_current_user), auth: AuthService = Depends(get_auth_service)):
    """Start MFA setup: returns secret + QR code. Active only after /mfa/verify (10 min window)."""
    user_id = str(user["user_id"])
    try:
        secret, uri, qr_base64 = auth.begin_mfa_setup(user_id, user["email"])
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    store_pending_mfa_secret(user_id, secret)
    return MFASetupResponse(secret=secret, qr_code_base64=qr_base64, provisioning_uri=uri)


@router.post("/mfa/verify", response_model=MFAVerifyResponse)
async def verify_mfa_setup(
    verification: MFAVerifyRequest,
    user: Dict = Depends(get_current_user),
    auth: AuthService = Depends(get_auth_service),
):
    """Confirm the authenticator with a first code; returns one-time backup codes."""
    user_id = str(user["user_id"])
    secret = get_pending_mfa_secret(user_id)
    if not secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No pending MFA setup found. Please call /mfa/setup first.")
    backup_codes = auth.complete_mfa_setup(user_id, secret, verification.totp_code)
    if backup_codes is None:
        store_pending_mfa_secret(user_id, secret)  # allow a retry
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code. Please try again.")
    return MFAVerifyResponse(message="MFA enabled successfully. Store your backup codes securely!", backup_codes=backup_codes)


@router.delete("/mfa", status_code=status.HTTP_204_NO_CONTENT)
async def disable_mfa(
    verification: MFAVerifyRequest,
    user: Dict = Depends(get_current_user),
    auth: AuthService = Depends(get_auth_service),
):
    """Disable MFA (requires a current TOTP code)."""
    try:
        ok = auth.disable_mfa(str(user["user_id"]), verification.totp_code)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code")
    return None
