#!/bin/bash
# KERBERUS - SQLCipher Installation Script for macOS
# This script installs the system-level sqlcipher and the Python bindings.

set -e

echo "🛡️  Checking for Homebrew..."
if ! command -v brew &> /dev/null; then
    echo "❌ Homebrew is not installed. Please install it from https://brew.sh/"
    exit 1
fi

echo "📦 Installing system-level sqlcipher via Homebrew..."
brew install sqlcipher

# Determine Homebrew prefix (usually /opt/homebrew on Apple Silicon)
BREW_PREFIX=$(brew --prefix)
SQLCIPHER_PATH="$BREW_PREFIX/opt/sqlcipher"

echo "🔗 Setting environment variables for compilation..."
export LDFLAGS="-L$SQLCIPHER_PATH/lib"
export CPPFLAGS="-I$SQLCIPHER_PATH/include"

echo "🐍 Installing sqlcipher3 Python bindings..."
# We use sqlcipher3 instead of pysqlcipher3 for Python 3.13 compatibility
pip install sqlcipher3>=0.6.2 --no-cache-dir

echo "✅ SQLCipher installation complete!"
echo "You can now use encrypted dossiers in KERBERUS."
