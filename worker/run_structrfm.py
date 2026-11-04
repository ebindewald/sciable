"""structRFM secondary-structure runner (invoked as a subprocess by handler.py).

STUB — PENDING M1. This does NOT run structRFM. It emits a deterministic,
well-formed placeholder dot-bracket so the worker/isolation/control-plane
pipeline can be exercised with no GPU, model weights, or network. Once the M1
baseline pins the upstream commit and checkpoint, replace ``_predict`` with the
real inference call; the I/O contract and isolated-cwd/HOME behavior stay.

I/O contract (shared by every runner):
    --input <input.json>   validated input values: {"sequence", "seed", ...}
    --out   <out.json>     output values: {"sequence": ..., "structure": ...}

Isolation note: this process reads only files handed to it and writes only under
its workspace (cwd) and HOME, both of which the handler makes private per job.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _predict(sequence: str) -> str:
    """Deterministic placeholder structure (a single hairpin): pair the outer
    third on each side, leave the middle unpaired. Well-formed dot-bracket of the
    right length — NOT a real structRFM prediction."""
    n = len(sequence)
    k = n // 3
    return "(" * k + "." * (n - 2 * k) + ")" * k


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    inputs = json.loads(Path(args.input).read_text())
    sequence = str(inputs.get("sequence") or "")

    # Prove per-job HOME isolation the same way the gRNAde stub does (structRFM /
    # torch cache weights to ~/ in the real runner).
    home = Path(os.path.expanduser("~"))
    seen_at_start = sorted(p.name for p in home.iterdir()) if home.exists() else []
    cache = home / ".structrfm_cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "weights.marker").write_text("stub-cache")

    structure = _predict(sequence)
    Path(args.out).write_text(json.dumps({"sequence": sequence, "structure": structure}))

    print(json.dumps({
        "stub": True,
        "home": str(home),
        "seen_at_start": seen_at_start,
        "length": len(sequence),
        "format": "dot_bracket",
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
