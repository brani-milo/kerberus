"""
Multi-Factor Authentication (MFA) utilities for KERBERUS.

Implements TOTP (Time-based One-Time Password) using RFC 6238.
Compatible with Google Authenticator, Authy, and other TOTP apps.
"""
import base64
import io
import secrets
from typing import Tuple, Optional

import pyotp
import qrcode


def generate_totp_secret() -> str:
    """
    Generate a new TOTP secret for MFA enrollment.

    Returns:
        Base32-encoded secret (32 characters).
    """
    return pyotp.random_base32()


def get_totp_provisioning_uri(
    secret: str,
    email: str,
    issuer: str = "KERBERUS"
) -> str:
    """
    Generate a provisioning URI for TOTP apps.

    This URI can be encoded as a QR code for easy scanning.

    Args:
        secret: Base32-encoded TOTP secret.
        email: User's email address (displayed in authenticator app).
        issuer: Application name (displayed in authenticator app).

    Returns:
        otpauth:// URI string.
    """
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=issuer)


def generate_qr_code(uri: str) -> bytes:
    """
    Generate a QR code image for the provisioning URI.

    Args:
        uri: otpauth:// provisioning URI.

    Returns:
        PNG image bytes.
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer.read()


def generate_qr_code_base64(uri: str) -> str:
    """
    Generate a base64-encoded QR code for embedding in HTML.

    Args:
        uri: otpauth:// provisioning URI.

    Returns:
        Base64-encoded PNG image string (data URI ready).
    """
    png_bytes = generate_qr_code(uri)
    b64 = base64.b64encode(png_bytes).decode('utf-8')
    return f"data:image/png;base64,{b64}"


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    """
    Verify a TOTP code against the secret.

    Args:
        secret: Base32-encoded TOTP secret.
        code: 6-digit code entered by user.
        window: Number of 30-second windows to allow (default 1 = +-30s).

    Returns:
        True if code is valid, False otherwise.
    """
    if not secret or not code:
        return False

    # Clean the code (remove spaces, only digits)
    code = ''.join(filter(str.isdigit, code))

    if len(code) != 6:
        return False

    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=window)


def get_current_totp(secret: str) -> str:
    """
    Get the current TOTP code (for testing/debugging).

    Args:
        secret: Base32-encoded TOTP secret.

    Returns:
        Current 6-digit TOTP code.
    """
    totp = pyotp.TOTP(secret)
    return totp.now()


def setup_mfa(email: str, issuer: str = "KERBERUS") -> Tuple[str, str, str]:
    """
    Complete MFA setup: generate secret, URI, and QR code.

    Args:
        email: User's email address.
        issuer: Application name.

    Returns:
        Tuple of (secret, provisioning_uri, qr_code_base64).
    """
    secret = generate_totp_secret()
    uri = get_totp_provisioning_uri(secret, email, issuer)
    qr_base64 = generate_qr_code_base64(uri)

    return secret, uri, qr_base64


def generate_backup_codes(count: int = 8, length: int = 8) -> list[str]:
    """
    Generate backup codes for account recovery.

    These should be stored securely by the user and each can only be used once.

    Args:
        count: Number of backup codes to generate.
        length: Length of each code (default 8 characters).

    Returns:
        List of backup codes (uppercase alphanumeric).
    """
    codes = []
    for _ in range(count):
        # Generate cryptographically secure random code
        code = secrets.token_hex(length // 2).upper()
        # Format as XXXX-XXXX for readability
        formatted = f"{code[:4]}-{code[4:]}"
        codes.append(formatted)

    return codes
