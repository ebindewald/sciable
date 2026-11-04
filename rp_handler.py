"""Runpod Serverless entrypoint — root-level `rp_handler.py` with a bare
`runpod.serverless.start()` so Runpod's GitHub handler detection finds it.

(The detector traces the Dockerfile CMD to a concrete file and looks for a
module-level `runpod.serverless.start(...)`; a call nested in worker/ under an
`if __name__ == "__main__":` guard and launched via `python -m ...` is invisible
to it.)

All real work stays in worker/: this only wraps the isolated job runner in
`worker/handler.py`, where each job runs in its own subprocess with a private
workspace/HOME/TMPDIR.
"""

from __future__ import annotations

import runpod

from worker.handler import handler as _run_job


def handler(event: dict) -> dict:
    return _run_job(event)


runpod.serverless.start({"handler": handler})
