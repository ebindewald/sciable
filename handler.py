"""Runpod Serverless entrypoint.

Named `handler.py` and placed at the repo root with a module-level
`runpod.serverless.start(...)`, matching Runpod's canonical worker template —
that is what Runpod's GitHub handler detection looks for (a root `handler.py`
containing the start call; a differently-named file, or `worker/handler.py`
without the start call, is not detected).

All real work stays in worker/: this thin entrypoint wraps the isolated job
runner in `worker/handler.py`, where each job runs in its own subprocess with a
private workspace/HOME/TMPDIR.
"""

from __future__ import annotations

import runpod

from worker.handler import handler as _run_job


def handler(job: dict) -> dict:
    return _run_job(job)


# Guard with __main__ so Runpod's build-time import validation doesn't try to
# start the serverless loop (which would hang or fail without a RUNPOD_API_KEY).
if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
