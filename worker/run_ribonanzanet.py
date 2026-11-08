"""RibonanzaNet-SS base-pairing runner (invoked as a subprocess by handler.py).

Predicts the RNA base-pairing probability matrix (L×L, 0..1) of a single RNA sequence
with RibonanzaNet-SS — the model's native output, parallel to structRFM's attention map.
Off-diagonal probability hints at base pairs, including crossing/pseudoknot interactions.
It is a model PREDICTION, not an experimentally validated structure, and no pairing
cutoff is calibrated. (A dot-bracket would need arnie's Hungarian parser on top; we
expose the raw probability matrix instead — simpler, no extra deps, more faithful.)

Single-sequence — no MSA, no folding binaries — so job isolation holds.

Provenance: the SS head's only published form is a Kaggle notebook; the fetchable,
self-contained implementation is the gRNAde copy (tools/ribonanzanet_sec_struct/
network.py + config.yaml), which credits https://github.com/Shujun-He/RibonanzaNet (MIT).
The image clones that source and bakes the ~45 MB ribonanzanet_ss.pt checkpoint.

Two paths, chosen at runtime (mirrors run_structrfm.py / run_grnade.py):
* Real — when the checkpoint is present (env RIBONANZANET_SS_CHECKPOINT): import
  RibonanzaNetSS from the baked source, load the checkpoint, run the forward pass, and
  read the base-pairing probability matrix off the contact head. Raises on any failure —
  never silently falls back to the stub.
* Stub — only on explicit opt-in (SCIABLE_ALLOW_STUB=1, CI/dev): a deterministic
  placeholder matrix, engine="stub", byte-identical to the fake backend's.
  Without the checkpoint AND without the opt-in the job fails loudly.

Real-path config (all baked into the GPU worker image):
    RIBONANZANET_SS_CHECKPOINT  path to ribonanzanet_ss.pt (gates the real path)
    RIBONANZANET_REPO           repo root holding tools/ribonanzanet_sec_struct/ (added to sys.path)
    RIBONANZANET_SS_CONFIG      path to the network's config.yaml
    RIBONANZANET_DEVICE         torch device, default "cuda:0"

I/O contract (shared by every runner):
    --input <input.json>   {"sequence": ..., "seed": ...}
    --out   <out.json>     {"sequence", "matrix" (LxL, 0..1), "matrix_type", "engine"}
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


def _log(msg: str) -> None:
    """Progress line on stderr; the handler forwards these live to Runpod's logs."""
    print(f"[ribonanzanet +{time.monotonic() - _T0:7.1f}s] {msg}", file=sys.stderr, flush=True)


class RealInferenceError(RuntimeError):
    """The real RibonanzaNet-SS path was configured but failed — surface, don't fake."""


def _stub_matrix(sequence: str) -> list[list[float]]:
    """Deterministic symmetric placeholder matrix in [0,1] — NOT a prediction.
    Byte-identical to app.backends.fake._contact_matrix so stub output is the same on
    both backends (and matches structRFM's stub shape)."""
    n = len(sequence)
    mat: list[list[float]] = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            h = hashlib.sha256(f"{sequence}:{i}:{j}".encode()).digest()[0]
            v = round(h / 255, 4)
            mat[i][j] = mat[j][i] = v
    return mat


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


def _patched_config(src_config: str, work: Path) -> str:
    """Copy the network's config.yaml into the private work dir with
    use_grad_checkpoint disabled — gradient checkpointing is a training-only feature,
    pointless under a no-grad forward pass and a source of no_grad warnings/errors.
    Returns the path to the patched copy."""
    import yaml  # noqa: PLC0415

    cfg = yaml.safe_load(Path(src_config).read_text())
    cfg["use_grad_checkpoint"] = False
    out = work / "ribonanzanet_ss_config.yaml"
    out.write_text(yaml.safe_dump(cfg))
    return str(out)


