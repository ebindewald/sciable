"""Runpod Serverless entrypoint.

Runpod invokes ``handler(event)`` with ``event["input"]`` carrying the job
parameters we send from ``RunpodBackend.submit``. Each job runs in its own
isolated subprocess (see ``handler.run_job``). This module only adapts our
handler to the Runpod SDK's worker loop.

NOTE: the in-container inference is still the stub in ``run_grnade.py`` until the
M1 baseline pins real gRNAde. Deploying this proves the full Runpod path end to
end (submit/status/cancel, billing, isolation); the designs are placeholders
until the model is swapped in.
"""

from __future__ import annotations

import runpod

from worker.handler import handler as run_handler


def handler(event: dict) -> dict:
    return run_handler(event)


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
