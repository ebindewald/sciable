"""Execution backends behind a narrow submit/status/cancel interface.

The control plane only ever talks to a ``Backend``; swapping the fake for the
live Runpod adapter changes no run-service logic.
"""

from app.backends.base import (
    Backend,
    JobSpec,
    JobStatus,
    SubmitResult,
    SubmitStatus,
)

__all__ = [
    "Backend",
    "JobSpec",
    "JobStatus",
    "SubmitResult",
    "SubmitStatus",
]
