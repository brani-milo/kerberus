"""Single source of truth for the API version string."""
import os

API_VERSION = os.getenv("APP_VERSION", "0.3.0")
