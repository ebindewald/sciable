"""structRFM secondary-structure runner (invoked as a subprocess by handler.py).

Predicts an RNA secondary structure (dot-bracket) for a sequence.

Two paths, chosen at runtime:

* **Real inference** — when the structRFM SSP script and a fine-tuned checkpoint
  are present (env ``STRUCTRFM_SSP_SCRIPT`` + ``STRUCTRFM_SSP_CHECKPOINT``, baked
  into the GPU worker image), we invoke upstream's official SSP CLI with
  ``--output_format dbn`` and parse the dot-bracket it writes. Using the upstream
  script keeps our output faithful to structRFM by construction (the
  "validate the adapter against upstream" step compares this to a direct run).
      upstream: https://github.com/heqin-zhu/structRFM  (MIT)
      checkpoint: structRFM_SSP_bpRNA1m.pt (GitHub release asset)

* **Stub fallback** — when those are absent (local/CI, no GPU, no weights) we emit
  a deterministic placeholder sized to the sequence. It is clearly marked
  ``engine="stub"`` so nothing downstream mistakes it for a real prediction; the
  web UI also shows a "Mockup" banner while the method manifest is provisional.

I/O contract (shared by every runner):
    --input <input.json>   {"sequence": ..., "seed": ...}
    --out   <out.json>     {"sequence": ..., "structure": ..., "engine": ...}

Isolation: this process reads only files handed to it and writes only under its
workspace (cwd) and HOME, both of which the handler makes private per job.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_BRACKETS = set("().[]{}<>")


class RealInferenceError(RuntimeError):
    """The real structRFM path was configured but failed — surface, don't fake."""


def _stub_structure(sequence: str) -> str:
    """Deterministic placeholder (a single hairpin): pair the outer third on each
    side, leave the middle unpaired. Well-formed dot-bracket of the right length —
    NOT a real structRFM prediction; depends only on length, not on the bases."""
    n = len(sequence)
    k = n // 3
    return "(" * k + "." * (n - 2 * k) + ")" * k


def _real_paths() -> tuple[str, str] | None:
    """(script, checkpoint) if both are present on disk, else None (-> stub)."""
    script = os.environ.get("STRUCTRFM_SSP_SCRIPT")
    checkpoint = os.environ.get("STRUCTRFM_SSP_CHECKPOINT")
    if script and checkpoint and Path(script).exists() and Path(checkpoint).exists():
        return script, checkpoint
    return None


def _parse_dbn(output_dir: Path, seq_len: int) -> str:
    """Find the dot-bracket line in the .dbn the SSP CLI wrote for our sequence."""
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            s = line.strip()
            if s and len(s) == seq_len and set(s) <= _BRACKETS:
                return s
    raise RealInferenceError(f"No dot-bracket of length {seq_len} in structRFM output.")


def _predict_real(sequence: str, script: str, checkpoint: str) -> str:
    """Run upstream's SSP CLI (dot-bracket output).

    Our I/O files are ABSOLUTE (they live in this job's isolated cwd), but the
    subprocess runs with cwd = the structRFM repo root, because the upstream
    script resolves some resources relative to the repo.
    """
    fasta = Path("query.fasta").resolve()
    fasta.write_text(f">query\n{sequence}\n")
    out_dir = Path("structrfm_out").resolve()
    out_dir.mkdir(exist_ok=True)
    repo_root = Path(script).resolve().parent.parent  # /opt/structRFM

    argv = [
        sys.executable, script,
        "--gpu", os.environ.get("STRUCTRFM_GPU", "0"),
        "--output_format", "dbn",
        "--checkpoint_path", checkpoint,
        "--input_fasta", str(fasta),
        "--output_dir", str(out_dir),
    ]
    proc = subprocess.run(  # noqa: S603
        argv, cwd=str(repo_root), capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-1200:]
        raise RealInferenceError(f"structRFM SSP exited {proc.returncode}: {tail}")
    return _parse_dbn(out_dir, len(sequence))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    inputs = json.loads(Path(args.input).read_text())
    sequence = str(inputs.get("sequence") or "")

    # Record HOME contents before writing — proves per-job HOME isolation in tests
    # (structRFM / HF cache weights under ~/ in the real path).
    home = Path(os.path.expanduser("~"))
    seen_at_start = sorted(p.name for p in home.iterdir()) if home.exists() else []

    # Diagnostics: why did we (not) take the real path? Surfaced in the report so
    # a stub fallback on a GPU worker is explainable at a glance.
    _script = os.environ.get("STRUCTRFM_SSP_SCRIPT")
    _ckpt = os.environ.get("STRUCTRFM_SSP_CHECKPOINT")
    diag = {
        "ssp_script_env": _script,
        "ssp_ckpt_env": _ckpt,
        "script_exists": bool(_script and Path(_script).exists()),
        "ckpt_exists": bool(_ckpt and Path(_ckpt).exists()),
    }

    real = _real_paths()
    if real is not None:
        # A configured real path that fails must error (not silently fall to stub),
        # so a broken deployment surfaces as worker_error rather than fake output.
        structure = _predict_real(sequence, *real)
        engine = "structRFM"
    else:
        cache = home / ".structrfm_cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "weights.marker").write_text("stub-cache")
        structure = _stub_structure(sequence)
        engine = "stub"

    Path(args.out).write_text(
        json.dumps({"sequence": sequence, "structure": structure, "engine": engine})
    )
    print(json.dumps({
        "engine": engine,
        "stub": engine == "stub",
        "home": str(home),
        "seen_at_start": seen_at_start,
        "length": len(sequence),
        "format": "dot_bracket",
        "diag": diag,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
