"""Run lifecycle service.

Responsibilities:
- Validate inputs against the manifest and compute the input fingerprint.
- Deduplicate via the idempotency key (identical resubmission -> existing run,
  never a second billed job).
- Enforce the deployment's max-in-flight cap.
- Submit to the backend and persist the resulting run; surface UNCERTAIN
  submissions without auto-resubmitting.
- Poll the backend and advance run state; store outputs on success.

The service is method-agnostic: inputs and outputs flow as generic dicts shaped
by the active manifest, so a new method needs no change here.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.backends.base import Backend, JobSpec, SubmitStatus
from app.config import Settings
from app.db import get_conn
from app.fingerprint import input_fingerprint, resolve_idempotency_key
from app.manifest import Manifest
from app.models import TERMINAL_STATES, Run, RunState
from app.validation import ValidationError, validate_submission

#: error_category sentinel for a submission whose provider outcome is unknown.
UNCERTAIN_SUBMISSION = "submit_uncertain"


class RunConflictError(Exception):
    """Raised when the deployment's in-flight cap would be exceeded."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_run(row) -> Run:
    return Run(
        id=row["id"],
        deployment_id=row["deployment_id"],
        resolved_release_id=row["resolved_release_id"],
        state=RunState(row["state"]),
        input_fingerprint=row["input_fingerprint"],
        idempotency_key=row["idempotency_key"],
        seed=row["seed"],
        inputs=json.loads(row["inputs"]),
        provider_job_id=row["provider_job_id"],
        submitted_at=row["submitted_at"],
        finished_at=row["finished_at"],
        error_category=row["error_category"],
        outputs_ref=row["outputs_ref"],
    )


class RunService:
    def __init__(self, settings: Settings, manifest: Manifest, backend: Backend) -> None:
        self.settings = settings
        self.manifest = manifest
        self.backend = backend
        self._outputs_dir = Path(settings.db_path).parent / "runs"

    # -- queries ---------------------------------------------------------------

    def get_run(self, run_id: str) -> Run | None:
        with get_conn(self.settings.db_path) as conn:
            row = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def _get_by_idempotency(self, conn, key: str) -> Run | None:
        row = conn.execute("SELECT * FROM run WHERE idempotency_key = ?", (key,)).fetchone()
        return _row_to_run(row) if row else None

    def _in_flight_count(self, conn, deployment_id: str) -> int:
        terminal = tuple(s.value for s in TERMINAL_STATES)
        placeholders = ",".join("?" for _ in terminal)
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM run "
            f"WHERE deployment_id = ? AND state NOT IN ({placeholders})",
            (deployment_id, *terminal),
        ).fetchone()
        return row["n"]

    # -- create ----------------------------------------------------------------

    def create_run(
        self,
        *,
        deployment_id: str,
        raw: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> Run:
        """Validate, dedupe, submit, and persist. ``raw`` maps the manifest's
        input field names to their provided values. Raises ValidationError /
        RunConflictError, which the API maps to HTTP responses."""
        with get_conn(self.settings.db_path) as conn:
            dep = conn.execute(
                "SELECT * FROM deployment WHERE id = ?", (deployment_id,)
            ).fetchone()
            if dep is None:
                raise ValidationError("unknown_deployment", f"No deployment '{deployment_id}'.")
            release_id = dep["method_release_id"]
            exec_limits = json.loads(dep["exec_limits"])

            validated = validate_submission(self.manifest, raw=raw)
            fp = input_fingerprint(release_id, validated)
            idem = resolve_idempotency_key(fp, idempotency_key)

            existing = self._get_by_idempotency(conn, idem)
            if existing is not None:
                return existing  # dedupe — no second submission

            max_in_flight = int(exec_limits.get("max_in_flight", self.settings.max_in_flight))
            if self._in_flight_count(conn, deployment_id) >= max_in_flight:
                raise RunConflictError(
                    f"Deployment '{deployment_id}' already has {max_in_flight} job(s) in flight."
                )

            run_id = uuid.uuid4().hex
            job = JobSpec(
                fingerprint=fp,
                seed=validated.seed,
                method_id=self.manifest.method_id,
                output_kind=self.manifest.outputs.kind,
                inputs=validated.values,
                endpoint_ref=dep["endpoint_ref"],
            )
            result = self.backend.submit(job)

            if result.status is SubmitStatus.UNCERTAIN:
                state, provider_id, error_cat = RunState.QUEUED, None, UNCERTAIN_SUBMISSION
            else:
                state, provider_id, error_cat = RunState.QUEUED, result.provider_job_id, None

            conn.execute(
                """
                INSERT INTO run
                    (id, deployment_id, resolved_release_id, state, input_fingerprint,
                     idempotency_key, seed, inputs, provider_job_id, submitted_at,
                     finished_at, error_category, outputs_ref)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    deployment_id,
                    release_id,
                    state.value,
                    fp,
                    idem,
                    validated.seed,
                    json.dumps(validated.values),
                    provider_id,
                    _now(),
                    None,
                    error_cat,
                    None,
                ),
            )
            row = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(row)

    # -- poll ------------------------------------------------------------------

    def poll_run(self, run_id: str) -> Run | None:
        """Advance a run by querying the backend. No-op for terminal runs or
        uncertain submissions (which have no provider job to poll)."""
        run = self.get_run(run_id)
        if run is None:
            return None
        if run.state in TERMINAL_STATES or run.provider_job_id is None:
            return run

        status = self.backend.status(run.provider_job_id)
        outputs_ref = run.outputs_ref
        if status.state is RunState.SUCCEEDED and status.outputs:
            outputs_ref = self._write_outputs(run_id, status.outputs)
            if status.raw:
                self._write_raw(run_id, status.raw)

        finished_at = _now() if status.state in TERMINAL_STATES else None
        with get_conn(self.settings.db_path) as conn:
            conn.execute(
                "UPDATE run SET state = ?, error_category = ?, outputs_ref = ?, "
                "finished_at = COALESCE(?, finished_at) WHERE id = ?",
                (status.state.value, status.error_category, outputs_ref, finished_at, run_id),
            )
        return self.get_run(run_id)

    def _write_outputs(self, run_id: str, outputs: dict) -> str:
        self._outputs_dir.mkdir(parents=True, exist_ok=True)
        path = self._outputs_dir / f"{run_id}.outputs.json"
        path.write_text(json.dumps(outputs, indent=2, sort_keys=True))
        return str(path)

    def _write_raw(self, run_id: str, raw: dict) -> str:
        self._outputs_dir.mkdir(parents=True, exist_ok=True)
        path = self._outputs_dir / f"{run_id}.raw.json"
        path.write_text(json.dumps(raw, indent=2, sort_keys=True))
        return str(path)

    def raw_output_path(self, run_id: str) -> Path:
        return self._outputs_dir / f"{run_id}.raw.json"
