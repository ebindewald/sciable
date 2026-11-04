"""Stable input fingerprint and idempotency key.

The fingerprint is a content hash of the resolved inputs (release id + the
method's ``fingerprint_material`` — normalized field values that define run
identity, including the seed). When a client does not supply an idempotency key,
the fingerprint is used as one, so an identical resubmission returns the existing
run instead of launching a second (billed) job.
"""

from __future__ import annotations

import hashlib

from app.validation import ValidatedSubmission


def input_fingerprint(release_id: str, submission: ValidatedSubmission) -> str:
    h = hashlib.sha256()
    parts = [release_id, *submission.fingerprint_material]
    h.update("\x00".join(parts).encode("utf-8"))
    return h.hexdigest()


def resolve_idempotency_key(fingerprint: str, client_key: str | None) -> str:
    return client_key if client_key else fingerprint
