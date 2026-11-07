"""Seed method releases and their deployments.

For each method in the registry the control plane bootstraps one immutable
MethodRelease (from the manifest) and one Deployment that points the release at
an endpoint. For the fake backend the endpoint is a placeholder; the credential
is stored only as a *reference* (an env-var name).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.config import Settings
from app.db import get_conn
from app.manifest import Manifest


def _now() -> str:
    return datetime.now(UTC).isoformat()


def deployment_id_for(settings: Settings, manifest: Manifest) -> str:
    """Stable per-method, per-backend deployment id."""
    suffix = "local-fake" if settings.backend == "fake" else settings.backend
    return f"{manifest.method_id}-{suffix}"


def ensure_seeded(settings: Settings, manifest: Manifest) -> str:
    """Idempotently insert the release + deployment. Returns the deployment id."""
    release = manifest.to_release(created_at=_now())
    exec_limits = {
        "job_timeout_s": settings.job_timeout_s,
        "max_in_flight": settings.max_in_flight,
        **dict(manifest.limits),
    }
    deployment_id = deployment_id_for(settings, manifest)
    with get_conn(settings.db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO method_release
                (id, version, input_schema, output_schema, source_digest,
                 checkpoint_digest, worker_image_digest, exec_requirements,
                 example_ref, citation, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                release.id,
                release.version,
                json.dumps(release.input_schema),
                json.dumps(release.output_schema),
                release.source_digest,
                release.checkpoint_digest,
                release.worker_image_digest,
                json.dumps(release.exec_requirements),
                release.example_ref,
                release.citation,
                release.created_at,
            ),
        )
        endpoint_ref = _endpoint_ref(settings, manifest)
        conn.execute(
            """
            INSERT OR IGNORE INTO deployment
                (id, method_release_id, endpoint_ref, exec_limits,
                 credential_ref, created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (
                deployment_id,
                release.id,
                endpoint_ref,
                json.dumps(exec_limits),
                settings.runpod_api_key_env,
                _now(),
            ),
        )
        # The operator's endpoint mapping lives in config; a deployment seeded
        # under an older config must follow it, or runs keep routing to a stale
        # endpoint (whose image may silently serve this method's STUB).
        conn.execute(
            "UPDATE deployment SET endpoint_ref = ? WHERE id = ? AND endpoint_ref <> ?",
            (endpoint_ref, deployment_id, endpoint_ref),
        )
    return deployment_id


def ensure_all_seeded(settings: Settings, manifests: dict[str, Manifest]) -> dict[str, str]:
    """Seed every method in the registry. Returns {method_id: deployment_id}."""
    return {mid: ensure_seeded(settings, m) for mid, m in manifests.items()}


def _endpoint_ref(settings: Settings, manifest: Manifest) -> str:
    if settings.backend == "fake":
        return "fake-endpoint"
    if settings.backend == "runpod":
        return settings.runpod_endpoint_for(manifest.method_id) or "PENDING-ENDPOINT-ID"
    return "unknown"
