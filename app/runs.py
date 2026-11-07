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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.backends.base import Backend, JobSpec, SubmitStatus
from app.config import Settings
from app.db import get_conn
from app.fingerprint import input_fingerprint
from app.manifest import Manifest
from app.models import TERMINAL_STATES, Run, RunState
from app.validation import ValidationError, validate_submission

#: error_category sentinel for a submission whose provider outcome is unknown.
UNCERTAIN_SUBMISSION = "submit_uncertain"


class RunConflictError(Exception):
    """Raised when the deployment's in-flight cap would be exceeded."""


class RunNotCancelableError(Exception):
    """Raised when canceling a run that is already terminal."""


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
    def __init__(
        self,
        settings: Settings,
        manifests: dict[str, Manifest] | Manifest,
        backend: Backend,
    ) -> None:
        self.settings = settings
        # Accept a single manifest (single-method callers/tests) or a registry.
        if isinstance(manifests, Manifest):
            manifests = {manifests.method_id: manifests}
        self.manifests = manifests
        self.backend = backend
        self._outputs_dir = Path(settings.db_path).parent / "runs"

    def _manifest_for_release(self, release_id: str) -> Manifest:
        method_id = release_id.split("@", 1)[0]
        manifest = self.manifests.get(method_id)
        if manifest is None:
            raise ValidationError("unknown_method", f"No manifest for method '{method_id}'.")
        return manifest

    # -- queries ---------------------------------------------------------------

    def get_run(self, run_id: str) -> Run | None:
        with get_conn(self.settings.db_path) as conn:
            row = conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def _get_by_idempotency(self, conn, key: str) -> Run | None:
        row = conn.execute("SELECT * FROM run WHERE idempotency_key = ?", (key,)).fetchone()
        return _row_to_run(row) if row else None

    def _expire_uncertain(self, conn, *, run_id: str | None = None) -> None:
        """Mark stale UNCERTAIN submissions (no provider job id) as FAILED.

        Such a run has nothing to poll, so without this it would stay QUEUED
        and hold an in-flight slot forever. ``error_category`` is kept as
        ``submit_uncertain`` so the cause stays visible. Never resubmits."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.uncertain_submit_expiry_s)
        query = (
            "SELECT id, submitted_at FROM run WHERE state = ? AND error_category = ? "
            "AND provider_job_id IS NULL"
        )
        params: list[Any] = [RunState.QUEUED.value, UNCERTAIN_SUBMISSION]
        if run_id is not None:
            query += " AND id = ?"
            params.append(run_id)
        now = _now()
        for row in conn.execute(query, params).fetchall():
            submitted = row["submitted_at"]
            if submitted and datetime.fromisoformat(submitted) > cutoff:
                continue
            conn.execute(
                "UPDATE run SET state = ?, finished_at = ? WHERE id = ? AND state = ?",
                (RunState.FAILED.value, now, row["id"], RunState.QUEUED.value),
            )

    def _in_flight_count(self, conn, deployment_id: str) -> int:
        self._expire_uncertain(conn)
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
            manifest = self._manifest_for_release(release_id)

            validated = validate_submission(manifest, raw=raw)
            fp = input_fingerprint(release_id, validated)
            # Decide the idempotency key. An explicit client key is always honored
            # (so a retry cannot double-charge a GPU job). Absent one, we only reuse
            # a prior run when dedupe_identical_inputs is on; by default every
            # submission runs fresh — the common case in development, where the same
            # input is deliberately run again. The default key is scoped to the
            # deployment so a run from another backend (e.g. the fake demo) never
            # stands in for a real one.
            if idempotency_key:
                idem: str | None = idempotency_key
            elif self.settings.dedupe_identical_inputs:
                idem = f"{deployment_id}:{fp}"
            else:
                idem = None  # no dedupe — this submission gets its own run

            if idem is not None:
                existing = self._get_by_idempotency(conn, idem)
                if existing is not None:
                    if existing.state not in (RunState.FAILED, RunState.CANCELED):
                        return existing  # dedupe — no second submission
                    # A failed/canceled run must not block a retry: retire its key.
                    conn.execute(
                        "UPDATE run SET idempotency_key = ? WHERE id = ?",
                        (f"{idem}:retired:{existing.id}", existing.id),
                    )

            max_in_flight = int(exec_limits.get("max_in_flight", self.settings.max_in_flight))
            if self._in_flight_count(conn, deployment_id) >= max_in_flight:
                raise RunConflictError(
                    f"Deployment '{deployment_id}' already has {max_in_flight} job(s) in flight."
                )

            run_id = uuid.uuid4().hex
            job = JobSpec(
                fingerprint=fp,
                seed=validated.seed,
                method_id=manifest.method_id,
                output_kind=manifest.outputs.kind,
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
        with get_conn(self.settings.db_path) as conn:
            self._expire_uncertain(conn, run_id=run_id)
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
        elif status.state is RunState.FAILED and status.raw:
            # Keep the worker's failure detail (the runner's stderr tail) so the run
            # is debuggable in-app, not only in the provider's live logs.
            self._write_raw(run_id, status.raw)

        finished_at = _now() if status.state in TERMINAL_STATES else None
        with get_conn(self.settings.db_path) as conn:
            conn.execute(
                "UPDATE run SET state = ?, error_category = ?, outputs_ref = ?, "
                "finished_at = COALESCE(?, finished_at) WHERE id = ?",
                (status.state.value, status.error_category, outputs_ref, finished_at, run_id),
            )
        return self.get_run(run_id)

    # -- cancel ----------------------------------------------------------------

    def cancel_run(self, run_id: str) -> tuple[Run, bool | None] | None:
        """Move a non-terminal run to CANCELED, freeing its in-flight slot.

        If the run has a provider job, the backend is asked to cancel it too.
        Returns ``(run, provider_canceled)`` where ``provider_canceled`` is the
        backend's answer, or None when there was no provider job to cancel.
        The local state is CANCELED either way: a failed provider cancel must
        not leave the deployment blocked. Raises RunNotCancelableError for
        terminal runs; returns None for unknown ids."""
        run = self.get_run(run_id)
        if run is None:
            return None
        if run.state in TERMINAL_STATES:
            raise RunNotCancelableError(f"Run '{run_id}' is already {run.state.value}.")

        provider_canceled: bool | None = None
        if run.provider_job_id is not None:
            provider_canceled = self.backend.cancel(run.provider_job_id)

        terminal = tuple(s.value for s in TERMINAL_STATES)
        placeholders = ",".join("?" for _ in terminal)
        with get_conn(self.settings.db_path) as conn:
            cur = conn.execute(
                f"UPDATE run SET state = ?, finished_at = ? "
                f"WHERE id = ? AND state NOT IN ({placeholders})",
                (RunState.CANCELED.value, _now(), run_id, *terminal),
            )
        if cur.rowcount == 0:
            # Became terminal concurrently (e.g. a poll landed first).
            raise RunNotCancelableError(f"Run '{run_id}' is already terminal.")
        return self.get_run(run_id), provider_canceled

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

    def read_raw(self, run_id: str) -> dict | None:
        """The worker's raw output/detail for a run, from the canonical path.

        Works for failed runs too (which have no ``outputs_ref`` to derive a dir
        from), so the failure detail is retrievable, not only the success case."""
        path = self.raw_output_path(run_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
