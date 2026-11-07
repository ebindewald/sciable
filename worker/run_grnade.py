"""gRNAde inverse-folding runner (invoked as a subprocess by handler.py).

Designs candidate RNA sequences for a target 3D backbone (PDB) — inverse folding.
The designs are CANDIDATES for the supplied backbone; a successful run validates
neither folding nor biological function.

Two paths, chosen at runtime (mirrors run_rhofold.py / run_structrfm.py):
* Real — when the pre-baked checkpoint is present (env GRNADE_CHECKPOINT): import
  upstream's programmatic API from the cloned repo (GRNADE_REPO), featurize the PDB,
  load the one gRNAde checkpoint, and sample ``design_count`` sequences with a fixed
  seed/temperature. Raises on any failure — it never silently falls back to the stub.
* Stub — only on explicit opt-in (SCIABLE_ALLOW_STUB=1, CI/dev): deterministic
  placeholder sequences, clearly marked engine="stub". Without the checkpoint AND
  without the opt-in the job fails loudly: a worker missing its checkpoint is a
  misconfigured deployment (wrong image/endpoint), and a stub "success" has twice
  masked exactly that (2026-11-07).

Scope note: this adapter exposes gRNAde's raw sampled designs. It deliberately does
NOT run upstream's RibonanzaNet scoring/filtering loop (design.py's
total_samples=1e6 → filter pipeline) — that is heavier than the sciable contract
(≤10 seeded designs) needs and pulls two extra checkpoints. Upstream-parity is
therefore validated against ``gRNAde.sample`` directly, not design.py's filtered CSV.

Real-path config (all baked into the GPU worker image):
    GRNADE_CHECKPOINT   path to gRNAde_drop3d@0.75_maxlen@500.h5 (gates the real path)
    GRNADE_REPO         path to the cloned upstream repo (added to sys.path)
    GRNADE_DEVICE       torch device, default "cuda:0"
    GRNADE_TEMPERATURE  sampling temperature, default "0.1" (upstream's conservative min)

I/O contract (shared by every runner):
    --input <input.json>   {"structure", "chain", "design_count", "seed", ...}
    --out   <out.json>     {"sequences": [...], "engine": "grnade"|"stub"}

Isolation note: this process reads only files handed to it and writes only under
its workspace (cwd) and HOME, both of which the handler makes private per job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

_T0 = time.monotonic()
_RNA = "ACGU"

# gRNAde architecture params — from upstream configs/design.yaml ("do not change
# unless using a custom checkpoint"). Must match the pinned checkpoint.
_MODEL_DIMS = {
    "node_in_dim": (15, 4),
    "node_h_dim": (128, 16),
    "edge_in_dim": (132, 3),
    "edge_h_dim": (64, 4),
    "num_layers": 4,
    "drop_rate": 0.5,
    "out_dim": 4,
}
# Featurizer params — design.yaml values for 3D-conditioned single-state design.
_FEATURIZER_KWARGS = {
    "split": "test",
    "radius": 0.0,
    "top_k": 32,
    "num_rbf": 32,
    "num_posenc": 32,
    "max_num_conformers": 1,
}


def _log(msg: str) -> None:
    """Progress line on stderr; the handler forwards these live to Runpod's logs."""
    print(f"[grnade +{time.monotonic() - _T0:7.1f}s] {msg}", file=sys.stderr, flush=True)


class RealInferenceError(RuntimeError):
    """The real gRNAde path was configured but failed — surface, don't fake."""


def _design(pdb_text: str, chain: str, seed: str, count: int, length: int = 24) -> list[str]:
    """Deterministic placeholder sequences — NOT gRNAde. Stub path only."""
    base = f"{pdb_text}|{chain}|{seed}".encode()
    out: list[str] = []
    for i in range(count):
        digest = hashlib.sha256(base + f"|{i}".encode()).digest()
        out.append("".join(_RNA[b % 4] for b in digest[:length]))
    return out


def _filter_chain(pdb_text: str, chain: str) -> str:
    """Keep only the selected chain's coordinate records (gRNAde expects a single
    RNA chain). The app's validator already resolved ``chain`` to one RNA chain;
    filtering here makes the featurizer see exactly that chain even if the PDB
    carried others. Fixed-column parse (chain id = col 22, PDB spec)."""
    if not chain:
        return pdb_text
    kept: list[str] = []
    for line in pdb_text.splitlines():
        if line.startswith(("ATOM", "HETATM")):
            if line[21:22].strip() == chain:
                kept.append(line)
        elif line.startswith("TER"):
            # keep a TER only if it belongs to the selected chain (or is unlabeled)
            if len(line) < 22 or line[21:22].strip() in ("", chain):
                kept.append(line)
    return "\n".join(kept) + "\n"


