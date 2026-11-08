"""RNA-FM embedding runner (invoked as a subprocess by handler.py).

Runs the RNA-FM foundation model on a single RNA sequence and returns its
per-nucleotide embeddings: an L×640 matrix (one 640-dim vector per base) plus a
mean-pooled 640-dim sequence vector. These are LEARNED FEATURES for downstream
models — not a structure, not a calibrated biological prediction.

Model: the ncRNA RNA-FM (12-layer BERT encoder, 640-dim, ~100M params) — NOT the
sibling mRNA-FM (1280-dim, codon tokenizer). v1 returns embeddings only; downstream
heads (secondary structure, classification, interaction) are out of scope.

Two paths, chosen at runtime:
* Real — when the checkpoint is present (env RNAFM_CHECKPOINT, baked into the GPU
  worker image): load RNA-FM and extract per-residue representations.
* Stub — when it is absent (local/CI, no GPU, no weights): a deterministic,
  obviously-synthetic placeholder matrix, clearly marked engine="stub".

I/O contract (shared by every runner):
    --input <input.json>   {"sequence": ..., "seed": ...}
    --out   <out.json>     {"sequence", "embedding" (LxD), "embedding_dim",
                            "pooled" (D), "engine"}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_T0 = time.monotonic()

#: RNA-FM per-residue embedding width (the real ncRNA model is 640-dim).
STUB_EMBED_DIM = 640


def _log(msg: str) -> None:
    """Progress line on stderr; the handler forwards these live to Runpod's logs."""
    print(f"[rnafm +{time.monotonic() - _T0:7.1f}s] {msg}", file=sys.stderr, flush=True)


class RealInferenceError(RuntimeError):
    """The real RNA-FM path was configured but failed — surface, don't fake."""


def _stub_embedding(sequence: str) -> list[list[float]]:
    """Deterministic, obviously-synthetic placeholder L×640 embedding — NOT real.
    Each value depends only on the (position, dim) indices (a regular moiré band in
    [-1,1]), so two different sequences of the same length get BYTE-IDENTICAL output:
    a dead giveaway that this is a stub, since real RNA-FM embeddings are
    sequence-specific. Kept byte-identical to app.backends.fake._embedding."""
    n = len(sequence)
    return [
        [round(((i * 7 + d * 13) % 101) / 100.0 * 2 - 1, 4) for d in range(STUB_EMBED_DIM)]
        for i in range(n)
    ]


def _mean_pool(embedding: list[list[float]]) -> list[float]:
    """Mean-pool an L×D embedding to a single D-vector. Kept byte-identical to
    app.backends.fake._mean_pool."""
    if not embedding:
        return []
    n = len(embedding)
    d = len(embedding[0])
    return [round(sum(row[j] for row in embedding) / n, 4) for j in range(d)]


