"""Application settings.

The backend selector decides whether runs go to the deterministic FakeBackend
(default, for local/CI — no GPU, no credentials) or the live RunpodBackend.

Secrets are never stored here as values. ``runpod_api_key_env`` names the
environment variable the RunpodBackend reads at submit time; the key itself
never enters the DB, logs, browser, or job payloads.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCIABLE_", env_file=".env", extra="ignore")

    # Storage
    db_path: Path = REPO_ROOT / "sciable.db"

    # Backend: "fake" (default) or "runpod"
    backend: str = "fake"

    # Active method id — selects which methods/<id>/manifest.json is served.
    # Both methods live in the registry; this is a config flip, not a code change.
    method: str = "grnade"

    # Secret *reference* only — the name of the env var holding the Runpod key.
    runpod_api_key_env: str = "RUNPOD_API_KEY"
    runpod_base_url: str = "https://api.runpod.ai/v2"
    # The operator's Serverless endpoint id (from the Runpod console). Required
    # when backend == "runpod".
    runpod_endpoint_id: str | None = None
    # Per-call HTTP timeout for Runpod submit/status/cancel.
    runpod_http_timeout_s: float = 30.0

    # Operator access token for the control-plane API. When unset (default),
    # the API is open — intended for a single operator on localhost. When set,
    # API submission and result access require `Authorization: Bearer <token>`.
    operator_token: str | None = None

    # Execution limits applied to every method's deployment. Per-method input
    # bounds (sizes, counts, lengths) live in that method's manifest, not here.
    job_timeout_s: int = 900
    max_in_flight: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
