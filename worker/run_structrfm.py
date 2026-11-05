"""structRFM attention-map runner (invoked as a subprocess by handler.py).

Runs structRFM on an RNA sequence and returns its last-layer mean attention as an
L×L matrix — a learned representation that correlates with base pairing, including
crossing/pseudoknot interactions a nested dot-bracket would discard. It is NOT a
calibrated secondary-structure prediction.

Why attention and not the SSP dot-bracket: upstream's SSP *script*
(scripts/structRFM_SSP.py) imports modules not in the documented install
(param_turner2004 — bundled only in mxfold2 — and a local `utils`), so it is not
cleanly reproducible. We instead use the documented programmatic API
(structRFM.infer.structRFM_infer), which depends only on the structRFM package +
torch. A true dot-bracket head is a deferred follow-up.

Two paths, chosen at runtime:
* Real — when the base checkpoint is present (env STRUCTRFM_CHECKPOINT, baked into
  the GPU worker image): load the model and extract the attention matrix.
* Stub — when it is absent (local/CI, no GPU, no weights): a deterministic
  placeholder matrix, clearly marked engine="stub".

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
    print(f"[structrfm +{time.monotonic() - _T0:7.1f}s] {msg}", file=sys.stderr, flush=True)


class RealInferenceError(RuntimeError):
    """The real structRFM path was configured but failed — surface, don't fake."""


def _stub_matrix(sequence: str) -> list[list[float]]:
    """Deterministic symmetric placeholder matrix in [0,1] — NOT a prediction."""
    n = len(sequence)
    mat: list[list[float]] = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            h = hashlib.sha256(f"{sequence}:{i}:{j}".encode()).digest()[0]
            v = round(h / 255, 4)
            mat[i][j] = mat[j][i] = v
    return mat


def _real_matrix(sequence: str, checkpoint: str) -> list[list[float]]:
    """structRFM last-layer mean attention, symmetrized and min-max normalized."""
    _log("importing torch")
    import torch

    _log(f"torch {torch.__version__}, built for CUDA {torch.version.cuda}")
    _log("checking CUDA availability")
    if torch.cuda.is_available():
        cap = torch.cuda.get_device_capability(0)
        _log(
            f"GPU {torch.cuda.get_device_name(0)}, compute capability sm_{cap[0]}{cap[1]}; "
            f"torch kernels for {torch.cuda.get_arch_list()}"
        )
    else:
        _log("CUDA NOT available - running on CPU")

    _log("importing structRFM")
    from structRFM.infer import structRFM_infer

    _log(f"loading model from {checkpoint} (and moving it to the GPU)")
    model = structRFM_infer(
        from_pretrained=checkpoint,
        max_length=514, dim=768, layer=12, num_attention_heads=12,
    )
    _log(f"model ready on {model.model.device}; running inference, length {len(sequence)}")
    feat = model.extract_feature(sequence)
    _log("inference done; post-processing attention map")
    attn = feat["last_mean_attn_feat"].detach().float().cpu()  # L x L
    sym = 0.5 * (attn + attn.t())
    lo, hi = float(sym.min()), float(sym.max())
    if hi > lo:
        sym = (sym - lo) / (hi - lo)
    return [[round(float(v), 4) for v in row] for row in sym]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    inputs = json.loads(Path(args.input).read_text())
    sequence = str(inputs.get("sequence") or "")

    home = Path(os.path.expanduser("~"))
    seen_at_start = sorted(p.name for p in home.iterdir()) if home.exists() else []

    checkpoint = os.environ.get("STRUCTRFM_CHECKPOINT")
    diag = {"ckpt_env": checkpoint, "ckpt_exists": bool(checkpoint and Path(checkpoint).exists())}
    _log(f"start: checkpoint={checkpoint!r} exists={diag['ckpt_exists']}")

    if diag["ckpt_exists"]:
        # A configured real path that fails must error (not silently fall to stub).
        try:
            matrix = _real_matrix(sequence, checkpoint)
        except Exception as exc:  # noqa: BLE001 - surface the reason to the caller
            raise RealInferenceError(f"structRFM attention extraction failed: {exc}") from exc
        engine = "structRFM"
    else:
        cache = home / ".structrfm_cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "weights.marker").write_text("stub-cache")
        matrix = _stub_matrix(sequence)
        engine = "stub"

    _log(f"done: engine={engine}")
    Path(args.out).write_text(json.dumps({
        "sequence": sequence,
        "matrix": matrix,
        "matrix_type": "attention",
        "engine": engine,
    }))
    print(json.dumps({
        "engine": engine,
        "stub": engine == "stub",
        "home": str(home),
        "seen_at_start": seen_at_start,
        "length": len(sequence),
        "format": "attention_matrix",
        "diag": diag,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
