"""RhoFold+ 3D-structure runner (invoked as a subprocess by handler.py).

Predicts a 3D structure for an RNA sequence and returns the predicted coordinates
(PDB text) plus the per-residue pLDDT confidence. v1 runs in single-sequence mode
(--single_seq_pred True): no MSA generation at runtime, which would need network +
databases and break job isolation. The predicted fold is a CANDIDATE; a successful
run does not validate biological function.

Two paths, chosen at runtime (mirrors run_structrfm.py):
* Real — when the pre-baked checkpoint is present (env RHOFOLD_CHECKPOINT): invoke
  upstream ``inference.py`` as a nested subprocess with the local checkpoint, so it
  never hits its auto-download path, and read back the predicted PDB.
* Stub — when it is absent (local/CI, no GPU, no weights): a deterministic
  placeholder PDB, clearly marked engine="stub". The placeholder matches the fake
  backend's, so control-plane output is identical on both.

Real-path config (all baked into the GPU worker image):
    RHOFOLD_CHECKPOINT   path to rhofold_pretrained_params.pt (gates the real path)
    RHOFOLD_INFERENCE    path to upstream inference.py
    RHOFOLD_DEVICE       torch device, default "cuda:0"
    RHOFOLD_RELAX_STEPS  amber relax steps, default "1000" (upstream default)

I/O contract (shared by every runner):
    --input <input.json>   {"sequence": ..., "seed": ...}
    --out   <out.json>     {"sequence", "pdb", "plddt" (per-residue), "engine"}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_T0 = time.monotonic()


def _log(msg: str) -> None:
    """Progress line on stderr; the handler forwards these live to Runpod's logs."""
    print(f"[rhofold +{time.monotonic() - _T0:7.1f}s] {msg}", file=sys.stderr, flush=True)


class RealInferenceError(RuntimeError):
    """The real RhoFold path was configured but failed — surface, don't fake."""


def _plddt_from_pdb(pdb_text: str) -> list[float]:
    """Per-residue pLDDT from the B-factor column (cols 61-66), in file order.

    RhoFold writes the per-residue pLDDT into the B-factor of every atom in a
    residue, so we read the first atom seen per (chain, resSeq). Kept in sync with
    ``app.api.plddt_from_pdb`` (the worker stays standalone, so it is duplicated)."""
    seen: set[tuple[str, str]] = set()
    plddt: list[float] = []
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM", "HETATM")) or len(line) < 66:
            continue
        key = (line[21:22], line[22:26])
        if key in seen:
            continue
        seen.add(key)
        try:
            plddt.append(round(float(line[60:66]), 2))
        except ValueError:
            continue
    return plddt


def _stub_pdb(sequence: str) -> tuple[str, list[float]]:
    """Deterministic placeholder PDB — one P atom per nucleotide along a straight
    line, with an input-derived per-residue pLDDT (40-95) in the B-factor column.
    NOT a real structure. Matches app.backends.fake._pdb so output is identical."""
    seq = sequence or ""
    lines: list[str] = []
    plddt: list[float] = []
    for i, base in enumerate(seq, start=1):
        h = hashlib.sha256(f"{seq}:{i}".encode()).digest()
        x = i * 5.9  # ~P-P backbone spacing
        conf = round(40 + (h[0] / 255) * 55, 2)  # 40.00 - 95.00
        plddt.append(conf)
        lines.append(
            f"ATOM  {i:>5} {'P':^4}{' '}{base:>3} {'A'}{i:>4}{' '}   "
            f"{x:>8.3f}{0.0:>8.3f}{0.0:>8.3f}{1.0:>6.2f}{conf:>6.2f}          {'P':>2}"
        )
    lines += ["TER", "END"]
    return "\n".join(lines) + "\n", plddt


def _check_gpu(device: str) -> None:
    """Refuse an unsupported GPU up front (same guard as run_structrfm.py).

    On an unsupported arch the first CUDA allocation can hang instead of erroring,
    so we fail fast with a clear message rather than time out."""
    if not device.startswith("cuda"):
        _log(f"device={device!r} (not CUDA) - skipping GPU arch check")
        return
    import torch

    _log(f"torch {torch.__version__}, built for CUDA {torch.version.cuda}")
    if not torch.cuda.is_available():
        raise RealInferenceError(f"device={device!r} requested but CUDA is not available.")
    cap = torch.cuda.get_device_capability(0)
    arch = f"sm_{cap[0]}{cap[1]}"
    archs = torch.cuda.get_arch_list()
    _log(f"GPU {torch.cuda.get_device_name(0)}, {arch}; torch kernels for {archs}")
    level = cap[0] * 10 + cap[1]
    supported = any(a.startswith(f"sm_{cap[0]}") and int(a[3:]) <= level for a in archs)
    if not supported:
        raise RealInferenceError(
            f"GPU {torch.cuda.get_device_name(0)} ({arch}) is not supported by torch "
            f"{torch.__version__} (kernels for {archs}). Rebuild the image on a newer "
            f"torch or restrict the endpoint to supported GPUs."
        )


