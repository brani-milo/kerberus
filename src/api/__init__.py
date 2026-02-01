"""
KERBERUS REST API.

FastAPI-based REST API for the legal intelligence platform.
"""
from .main import app, create_app

__all__ = ["app", "create_app"]
