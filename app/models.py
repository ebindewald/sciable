"""Pydantic models for the three records and the run state machine."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


TERMINAL_STATES = {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELED}


class MethodRelease(BaseModel):
    id: str
    version: str
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    source_digest: str
    checkpoint_digest: str
    worker_image_digest: str
    exec_requirements: dict = Field(default_factory=dict)
    example_ref: str | None = None
    citation: str
    created_at: str


class Deployment(BaseModel):
    id: str
    method_release_id: str
    endpoint_ref: str
    exec_limits: dict = Field(default_factory=dict)
    # Name/id of the secret, never the secret value.
    credential_ref: str
    created_at: str


class Run(BaseModel):
    id: str
    deployment_id: str
    resolved_release_id: str
    state: RunState
    input_fingerprint: str
    idempotency_key: str | None = None
    seed: int | None = None
    #: The method's validated input values (generic; shape per the manifest).
    inputs: dict = Field(default_factory=dict)
    provider_job_id: str | None = None
    submitted_at: str | None = None
    finished_at: str | None = None
    error_category: str | None = None
    outputs_ref: str | None = None
