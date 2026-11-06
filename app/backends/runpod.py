"""Runpod Serverless backend.

Implements the narrow Backend contract against the Runpod Serverless HTTP API:

    POST   {base}/{endpoint_id}/run            -> {"id", "status"}
    GET    {base}/{endpoint_id}/status/{job}   -> {"id", "status", "output"}
    POST   {base}/{endpoint_id}/cancel/{job}

Auth is ``Authorization: Bearer <key>``, where the key is read at call time from
the env var named by ``settings.runpod_api_key_env`` — it is never stored in the
DB, logs, or job payloads.

A submit that times out or fails to connect is reported as UNCERTAIN (the provider
may or may not have accepted the job); the run service surfaces that and never
auto-resubmits, avoiding a second GPU charge.
"""

from __future__ import annotations

import os

import httpx

from app.backends.base import JobSpec, JobStatus, SubmitResult, SubmitStatus
from app.config import Settings
from app.models import RunState

# Runpod job status -> our RunState (+ optional error category).
_STATUS_MAP: dict[str, tuple[RunState, str | None]] = {
    "IN_QUEUE": (RunState.QUEUED, None),
    "IN_PROGRESS": (RunState.RUNNING, None),
    "COMPLETED": (RunState.SUCCEEDED, None),
    "FAILED": (RunState.FAILED, "worker_error"),
    "CANCELLED": (RunState.CANCELED, None),
    "TIMED_OUT": (RunState.FAILED, "timeout"),
}


class BackendError(Exception):
    """A definite backend failure (bad config, auth, or provider rejection)."""


class RunpodBackend:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not settings.runpod_endpoint_id:
            raise BackendError("SCIABLE_RUNPOD_ENDPOINT_ID is required for the runpod backend.")
        api_key = os.environ.get(settings.runpod_api_key_env)
        if not api_key:
            raise BackendError(
                f"Runpod API key not found in env var '{settings.runpod_api_key_env}'."
            )
        self._endpoint = settings.runpod_endpoint_id
        self._base = f"{settings.runpod_base_url.rstrip('/')}/{self._endpoint}"
        self._client = client or httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=settings.runpod_http_timeout_s,
        )

    def submit(self, job: JobSpec) -> SubmitResult:
        payload = {
            "input": {
                "method": job.method_id,
                "inputs": job.inputs,
            }
        }
        try:
            resp = self._client.post(f"{self._base}/run", json=payload)
        except (httpx.TimeoutException, httpx.TransportError):
            # The request may or may not have reached Runpod — do not resubmit.
            return SubmitResult(status=SubmitStatus.UNCERTAIN)

        if resp.status_code in (401, 403):
            raise BackendError("Runpod rejected the API key (check scope/validity).")
        if resp.status_code >= 400:
            raise BackendError(
                f"Runpod /run failed: HTTP {resp.status_code}: {resp.text[:300]}"
            )

        job_id = resp.json().get("id")
        if not job_id:
            raise BackendError("Runpod /run returned no job id.")
        return SubmitResult(status=SubmitStatus.ACCEPTED, provider_job_id=job_id)

    def status(self, provider_job_id: str) -> JobStatus:
        resp = self._client.get(f"{self._base}/status/{provider_job_id}")
        if resp.status_code == 404:
            # Job TTL expired or unknown id.
            return JobStatus(state=RunState.FAILED, error_category="expired_or_unknown")
        if resp.status_code >= 400:
            raise BackendError(f"Runpod /status failed: HTTP {resp.status_code}.")

        data = resp.json()
        raw_status = data.get("status", "")
        state, error_category = _STATUS_MAP.get(raw_status, (RunState.RUNNING, None))

        output = data.get("output") or {}
        outputs: dict = {}
        raw: dict = {}
        if state is RunState.SUCCEEDED and isinstance(output, dict):
            if output.get("error"):
                # Worker reported a handled failure inside a COMPLETED job; keep
                # the detail (stderr tail) in raw so the failure is debuggable.
                return JobStatus(
                    state=RunState.FAILED,
                    error_category=str(output["error"]),
                    raw={"backend": "runpod", "detail": output.get("detail")},
                )
            outputs = dict(output.get("outputs") or {})
            raw = {"backend": "runpod", **{k: v for k, v in output.items() if k != "outputs"}}
        return JobStatus(
            state=state, outputs=outputs, error_category=error_category, raw=raw
        )

    def cancel(self, provider_job_id: str) -> bool:
        try:
            resp = self._client.post(f"{self._base}/cancel/{provider_job_id}")
        except (httpx.TimeoutException, httpx.TransportError):
            return False
        return resp.status_code < 400
