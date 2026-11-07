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
    # Optional per-method endpoint override: {method_id: endpoint_id}. The active
    # method resolves to runpod_endpoint_ids[method] if present, else
    # runpod_endpoint_id. Lets one operator run several methods, each on its own
    # endpoint/image, so the single global id can't silently route a method to the
    # wrong worker image (which fails with "unknown_method").
    runpod_endpoint_ids: dict[str, str] = {}
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
    # A run whose provider submit was UNCERTAIN (no provider job id) is marked
    # FAILED once it is older than this, so it cannot hold an in-flight slot
    # forever. It is never resubmitted.
    uncertain_submit_expiry_s: int = 600

    # When True, an identical resubmission (same deployment + inputs + seed)
    # returns the EXISTING run instead of launching a new job — input-fingerprint
    # "caching". Off by default: every submission runs fresh. An explicit client
    # Idempotency-Key is honored regardless of this flag, so retries still cannot
    # double-charge a GPU job.
    dedupe_identical_inputs: bool = False

    def runpod_endpoint_for(self, method_id: str) -> str | None:
        """Endpoint id for a method: its per-method override, else the global id."""
        return self.runpod_endpoint_ids.get(method_id) or self.runpod_endpoint_id


@lru_cache
def get_settings() -> Settings:
    return Settings()