def _real_embedding(sequence: str, checkpoint: str) -> list[list[float]]:
    """RNA-FM per-residue embeddings (last representation layer, BOS/EOS stripped)."""
    _log("importing torch")
    import torch

    _log(f"torch {torch.__version__}, built for CUDA {torch.version.cuda}")
    _log("checking CUDA availability")
    if torch.cuda.is_available():
        cap = torch.cuda.get_device_capability(0)
        arch = f"sm_{cap[0]}{cap[1]}"
        archs = torch.cuda.get_arch_list()
        gpu = torch.cuda.get_device_name(0)
        _log(f"GPU {gpu}, compute capability {arch}; torch kernels for {archs}")
        # An unsupported GPU makes the first CUDA allocation hang instead of
        # erroring, so refuse up front (same guard as the other runners).
        level = cap[0] * 10 + cap[1]
        supported = any(
            a.startswith(f"sm_{cap[0]}") and int(a[3:]) <= level for a in archs
        )
        if not supported:
            raise RealInferenceError(
                f"GPU {torch.cuda.get_device_name(0)} ({arch}) is not supported by torch "
                f"{torch.__version__} (kernels for {archs}). Rebuild the image on a newer "
                f"torch or restrict the endpoint to supported GPUs."
            )
        device = torch.device("cuda")
    else:
        _log("CUDA NOT available - running on CPU")
        device = torch.device("cpu")

    _log("importing fm (RNA-FM)")
    import fm

    _log(f"loading RNA-FM from {checkpoint}")
    # Verified against the pinned commit (fm/pretrained.py): rna_fm_t12(path) takes the
    # LOCAL branch (os.path.exists) and loads with theme="rna" — NOT the bare
    # load_model_and_alphabet_local(), whose theme defaults to "protein" (wrong
    # alphabet). No runtime download (HF_HUB_OFFLINE=1). ⚠ The baked file must keep
    # "RNA-FM" in its stem (e.g. RNA-FM_pretrained.pth): the loader derives model_name
    # from the filename, and _has_regression_weights() keys off it to decide whether to
    # co-load a regression .pt — a renamed file would try (and fail) to find one.
    model, alphabet = fm.pretrained.rna_fm_t12(checkpoint)
    batch_converter = alphabet.get_batch_converter()
    model = model.to(device).eval()

    _, _, tokens = batch_converter([("query", sequence)])
    tokens = tokens.to(device)
    _log(f"model ready on {device}; running inference, length {len(sequence)}")
    with torch.no_grad():
        out = model(tokens, repr_layers=[12])
    reps = out["representations"][12]  # (1, L+2, 640), incl. prepended BOS + appended EOS
    # Strip the leading BOS and trailing EOS token to get one vector per nucleotide.
    per_residue = reps[0, 1 : 1 + len(sequence)].detach().float().cpu()
    _log("inference done; serializing embeddings")
    return [[round(float(v), 4) for v in row] for row in per_residue]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    inputs = json.loads(Path(args.input).read_text())
    sequence = str(inputs.get("sequence") or "")

    home = Path(os.path.expanduser("~"))
    seen_at_start = sorted(p.name for p in home.iterdir()) if home.exists() else []

    checkpoint = os.environ.get("RNAFM_CHECKPOINT")
    # Report only a boolean, never the checkpoint PATH (internal plumbing, not a result).
    diag = {"ckpt_exists": bool(checkpoint and Path(checkpoint).exists())}
    _log(f"start: checkpoint_present={diag['ckpt_exists']}")

    if diag["ckpt_exists"]:
        # A configured real path that fails must error (not silently fall to stub).
        try:
            embedding = _real_embedding(sequence, checkpoint)
        except Exception as exc:  # noqa: BLE001 - surface the reason to the caller
            raise RealInferenceError(f"RNA-FM embedding extraction failed: {exc}") from exc
        engine = "rnafm"
    elif os.environ.get("SCIABLE_ALLOW_STUB") != "1":
        # Refuse to fake a result — a missing checkpoint means a misconfigured
        # deployment, not a stub "success" (same gate as the other runners).
        print(
            "Internal error: job failed — RNAFM_CHECKPOINT is unset or the file is "
            "missing, so this image cannot run real RNA-FM inference (wrong "
            "image/endpoint for this method?). Stub fallback is disabled outside "
            "CI/dev; set SCIABLE_ALLOW_STUB=1 to permit it.",
            file=sys.stderr,
        )
        return 1
    else:
        cache = home / ".rnafm_cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "weights.marker").write_text("stub-cache")
        embedding = _stub_embedding(sequence)
        engine = "stub"

    pooled = _mean_pool(embedding)
    dim = len(embedding[0]) if embedding else STUB_EMBED_DIM

    _log(f"done: engine={engine}")
    Path(args.out).write_text(json.dumps({
        "sequence": sequence,
        "embedding": embedding,
        "embedding_dim": dim,
        "pooled": pooled,
        "engine": engine,
    }))
    print(json.dumps({
        "engine": engine,
        "stub": engine == "stub",
        # Leak-free isolation signal (see run_grnade.py); never emit the absolute home path.
        "fresh_home": not seen_at_start,
        "length": len(sequence),
        "dim": dim,
        "format": "rnafm_embedding",
        "diag": diag,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