def _check_gpu(device: str) -> None:
    """Refuse an unsupported GPU up front (same guard as the other runners).

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


def _real_design(pdb_text: str, chain: str, seed: int, count: int, work: Path) -> list[str]:
    """Run real gRNAde inference via upstream's programmatic API.

    Mirrors upstream design.py's load/featurize/sample sequence, but on the one
    checkpoint and without the RibonanzaNet scoring loop (see the module docstring).
    """
    repo = os.environ.get("GRNADE_REPO")
    if not repo or not Path(repo).exists():
        raise RealInferenceError(f"GRNADE_REPO not set or missing (got {repo!r}).")
    checkpoint = os.environ.get("GRNADE_CHECKPOINT", "")
    device_str = os.environ.get("GRNADE_DEVICE", "cuda:0")
    temperature = float(os.environ.get("GRNADE_TEMPERATURE", "0.1"))
    _check_gpu(device_str)

    # Upstream's src.constants reads PROJECT_PATH at import (used by sec_struct_utils
    # as a temp-file dir); point it at this job's private, writable workspace so
    # featurization cannot write into the shared image. Set BEFORE importing src.*.
    os.environ.setdefault("PROJECT_PATH", str(work))
    os.environ.setdefault("DATA_PATH", str(work))
    # ⚠ Upstream evaluates os.path.join(X3DNA_PATH, ...) and os.path.join(ETERNAFOLD_PATH,
    # ...) as DEFAULT ARGUMENT VALUES at module import (src/data/sec_struct_utils.py),
    # which raises TypeError if the env var is None. Set placeholder paths so the import
    # chain loads. These fallback tools are only invoked at runtime if biotite's
    # sec-struct path fails (our normal PDB path does not hit it); the binaries are NOT
    # installed, so if that fallback ever triggers it fails loudly, never silently.
    os.environ.setdefault("X3DNA", "/opt/x3dna")
    os.environ.setdefault("ETERNAFOLD", "/opt/eternafold")
    if repo not in sys.path:
        sys.path.insert(0, repo)

    # All heavy imports are function-local so the stub path needs no torch/upstream.
    import random  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415
    import torch  # noqa: PLC0415
    from src.constants import NUM_TO_LETTER  # noqa: PLC0415
    from src.data.featurizer import RNAGraphFeaturizer  # noqa: PLC0415
    from src.models import gRNAde  # noqa: PLC0415

    device = torch.device(device_str if torch.cuda.is_available() else "cpu")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

    # Featurize the (single-chain) PDB.
    pdb_path = work / "target.pdb"
    pdb_path.write_text(_filter_chain(pdb_text, chain))
    _log(f"featurizing target (chain={chain!r})")
    featurizer = RNAGraphFeaturizer(device=device, **_FEATURIZER_KWARGS)
    featurized, raw = featurizer.featurize_from_pdb_file(str(pdb_path))
    featurized = featurized.to(device)
    n_nodes = len(raw["sequence"])
    _log(f"featurized {n_nodes} residues; loading checkpoint")

    # Load the one gRNAde checkpoint (weights only; arch from _MODEL_DIMS).
    model = gRNAde(**_MODEL_DIMS)
    model.load_state_dict(torch.load(checkpoint, map_location=torch.device("cpu")))
    model = model.to(device)
    model.eval()

    _log(f"sampling {count} designs (temperature={temperature})")
    with torch.no_grad():
        samples = model.sample(featurized, count, temperature)
    seqs = [
        "".join(NUM_TO_LETTER[int(n)] for n in row)
        for row in samples.cpu().tolist()
    ]
    bad = {ch for s in seqs for ch in s} - set(_RNA)
    if bad:
        raise RealInferenceError(f"designs contain non-ACGU characters: {sorted(bad)}")
    if any(len(s) != n_nodes for s in seqs):
        raise RealInferenceError(
            f"design length != target length {n_nodes}: {[len(s) for s in seqs]}"
        )
    _log(f"done: {len(seqs)} designs of length {n_nodes}")
    return seqs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    inputs = json.loads(Path(args.input).read_text())
    pdb_text = str(inputs.get("structure") or "")
    chain = str(inputs.get("chain") or "")
    seed_raw = inputs.get("seed")
    seed = 0 if seed_raw is None else int(seed_raw)
    count = max(1, int(inputs.get("design_count") or 1))

    # Record what this job sees in HOME *before* writing — proves, in tests, that
    # a fresh job never inherits a previous job's cache.
    home = Path(os.path.expanduser("~"))
    seen_at_start = sorted(p.name for p in home.iterdir()) if home.exists() else []

    checkpoint = os.environ.get("GRNADE_CHECKPOINT")
    ckpt_exists = bool(checkpoint and Path(checkpoint).exists())
    diag = {"ckpt_env": checkpoint, "ckpt_exists": ckpt_exists}
    _log(f"start: checkpoint={checkpoint!r} exists={ckpt_exists} count={count} seed={seed}")

    if ckpt_exists:
        # A configured real path that fails must error (not silently fall to stub).
        try:
            sequences = _real_design(pdb_text, chain, seed, count, Path.cwd())
        except Exception as exc:  # noqa: BLE001 - surface the reason to the caller
            raise RealInferenceError(f"gRNAde inference failed: {exc}") from exc
        engine = "grnade"
    elif os.environ.get("SCIABLE_ALLOW_STUB") != "1":
        # Refuse to fake a design. A worker without its checkpoint is a
        # misconfigured deployment (wrong image or endpoint); a stub "success"
        # here has twice masked exactly that (2026-11-07). The stub stays
        # available for CI/dev, but only on explicit opt-in.
        print(
            "Internal error: job failed — GRNADE_CHECKPOINT is unset or the file "
            "is missing, so this image cannot run real gRNAde inference (wrong "
            "image/endpoint for this method?). Stub fallback is disabled outside "
            "CI/dev; set SCIABLE_ALLOW_STUB=1 to permit it.",
            file=sys.stderr,
        )
        return 1
    else:
        # Simulate a model that caches to ~/ (gRNAde/torch do this). The handler's
        # per-job HOME means this cannot leak to the next job.
        cache = home / ".grnade_cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "weights.marker").write_text("stub-cache")
        sequences = _design(pdb_text, chain, "" if seed_raw is None else str(seed), count)
        engine = "stub"

    Path(args.out).write_text(json.dumps({"sequences": sequences, "engine": engine}))

    # Machine-readable report on the last stdout line (handler parses this).
    print(json.dumps({
        "engine": engine,
        "stub": engine == "stub",
        "chain_received": chain,
        "home": str(home),
        "seen_at_start": seen_at_start,
        "count": len(sequences),
        "diag": diag,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