def _real_matrix(sequence: str, checkpoint: str, work: Path) -> list[list[float]]:
    """Run RibonanzaNet-SS's forward pass and return the L×L base-pairing probability
    matrix (the contact head's sigmoid output), symmetrized. Mirrors the model's own
    ``predict`` matrix step but skips its arnie/Hungarian dot-bracket post-processing."""
    repo = os.environ.get("RIBONANZANET_REPO")
    if not repo or not Path(repo).exists():
        raise RealInferenceError(f"RIBONANZANET_REPO not set or missing (got {repo!r}).")
    config = os.environ.get("RIBONANZANET_SS_CONFIG")
    if not config or not Path(config).exists():
        raise RealInferenceError(f"RIBONANZANET_SS_CONFIG not set or missing (got {config!r}).")
    device_str = os.environ.get("RIBONANZANET_DEVICE", "cuda:0")
    _check_gpu(device_str)

    if repo not in sys.path:
        sys.path.insert(0, repo)

    import contextlib  # noqa: PLC0415

    import torch  # noqa: PLC0415
    from tools.ribonanzanet_sec_struct.network import RibonanzaNetSS  # noqa: PLC0415

    device = torch.device(device_str if torch.cuda.is_available() else "cpu")
    cfg_path = _patched_config(config, work)
    _log(f"loading RibonanzaNet-SS (len={len(sequence)})")
    # __init__ reads the YAML + checkpoint and writes an arnie_file.txt into the CWD
    # (the handler makes CWD a private, writable per-job dir). We never call .predict()
    # (which would import arnie) — we read the contact head directly.
    # Upstream __init__ prints "Loading ... checkpoint: <path>" to stdout; redirect that
    # to stderr so the baked checkpoint PATH never lands in the returned stdout/raw
    # (stdout carries only our path-free JSON report; paths are internal plumbing).
    with contextlib.redirect_stdout(sys.stderr):
        model = RibonanzaNetSS(
            config_filepath=cfg_path, checkpoint_filepath=checkpoint, device=str(device)
        )
        model = model.to(device)
        model.eval()

    tokens = getattr(model, "tokens", {"A": 0, "C": 1, "G": 2, "U": 3})
    try:
        ids = [tokens[c] for c in sequence]
    except KeyError as exc:
        raise RealInferenceError(f"sequence has a base with no token: {exc} (ACGU only).") from exc

    _log("running forward pass; reading base-pairing probability matrix")
    with torch.no_grad():
        seq_t = torch.tensor(ids).int().unsqueeze(0).to(device)
        mask = torch.ones_like(seq_t)
        model.forward(seq_t, mask)
        pw = model.pairwise_features
        pw = pw + pw.permute(0, 2, 1, 3)  # symmetrize
        preds = model.ct_predictor(model.dropout(pw)).sigmoid().squeeze(-1)  # (1, L, L)
    mat = preds.squeeze(0).float().cpu().numpy()
    n = len(sequence)
    if mat.shape != (n, n):
        raise RealInferenceError(f"matrix shape {mat.shape} != ({n},{n}).")
    _log(f"done: {n}x{n} matrix")
    return [[round(float(v), 4) for v in row] for row in mat]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    inputs = json.loads(Path(args.input).read_text())
    sequence = str(inputs.get("sequence") or "").upper()

    home = Path(os.path.expanduser("~"))
    seen_at_start = sorted(p.name for p in home.iterdir()) if home.exists() else []

    checkpoint = os.environ.get("RIBONANZANET_SS_CHECKPOINT")
    ckpt_exists = bool(checkpoint and Path(checkpoint).exists())
    # Report only a boolean, never the checkpoint PATH (internal plumbing, not a result).
    diag = {"ckpt_exists": ckpt_exists}
    _log(f"start: checkpoint_present={ckpt_exists} length={len(sequence)}")

    if ckpt_exists:
        # A configured real path that fails must error (not silently fall to stub).
        try:
            matrix = _real_matrix(sequence, checkpoint, Path.cwd())
        except Exception as exc:  # noqa: BLE001 - surface the reason to the caller
            raise RealInferenceError(f"RibonanzaNet-SS inference failed: {exc}") from exc
        engine = "ribonanzanet"
    elif os.environ.get("SCIABLE_ALLOW_STUB") != "1":
        # Refuse to fake a prediction. A worker without its checkpoint is a
        # misconfigured deployment (wrong image or endpoint); a stub "success" here
        # has masked exactly that before. Stub stays available for CI/dev only.
        print(
            "Internal error: job failed — RIBONANZANET_SS_CHECKPOINT is unset or the "
            "file is missing, so this image cannot run real RibonanzaNet-SS inference "
            "(wrong image/endpoint for this method?). Stub fallback is disabled outside "
            "CI/dev; set SCIABLE_ALLOW_STUB=1 to permit it.",
            file=sys.stderr,
        )
        return 1
    else:
        cache = home / ".ribonanzanet_cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "weights.marker").write_text("stub-cache")
        matrix = _stub_matrix(sequence)
        engine = "stub"

    _log(f"done: engine={engine}")
    Path(args.out).write_text(json.dumps({
        "sequence": sequence,
        "matrix": matrix,
        "matrix_type": "base_pairing",
        "engine": engine,
    }))
    print(json.dumps({
        "engine": engine,
        "stub": engine == "stub",
        # Leak-free isolation signal (see run_grnade.py); never emit the absolute home path.
        "fresh_home": not seen_at_start,
        "length": len(sequence),
        "format": "base_pairing_matrix",
        "diag": diag,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
