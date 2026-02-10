"""
Secrets management utilities for KERBERUS.

Supports multiple secret sources:
1. Environment variables (development)
2. Docker secrets files (production)
3. Infomaniak environment variables (cloud)

Usage:
    from src.utils.secrets import get_secret

    # Automatically checks SECRET_FILE env var, then SECRET env var
    db_password = get_secret("POSTGRES_PASSWORD")
"""
import os
import logging
from typing import Optional
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Get a secret value from various sources.

    Priority:
    1. {NAME}_FILE environment variable (path to file containing secret)
    2. {NAME} environment variable (direct value)
    3. /run/secrets/{name.lower()} file (Docker secrets default path)
    4. Default value

    Args:
        name: Secret name (e.g., "POSTGRES_PASSWORD")
        default: Default value if secret not found

    Returns:
        Secret value or default

    Example:
        # If POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password exists:
        password = get_secret("POSTGRES_PASSWORD")  # reads from file

        # If only POSTGRES_PASSWORD=mypass exists:
        password = get_secret("POSTGRES_PASSWORD")  # returns "mypass"
    """
    # 1. Check for _FILE variant (Docker secrets pattern)
    file_env = f"{name}_FILE"
    file_path = os.environ.get(file_env)

    if file_path and os.path.isfile(file_path):
        try:
            with open(file_path, 'r') as f:
                secret = f.read().strip()
                logger.debug(f"Loaded secret {name} from file")
                return secret
        except Exception as e:
            logger.warning(f"Failed to read secret file {file_path}: {e}")

    # 2. Check direct environment variable
    env_value = os.environ.get(name)
    if env_value:
        logger.debug(f"Loaded secret {name} from environment")
        return env_value

    # 3. Check Docker secrets default path
    docker_secret_path = f"/run/secrets/{name.lower()}"
    if os.path.isfile(docker_secret_path):
        try:
            with open(docker_secret_path, 'r') as f:
                secret = f.read().strip()
                logger.debug(f"Loaded secret {name} from Docker secrets")
                return secret
        except Exception as e:
            logger.warning(f"Failed to read Docker secret {docker_secret_path}: {e}")

    # 4. Return default
    if default is None:
        logger.warning(f"Secret {name} not found, no default provided")
    return default


def get_required_secret(name: str) -> str:
    """
    Get a required secret, raising an error if not found.

    Args:
        name: Secret name

    Returns:
        Secret value

    Raises:
        ValueError: If secret not found
    """
    value = get_secret(name)
    if value is None:
        raise ValueError(
            f"Required secret '{name}' not found. "
            f"Set {name} or {name}_FILE environment variable."
        )
    return value


# Common secrets accessors
def get_postgres_password() -> str:
    """Get PostgreSQL password."""
    return get_required_secret("POSTGRES_PASSWORD")


def get_llm_api_key() -> Optional[str]:
    """Get LLM API key (Infomaniak/Mistral/OpenAI)."""
    return (
        get_secret("LLM_API_KEY") or
        get_secret("INFOMANIAK_API_KEY") or
        get_secret("MISTRAL_API_KEY")
    )


def get_chainlit_auth_secret() -> str:
    """Get Chainlit authentication secret."""
    return get_required_secret("CHAINLIT_AUTH_SECRET")


def mask_secret(secret: str, visible_chars: int = 4) -> str:
    """
    Mask a secret for safe logging.

    Args:
        secret: The secret to mask
        visible_chars: Number of characters to show at start and end

    Returns:
        Masked string like "abc...xyz"
    """
    if not secret or len(secret) <= visible_chars * 2:
        return "***"
    return f"{secret[:visible_chars]}...{secret[-visible_chars:]}"
