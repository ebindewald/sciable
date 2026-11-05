"""Deterministic in-process backend for local development and CI.

No GPU, no credentials, no network. The job lifecycle advances by one step per
``status()`` poll so tests can observe queued -> running -> succeeded. Edge
paths are driven by sentinel seeds so every scenario is reproducible:

    seed == -1  -> job fails
    seed == -2  -> submission is UNCERTAIN (provider ack unknown)
    seed == -3  -> job stays RUNNING forever (simulates a timeout/stuck job)

Output is shaped by the method's ``output_kind``: design methods get candidate
sequences, structure methods get a (placeholder) dot-bracket over the input
sequence. A single FakeBackend instance is held on ``app.state`` so its per-job
poll counters persist across requests within one app.
"""

from __future__ import annotations

import hashlib

from app.backends.base import JobSpec, JobStatus, SubmitResult, SubmitStatus
from app.models import RunState

_RNA_ALPHABET = "ACGU"


def _sequences(fingerprint: str, design_count: int, length: int = 24) -> list[str]:
    """Deterministic pseudo-sequences derived from the input fingerprint."""
    out: list[str] = []
    for i in range(design_count):
        digest = hashlib.sha256(f"{fingerprint}:{i}".encode()).digest()
        seq = "".join(_RNA_ALPHABET[b % 4] for b in digest[:length])
        out.append(seq)
    return out


def _dot_bracket(sequence: str) -> str:
    """A deterministic, balanced placeholder structure (a single hairpin): pair
    the outer third on each side, leave the middle unpaired. Not a real
    prediction — just a well-formed dot-bracket of the right length."""
    n = len(sequence)
    k = n // 3
    return "(" * k + "." * (n - 2 * k) + ")" * k


def _contact_matrix(sequence: str) -> list[list[float]]:
    """Deterministic symmetric placeholder L×L matrix in [0,1] — not a prediction."""
    n = len(sequence)
    mat = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            h = hashlib.sha256(f"{sequence}:{i}:{j}".encode()).digest()[0]
            v = round(h / 255, 4)
            mat[i][j] = mat[j][i] = v
    return mat


class FakeBackend:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._counter = 0

    def submit(self, job: JobSpec) -> SubmitResult:
        if job.seed == -2:
            return SubmitResult(status=SubmitStatus.UNCERTAIN)

        self._counter += 1
        job_id = f"fake-{self._counter:06d}"
        if job.seed == -1:
            scenario = "fail"
        elif job.seed == -3:
            scenario = "stuck"
        else:
            scenario = "succeed"
        self._jobs[job_id] = {
            "scenario": scenario,
            "polls": 0,
            "fingerprint": job.fingerprint,
            "method_id": job.method_id,
            "output_kind": job.output_kind,
            "inputs": dict(job.inputs),
        }
        return SubmitResult(status=SubmitStatus.ACCEPTED, provider_job_id=job_id)

    def status(self, provider_job_id: str) -> JobStatus:
        job = self._jobs.get(provider_job_id)
        if job is None:
            return JobStatus(state=RunState.FAILED, error_category="unknown_job")

        if job["scenario"] == "canceled":
            return JobStatus(state=RunState.CANCELED)

        job["polls"] += 1
        polls, scenario = job["polls"], job["scenario"]

        if scenario == "stuck":
            return JobStatus(state=RunState.RUNNING)
        if polls < 2:
            return JobStatus(state=RunState.RUNNING)
        if scenario == "fail":
            return JobStatus(state=RunState.FAILED, error_category="worker_error")

        outputs, raw = self._produce(job, provider_job_id)
        return JobStatus(state=RunState.SUCCEEDED, outputs=outputs, raw=raw)

    def _produce(self, job: dict, provider_job_id: str) -> tuple[dict, dict]:
        method_id, kind, inputs = job["method_id"], job["output_kind"], job["inputs"]
        base_raw = {
            "backend": "fake",
            "note": f"Deterministic placeholder output — not real {method_id} (pending M1).",
            "model": {"name": method_id, "version": "stub", "checkpoint": "PENDING-M1-PIN"},
            "job": {"provider_job_id": provider_job_id},
        }
        if kind == "contact_map":
            sequence = str(inputs.get("sequence") or "")
            matrix = _contact_matrix(sequence)
            base_raw["job"]["length"] = len(sequence)
            base_raw["stdout"] = "\n".join(
                [
                    "[stub-runner] loading sequence... ok",
                    f"[stub-runner] extracting attention map for {len(sequence)} nt...",
                    "[stub-runner] done.",
                ]
            )
            base_raw["report"] = {"stub": True, "format": "attention_matrix"}
            return (
                {"sequence": sequence, "matrix": matrix, "matrix_type": "attention"},
                base_raw,
            )

        if kind == "structure":
            sequence = str(inputs.get("sequence") or "")
            structure = _dot_bracket(sequence)
            base_raw["job"]["length"] = len(sequence)
            base_raw["stdout"] = "\n".join(
                [
                    "[stub-runner] loading sequence... ok",
                    f"[stub-runner] predicting structure for {len(sequence)} nt...",
                    "[stub-runner] done.",
                ]
            )
            base_raw["report"] = {"stub": True, "format": "dot_bracket"}
            return {"sequence": sequence, "structure": structure}, base_raw

        # default: sequences (design methods)
        design_count = int(inputs.get("design_count") or 1)
        sequences = _sequences(job["fingerprint"], design_count)
        base_raw["job"]["design_count"] = len(sequences)
        base_raw["stdout"] = "\n".join(
            [
                "[stub-runner] loading target backbone... ok",
                f"[stub-runner] sampling {len(sequences)} designs (seed-derived)...",
                "[stub-runner] done.",
            ]
        )
        base_raw["report"] = {"stub": True, "alphabet": "ACGU"}
        return {"sequences": sequences}, base_raw

    def cancel(self, provider_job_id: str) -> bool:
        job = self._jobs.get(provider_job_id)
        if job is None:
            return False
        already_terminal = job["scenario"] in {"fail", "succeed"} and job["polls"] >= 2
        if already_terminal:
            return False
        job["scenario"] = "canceled"
        return True
