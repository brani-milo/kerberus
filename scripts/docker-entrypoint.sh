#!/bin/bash
# Docker entrypoint script
# Reads secrets from files and exports them as environment variables

if [ -f "/run/secrets/chainlit_auth_secret" ]; then
    export CHAINLIT_AUTH_SECRET=$(cat /run/secrets/chainlit_auth_secret)
    export JWT_SECRET=$(cat /run/secrets/chainlit_auth_secret)
fi
if [ -f "/run/secrets/llm_api_key" ]; then
    export LLM_API_KEY=$(cat /run/secrets/llm_api_key)
fi
if [ -f "/run/secrets/postgres_password" ]; then
    export POSTGRES_PASSWORD=$(cat /run/secrets/postgres_password)
fi
if [ -f "/run/secrets/conversation_encryption_key" ]; then
    export CONVERSATION_ENCRYPTION_KEY=$(cat /run/secrets/conversation_encryption_key)
fi

exec "$@"
