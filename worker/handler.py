"""Worker request handler — one isolated subprocess per job.

Isolation model (per AGENTS.md): each job runs in a fresh subprocess with its
own writable workspace, HOME, and TMPDIR. Inputs are treated as data (written to
``input.json`` in the job dir; never interpolated into a shell), arguments are
passed as an array, and the job directory is removed on success and on failure.
This separates *accidental* state for a trusted adapter; sandboxing arbitrary
submitted code would need a separate isolation design.

The handler is method-agnostic: it dispatches on ``method`` to the matching
runner script, hands it ``input.json`` (the validated input values) and an
``--out`` path, and reads back ``out.json`` (the method's output values). Adding
a method is a manifest + a runner here — no handler change beyond the registry.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

RUNNERS_DIR = Path(__file__).resolve().parent
#: method_id -> runner script baked into the worker image.
RUNNERS = {
    "grnade": "run_grnade.py",
    "structrfm": "run_structrfm.py",
    "rhofold": "run_rhofold.py",
}
# Must stay BELOW the Runpod endpoint's execution timeout (600 s by default), so a
# stuck job comes back as a "timeout" error carrying the runner's last log lines
# instead of being killed silently by Runpod.
DEFAULT_TIMEOUT_S = int(os.environ.get("WORKER_JOB_TIMEOUT_S", "540"))
#: How many trailing stderr lines to keep for error reports.
STDERR_TAIL_LINES = 60


class WorkerError(Exception):
    """Job failed. ``category`` is a stable, content-free label."""

    def __init__(self, category: str, message: str = "") -> None:
        super().__init__(message or category)
        self.category = category


@dataclass
class JobResult:
    outputs: dict = field(default_factory=dict)
    runner_report: dict = field(default_factory=dict)
    stdout: str = ""


def _isolated_dirs(base: Path, job_id: str) -> tuple[Path, Path, Path, Path]:
    job_dir = base / job_id
    work, home, tmp = job_dir / "work", job_dir / "home", job_dir / "tmp"
    for d in (work, home, tmp):
        d.mkdir(parents=True, exist_ok=True)
    return job_dir, work, home, tmp


def run_job(
    job_input: dict,
    *,
    base_dir: Path | None = None,
    runners_dir: Path = RUNNERS_DIR,
    runner: Path | None = None,
    python_executable: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    cleanup: bool = True,
) -> JobResult:
    """Run one job in an isolated subprocess.

    ``job_input`` keys: ``method`` (defaults to ``grnade``) and ``inputs`` (the
    validated input values dict). ``runner`` overrides the method->script lookup
    (used in tests). Raises :class:`WorkerError` on any failure; the job directory
    is always removed afterwards (unless ``cleanup=False``).
    """
    method = job_input.get("method") or "grnade"
    inputs = job_input.get("inputs") or {}
    if runner is None:
        runner_name = RUNNERS.get(method)
        if runner_name is None:
            raise WorkerError("unknown_method", f"No runner for method '{method}'.")
        runner = runners_dir / runner_name

    base = base_dir or Path(os.environ.get("WORKER_WORK_ROOT", "/tmp/sciable-jobs"))
    base.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    job_dir, work, home, tmp = _isolated_dirs(base, job_id)

    try:
        input_path = work / "input.json"
        input_path.write_text(json.dumps(inputs))  # inputs are data, written to a file
        out_path = work / "out.json"

        argv = [
            python_executable or sys.executable,
            str(runner),
            "--input", str(input_path),
            "--out", str(out_path),
        ]

        # Inherit the container environment — real runners need PATH, the CUDA /
        # LD_LIBRARY_PATH entries, and the model-config vars baked into the image
        # (e.g. STRUCTRFM_SSP_SCRIPT / STRUCTRFM_SSP_CHECKPOINT) — but give each
        # job a PRIVATE HOME and TMPDIR so a model that caches to ~/ or writes
        # temp files cannot share state across jobs.
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["TMPDIR"] = str(tmp)
        env["PYTHONHASHSEED"] = "0"

        stdout, stderr_tail, returncode = _run_streaming(argv, env, work, timeout_s)

        if returncode != 0:
            tail = (stderr_tail or stdout or "").strip()[-1500:]
            raise WorkerError("worker_error", tail or "Runner exited non-zero.")
        if not out_path.exists():
            raise WorkerError("no_output", "Runner produced no output file.")

        try:
            outputs = json.loads(out_path.read_text())
        except json.JSONDecodeError as exc:
            raise WorkerError("bad_output", "Runner output was not valid JSON.") from exc

        report = _parse_report(stdout)
        return JobResult(outputs=outputs, runner_report=report, stdout=stdout)
    finally:
        if cleanup:
            shutil.rmtree(job_dir, ignore_errors=True)


def _run_streaming(
    argv: list[str], env: dict, cwd: Path, timeout_s: int
) -> tuple[str, str, int]:
    """Run the runner, echoing its stderr live to this process's stderr.

    Runpod's worker logs only show what the handler process prints, so the
    runner's progress lines (stderr) are forwarded as they arrive; stdout is
    collected for the JSON report. Returns (stdout, stderr_tail, returncode).
    Raises WorkerError("timeout") — with the last stderr lines — on timeout.
    """
    tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)

    proc = subprocess.Popen(  # noqa: S603 - argv is a fixed array, shell=False
        argv,
        env=env,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def _pump_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            tail.append(line)
            sys.stderr.write(f"[runner] {line}")
            sys.stderr.flush()

    out_chunks: list[str] = []

    def _pump_stdout() -> None:
        assert proc.stdout is not None
        out_chunks.append(proc.stdout.read())

    threads = [threading.Thread(target=f, daemon=True) for f in (_pump_stderr, _pump_stdout)]
    for t in threads:
        t.start()
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        for t in threads:
            t.join(timeout=5)
        last = "".join(tail).strip()[-1500:]
        raise WorkerError(
            "timeout", f"Job exceeded {timeout_s}s. Last runner output:\n{last}"
        ) from exc
    for t in threads:
        t.join()
    return "".join(out_chunks), "".join(tail), proc.returncode


def _parse_report(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def handler(event: dict) -> dict:
    """Runpod Serverless entrypoint.

    Returns timing/outputs only; never echoes raw input content back to shared logs.
    """
    try:
        result = run_job(event.get("input", {}))
    except WorkerError as exc:
        # Surface a content-free category plus the runner's error detail (stderr
        # tail) so a failed deployment is debuggable without digging in logs.
        return {"error": exc.category, "detail": str(exc)}
    return {
        "outputs": result.outputs,
        "report": result.runner_report,
        "stdout": result.stdout,
    }
