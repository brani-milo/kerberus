"""
Authentication and authorization for KERBERUS.

This package provides:
- User authentication (password + MFA)
- Session management
- Rate limiting
- JWT token handling
"""
from .mfa import (
    generate_totp_secret,
    get_totp_provisioning_uri,
    verify_totp,
    setup_mfa,
    generate_backup_codes,
    generate_qr_code_base64,
)

__all__ = [
    "generate_totp_secret",
    "get_totp_provisioning_uri",
    "verify_totp",
    "setup_mfa",
    "generate_backup_codes",
    "generate_qr_code_base64",
]
