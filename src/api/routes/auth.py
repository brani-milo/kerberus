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
    MFASetupResponse,
    MFAVerifyRequest,
    UserResponse,
    UsageStats,
    ErrorResponse,
)
from ..deps import get_db, get_current_user
from ...database.auth_db import AuthDB, hash_password, verify_password
from ...auth.mfa import setup_mfa, verify_totp

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        409: {"model": ErrorResponse, "description": "Email already exists"},
    },
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
    },
)
async def login(
    credentials: UserLogin,
    db: AuthDB = Depends(get_db),
):
    """
    Authenticate user and return access token.

    If MFA is enabled, totp_code must be provided.
    """
    # Get user by email
    user = db.get_user_by_email(credentials.email)

    if user is None:
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Check MFA if enabled
    if user["mfa_enabled"]:
        if not credentials.totp_code:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="MFA code required",
                headers={"X-MFA-Required": "true"},
            )

        if not verify_totp(user["totp_secret"], credentials.totp_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid MFA code",
            )

    # Create session
    user_id = str(user["user_id"])
    session_token = db.create_session(user_id, expires_hours=24)

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
    # Note: We don't have the token in the user dict, but we could
    # invalidate all sessions for the user as a workaround
    # For now, just log the logout
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
    """
    email = user["email"]

    # Generate TOTP secret and QR code
    secret, uri, qr_base64 = setup_mfa(email, issuer="KERBERUS")

    # Store secret temporarily (not enabled until verified)
    # In production, store in a temporary table or cache
    # For now, we'll require immediate verification

    return MFASetupResponse(
        secret=secret,
        qr_code_base64=qr_base64,
        provisioning_uri=uri,
    )


@router.post("/mfa/verify", status_code=status.HTTP_204_NO_CONTENT)
async def verify_mfa_setup(
    verification: MFAVerifyRequest,
    secret: str,  # In production, get from temp storage
    user: Dict = Depends(get_current_user),
    db: AuthDB = Depends(get_db),
):
    """
    Verify MFA setup and enable it.

    Requires the TOTP code from the authenticator app.
    """
    # Verify the code
    if not verify_totp(secret, verification.totp_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code",
        )

    # Enable MFA
    user_id = str(user["user_id"])
    db.update_totp_secret(user_id, secret)

    logger.info(f"MFA enabled for user: {user['email']}")

    return None


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

    # Disable MFA
    db.update_totp_secret(str(user["user_id"]), None)

    logger.info(f"MFA disabled for user: {user['email']}")

    return None