def _real_pdb(sequence: str, checkpoint: str, work: Path) -> tuple[str, list[float]]:
    """Run upstream inference.py (single-sequence mode) and read the predicted PDB."""
    inference = os.environ.get("RHOFOLD_INFERENCE")
    if not inference or not Path(inference).exists():
        raise RealInferenceError(
            f"RHOFOLD_INFERENCE not set or missing (got {inference!r}); cannot run inference."
        )
    device = os.environ.get("RHOFOLD_DEVICE", "cuda:0")
    relax_steps = os.environ.get("RHOFOLD_RELAX_STEPS", "1000")
    _check_gpu(device)

    fasta = work / "input.fasta"
    fasta.write_text(f">query\n{sequence}\n")
    out_dir = work / "rhofold_out"
    out_dir.mkdir(parents=True, exist_ok=True)

    argv = [
        sys.executable, inference,
        "--input_fas", str(fasta),
        "--single_seq_pred", "True",   # no MSA: self-contained, no network/databases
        "--ckpt", checkpoint,          # local pre-baked weights; avoids auto-download
        "--device", device,
        "--relax_steps", relax_steps,
        "--output_dir", str(out_dir),
    ]
    _log(f"invoking inference.py (device={device}, relax_steps={relax_steps}, len={len(sequence)})")
    # inference.py's stdout/stderr -> our stderr, so the handler forwards it live and
    # it cannot pollute our stdout (where we print the one-line JSON report).
    proc = subprocess.run(  # noqa: S603 - argv is a fixed array, shell=False
        argv, cwd=str(work), stdout=sys.stderr, stderr=sys.stderr, check=False
    )
    if proc.returncode != 0:
        raise RealInferenceError(f"inference.py exited {proc.returncode}.")

    relaxed = out_dir / f"relaxed_{relax_steps}_model.pdb"
    unrelaxed = out_dir / "unrelaxed_model.pdb"
    pdb_path = relaxed if relaxed.exists() else unrelaxed
    if not pdb_path.exists():
        raise RealInferenceError(f"inference.py produced no PDB (looked for {pdb_path}).")
    _log(f"reading predicted structure from {pdb_path.name}")
    pdb_text = pdb_path.read_text()
    plddt = _plddt_from_pdb(pdb_text)
    if not plddt:
        raise RealInferenceError("predicted PDB had no parseable pLDDT (B-factor) values.")
    return pdb_text, plddt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    inputs = json.loads(Path(args.input).read_text())
    sequence = str(inputs.get("sequence") or "")

    home = Path(os.path.expanduser("~"))
    seen_at_start = sorted(p.name for p in home.iterdir()) if home.exists() else []

    checkpoint = os.environ.get("RHOFOLD_CHECKPOINT")
    ckpt_exists = bool(checkpoint and Path(checkpoint).exists())
    diag = {"ckpt_env": checkpoint, "ckpt_exists": ckpt_exists}
    _log(f"start: checkpoint={checkpoint!r} exists={ckpt_exists} length={len(sequence)}")

    if ckpt_exists:
        # A configured real path that fails must error (not silently fall to stub).
        try:
            pdb, plddt = _real_pdb(sequence, checkpoint, Path.cwd())
        except Exception as exc:  # noqa: BLE001 - surface the reason to the caller
            raise RealInferenceError(f"RhoFold inference failed: {exc}") from exc
        engine = "rhofold"
    elif os.environ.get("SCIABLE_ALLOW_STUB") != "1":
        # Refuse to fake a prediction. A worker without its checkpoint is a
        # misconfigured deployment (wrong image or endpoint), and a stub
        # "success" here has twice masked exactly that (2026-11-07). The stub
        # stays available for CI/dev, but only on explicit opt-in.
        print(
            "Internal error: job failed — RHOFOLD_CHECKPOINT is unset or the file "
            "is missing, so this image cannot run real rhofold inference (wrong "
            "image/endpoint for this method?). Stub fallback is disabled outside "
            "CI/dev; set SCIABLE_ALLOW_STUB=1 to permit it.",
            file=sys.stderr,
        )
        return 1
    else:
        cache = home / ".rhofold_cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "weights.marker").write_text("stub-cache")
        pdb, plddt = _stub_pdb(sequence)
        engine = "stub"

    _log(f"done: engine={engine}, residues={len(plddt)}")
    Path(args.out).write_text(json.dumps({
        "sequence": sequence,
        "pdb": pdb,
        "plddt": plddt,
        "engine": engine,
    }))
    print(json.dumps({
        "engine": engine,
        "stub": engine == "stub",
        "home": str(home),
        "seen_at_start": seen_at_start,
        "length": len(sequence),
        "residues": len(plddt),
        "format": "pdb",
        "diag": diag,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
