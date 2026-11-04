"""gRNAde inference runner (invoked as a subprocess by handler.py).

STUB — PENDING M1. This does NOT run gRNAde. It emits deterministic placeholder
sequences so the worker/isolation/control-plane pipeline can be exercised with no
GPU, model weights, or network. Once the M1 baseline pins the upstream commit and
checkpoint, replace ``_design`` with the real inference call; the I/O contract
(read input.json, write out.json) and the isolated-cwd/HOME behavior stay.

I/O contract (shared by every runner):
    --input <input.json>   validated input values: {"structure", "chain",
                           "design_count", "seed", ...}
    --out   <out.json>     output values: {"sequences": [...]}

Isolation note: this process reads only files handed to it and writes only under
its workspace (cwd) and HOME, both of which the handler makes private per job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

_RNA = "ACGU"


def _design(pdb_text: str, chain: str, seed: str, count: int, length: int = 24) -> list[str]:
    base = f"{pdb_text}|{chain}|{seed}".encode()
    out: list[str] = []
    for i in range(count):
        digest = hashlib.sha256(base + f"|{i}".encode()).digest()
        out.append("".join(_RNA[b % 4] for b in digest[:length]))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    inputs = json.loads(Path(args.input).read_text())
    pdb_text = str(inputs.get("structure") or "")
    chain = str(inputs.get("chain") or "")
    seed = "" if inputs.get("seed") is None else str(inputs.get("seed"))
    count = max(1, int(inputs.get("design_count") or 1))

    # Record what this job sees in HOME *before* writing — proves, in tests, that
    # a fresh job never inherits a previous job's cache.
    home = Path(os.path.expanduser("~"))
    seen_at_start = sorted(p.name for p in home.iterdir()) if home.exists() else []

    # Simulate a model that caches to ~/ (gRNAde/torch do this). The handler's
    # per-job HOME means this cannot leak to the next job.
    cache = home / ".grnade_cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "weights.marker").write_text("stub-cache")

    sequences = _design(pdb_text, chain, seed, count)
    Path(args.out).write_text(json.dumps({"sequences": sequences}))

    # Machine-readable report on the last stdout line (handler parses this).
    print(json.dumps({
        "stub": True,
        "chain_received": chain,
        "home": str(home),
        "seen_at_start": seen_at_start,
        "count": len(sequences),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
