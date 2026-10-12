"""Load settings from environment variables.

A local .env file is read for development; in Docker the same values are
injected as real environment variables by the env_file setting in compose.
Secrets are only ever read here and never logged.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Every setting the service needs."""

    resend_api_key: str
    internal_token: str
    database_url: str
    default_from: str


def _required(name):
    """Read one environment variable and fail loudly when it is missing."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_settings():
    """Read all settings from the environment."""
    return Settings(
        resend_api_key=_required("RESEND_API_KEY"),
        internal_token=_required("INTERNAL_TOKEN"),
        database_url=_required("DATABASE_URL"),
        default_from=_required("DEFAULT_FROM"),
    )
