"""The backend contract.

A ``Backend`` exposes exactly three operations: submit a job, poll its status,
and cancel it where supported. Keeping this interface narrow is what lets the
whole app run against a fake in local/CI and against Runpod in production with
no change to the run service.

The spec is method-agnostic: ``inputs`` carries the method's validated input
values and ``outputs`` carries whatever the worker returns (sequences, a
structure, ...), keyed per the manifest's output kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.models import RunState


@dataclass(frozen=True)
class JobSpec:
    """Everything a backend needs to run one job.

    ``inputs`` values are passed as data; backends must never interpolate them
    into a shell. ``seed`` and all bounds are already validated against the
    manifest by the time a JobSpec is built. ``method_id`` and ``output_kind``
    let a backend dispatch to the right runner and shape its output.
    """

    fingerprint: str
    seed: int | None
    method_id: str
    output_kind: str
    inputs: dict[str, Any]
    endpoint_ref: str


class SubmitStatus(StrEnum):
    #: Provider acknowledged receipt; ``provider_job_id`` is set.
    ACCEPTED = "accepted"
    #: Submission outcome is unknown (e.g. network timeout after the request may
    #: have been accepted). The caller must surface this and must NOT auto-resubmit.
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class SubmitResult:
    status: SubmitStatus
    provider_job_id: str | None = None


@dataclass(frozen=True)
class JobStatus:
    state: RunState
    #: Method outputs when ``state`` is SUCCEEDED — e.g. ``{"sequences": [...]}``
    #: for a design method, ``{"structure": "((..))", "sequence": "..."}`` for a
    #: structure method. Shape matches the manifest's output kind.
    outputs: dict[str, Any] = field(default_factory=dict)
    #: Category only — never raw provider messages or input content.
    error_category: str | None = None
    #: Structured extra output from the worker (report, stdout, metadata) that the
    #: UI can prettify and expose behind a "raw output" click. Content-free of the
    #: submitted structure/sequence itself.
    raw: dict = field(default_factory=dict)


@runtime_checkable
class Backend(Protocol):
    def submit(self, job: JobSpec) -> SubmitResult: ...

    def status(self, provider_job_id: str) -> JobStatus: ...

    def cancel(self, provider_job_id: str) -> bool: ...
