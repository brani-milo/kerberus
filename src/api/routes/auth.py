"""
Authentication Endpoints.

Provides user registration, login, logout, and MFA management.
"""
import logging
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, status

from ..models import (
    UserRegister,
    UserLogin,
    TokenResponse,
    PasswordChangeRequest,
    MFASetupResponse,
    MFAVerifyRequest,
    MFAVerifyResponse,
    UserResponse,
    UsageStats,
    ErrorResponse,
)
from ..deps import (
    get_db,
    get_current_user,
    store_pending_mfa_secret,
    get_pending_mfa_secret,
    clear_pending_mfa_secret,
    check_register_rate_limit,
    check_login_rate_limit,
)
from ...database.auth_db import AuthDB, hash_password, verify_password
from ...auth.mfa import (
    setup_mfa,
    verify_totp,
    generate_backup_codes,
    hash_backup_codes,
    find_matching_backup_code,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])

# Account lockout settings
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


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
async def register(
    user_data: UserRegister,
    db: AuthDB = Depends(get_db),
):
    """
    Register a new user account.

    Returns an access token for immediate use.
    """
    # Validate password strength
    if len(user_data.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 8 characters",
        )

    # Hash password
    password_hash = hash_password(user_data.password)

    # Create user
    try:
        user_id = db.create_user(
            email=user_data.email,
            password_hash=password_hash,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    # Create session
    session_token = db.create_session(user_id, expires_hours=24)

    # Update last login
    db.update_last_login(user_id)

    logger.info(f"New user registered: {user_data.email}")

    return TokenResponse(
        access_token=session_token,
        token_type="bearer",
        expires_in=24 * 3600,
        user_id=user_id,
        email=user_data.email,
        mfa_enabled=False,
    )


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
async def login(
    credentials: UserLogin,
    db: AuthDB = Depends(get_db),
):
    """
    Authenticate user and return access token.

    If MFA is enabled, provide either:
    - totp_code: 6-digit code from authenticator app
    - backup_code: One-time recovery code (format: XXXX-XXXX)

    Backup codes are consumed on use and cannot be reused.

    Account is locked for 15 minutes after 5 failed login attempts.
    """
    # Check for account lockout before processing
    failed_count = db.get_failed_login_count(credentials.email, LOCKOUT_MINUTES)
    if failed_count >= MAX_FAILED_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Account locked due to too many failed attempts. Try again in {LOCKOUT_MINUTES} minutes.",
            headers={"Retry-After": str(LOCKOUT_MINUTES * 60)},
        )

    # Get user by email
    user = db.get_user_by_email(credentials.email)

    if user is None:
        db.record_failed_login(credentials.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Check if account is active
    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    # Verify password
    if not verify_password(credentials.password, user["password_hash"]):
        db.record_failed_login(credentials.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Check MFA if enabled
    if user["mfa_enabled"]:
        if not credentials.totp_code and not credentials.backup_code:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="MFA code required",
                headers={"X-MFA-Required": "true"},
            )

        mfa_valid = False
        user_id = str(user["user_id"])

        # Try TOTP code first
        if credentials.totp_code:
            if verify_totp(user["totp_secret"], credentials.totp_code):
                mfa_valid = True

        # Try backup code if TOTP not provided or failed
        if not mfa_valid and credentials.backup_code:
            hashed_codes = db.get_backup_codes(user_id)
            code_index = find_matching_backup_code(credentials.backup_code, hashed_codes)
            if code_index is not None:
                # Remove the used backup code
                db.remove_backup_code(user_id, code_index)
                mfa_valid = True
                logger.info(f"Backup code used for login: {credentials.email}")

        if not mfa_valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid MFA code",
            )

    # Create session
    user_id = str(user["user_id"])
    session_token = db.create_session(user_id, expires_hours=24)

    # Clear failed login attempts on successful login
    db.clear_failed_logins(credentials.email)

    # Update last login
    db.update_last_login(user_id)

    logger.info(f"User logged in: {credentials.email}")

    return TokenResponse(
        access_token=session_token,
        token_type="bearer",
        expires_in=24 * 3600,
        user_id=user_id,
        email=user["email"],
        mfa_enabled=user["mfa_enabled"],
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Logout current session.

    Invalidates the current access token.
    """
    token = user.get("_session_token")
    if token:
        db.invalidate_session(token)
    logger.info(f"User logged out: {user['email']}")

    return None


@router.post("/logout/all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Logout from all devices.

    Invalidates all sessions for the current user.
    """
    user_id = str(user["user_id"])
    count = db.invalidate_all_sessions(user_id)

    logger.info(f"User {user['email']} logged out from {count} sessions")

    return None


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Get current user profile.
    """
    full_user = db.get_user_by_id(str(user["user_id"]))

    return UserResponse(
        user_id=str(full_user["user_id"]),
        email=full_user["email"],
        mfa_enabled=full_user["mfa_enabled"],
        created_at=full_user["created_at"],
        last_login=full_user["last_login"],
    )


@router.post(
    "/password/change",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse, "description": "Current password incorrect"},
    },
)
async def change_password(
    request: PasswordChangeRequest,
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Change the current user's password.

    Requires the current password for verification.
    Invalidates all other sessions for security.
    """
    user_id = str(user["user_id"])
    full_user = db.get_user_by_id(user_id)

    if not verify_password(request.current_password, full_user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    # Update password
    new_hash = hash_password(request.new_password)
    db.update_password(user_id, new_hash)

    # Invalidate all other sessions for security (keep current session active)
    current_token = user.get("_session_token")
    db.invalidate_all_sessions(user_id)

    # Re-create the current session so user stays logged in
    if current_token:
        db.create_session(user_id, expires_hours=24)

    logger.info(f"Password changed for user: {user['email']}")

    return None


@router.get("/usage", response_model=UsageStats)
async def get_usage_stats(
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Get current user's token usage statistics for this month.
    """
    user_id = str(user["user_id"])
    stats = db.get_user_monthly_costs(user_id)

    return UsageStats(**stats)


# ============================================
# MFA Management
# ============================================

@router.post("/mfa/setup", response_model=MFASetupResponse)
async def setup_mfa_endpoint(
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Initialize MFA setup.

    Returns a QR code and secret for authenticator app setup.
    MFA is not active until verified with /mfa/verify.

    The secret is stored temporarily (10 minutes) until verification.
    """
    user_id = str(user["user_id"])
    email = user["email"]

    # Check if MFA is already enabled
    full_user = db.get_user_by_id(user_id)
    if full_user and full_user["mfa_enabled"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already enabled. Disable it first to set up a new authenticator.",
        )

    # Generate TOTP secret and QR code
    secret, uri, qr_base64 = setup_mfa(email, issuer="KERBERUS")

    # Store secret temporarily in Redis (expires in 10 minutes)
    store_pending_mfa_secret(user_id, secret)

    logger.info(f"MFA setup initiated for user: {email}")

    return MFASetupResponse(
        secret=secret,
        qr_code_base64=qr_base64,
        provisioning_uri=uri,
    )


@router.post("/mfa/verify", response_model=MFAVerifyResponse)
async def verify_mfa_setup(
    verification: MFAVerifyRequest,
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Verify MFA setup and enable it.

    Requires the TOTP code from the authenticator app.
    Returns backup codes for account recovery - store these securely!
    """
    user_id = str(user["user_id"])

    # Retrieve pending secret from Redis
    secret = get_pending_mfa_secret(user_id)

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending MFA setup found. Please call /mfa/setup first.",
        )

    # Verify the code
    if not verify_totp(secret, verification.totp_code):
        # Store the secret back so user can retry
        store_pending_mfa_secret(user_id, secret)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code. Please try again.",
        )

    # Enable MFA
    db.update_totp_secret(user_id, secret)

    # Generate and store backup codes
    backup_codes = generate_backup_codes(count=8)
    hashed_codes = hash_backup_codes(backup_codes)
    db.store_backup_codes(user_id, hashed_codes)

    logger.info(f"MFA enabled for user: {user['email']}")

    return MFAVerifyResponse(
        message="MFA enabled successfully. Store your backup codes securely!",
        backup_codes=backup_codes,
    )


@router.delete("/mfa", status_code=status.HTTP_204_NO_CONTENT)
async def disable_mfa(
    verification: MFAVerifyRequest,
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Disable MFA for the current user.

    Requires current TOTP code for verification.
    """
    # Get full user to check MFA status
    full_user = db.get_user_by_id(str(user["user_id"]))

    if not full_user["mfa_enabled"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is not enabled",
        )

    # Verify the code
    if not verify_totp(full_user["totp_secret"], verification.totp_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code",
        )

    # Disable MFA and clear backup codes
    user_id = str(user["user_id"])
    db.update_totp_secret(user_id, None)
    db.store_backup_codes(user_id, [])  # Clear backup codes

    logger.info(f"MFA disabled for user: {user['email']}")

    return None
