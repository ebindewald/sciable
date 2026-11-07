# gRNAde — method card (rebuild-from-scratch reference)

Everything method-specific needed to (re)build this method's server. The generic
console walkthrough lives in `docs/ENDPOINT_SETUP.md`.

## ⚠ Status (2026-11-07): PROVISIONAL — real runner landed, pins not frozen

Phase A is done. `worker/run_grnade.py` now has a **checkpoint-gated real path**
(upstream's `src.*` programmatic API) that **raises on failure and fails loudly
without the checkpoint** — it only falls back to the stub on `SCIABLE_ALLOW_STUB=1`
(CI/dev), matching structRFM/rhofold. `manifest.json` is now `"provisional": true`
with the `"mockup"` flag removed, so the UI shows the **provisional (pre-baseline)**
note instead of the red MOCKUP badge. Clear `provisional` only after a real Runpod
smoke run validates against upstream.

Phase B files are emitted: `worker/requirements-grnade.txt`, `worker/Dockerfile.grnade`,
and the operator runbook `deploy-docs/GRNADE_DEPLOY.md`. What remains (operator, GPU +
BYOK): commit + push → create the `sciable-grnade` endpoint via the console GitHub import
→ smoke run (prove `raw.report.engine == "grnade"`) → validate vs upstream → freeze pins
→ flip `provisional: false`. **Follow `deploy-docs/GRNADE_DEPLOY.md`.**

## What it will do (method contract — already live in the form/API)

Inverse RNA design: given a target 3D backbone (PDB), design candidate RNA
**sequences** predicted to fold to it. Outputs are *candidates for the supplied
backbone* — a successful run does not validate folding or biological function.

| | |
|---|---|
| Inputs | `structure` (pdb_structure, required), `chain` (text), `design_count` (int, default 5, cap 10), `seed` (int) |
| Output | `sequences` (FASTA) |
| Limits (provisional) | `max_sequence_length: 400`, `job_timeout_s: 900`, `max_in_flight: 1` |
| Example | Real 12-nt RNA hairpin — PDB 1ZIH model 1, chain A, `GGGCGCAAGCCU` (`methods/grnade/example/`) |

## Upstream (verified 2026-11-07)

| | |
|---|---|
| Repo | https://github.com/chaitjo/geometric-rna-design |
| Citation | Joshi et al., gRNAde: Geometric Deep Learning for 3D RNA inverse design |
| Code license | **MIT** (verified) |
| Weights license | gRNAde `.h5` is **MIT** on HF; the HF repo also bundles rhofold/ribonanzanet weights under their own LICENSEs (not baked here) — verify if you enable scoring |
| Pinned commit | `2453b18778a1981ac25f314790e5395f1e9ab218` (gRNAde v1.0 release, 2025-12-15) — verified from GitHub, build-unverified |
| Checkpoint | `gRNAde_drop3d@0.75_maxlen@500.h5` (8,726,092 B), `huggingface.co/chaitjo/gRNAde` rev `91491fc71584bf37fee8297781edf7df07b1d349`, **no runtime auto-download** (`torch.load` from a local path). Pinned sha256 `d43454…62971c`; pre-baked + checksummed by the Dockerfile |

> ⚠ Upstream was rewritten for v1.0: the old `gRNAde.py` wrapper is gone. The runner uses
> the new `src.*` programmatic API and omits the RibonanzaNet scoring loop. See
> `deploy-docs/GRNADE_DEPLOY.md` for the full findings.

## Worker image (built by Runpod from Dockerfile.grnade)

| | |
|---|---|
| Dockerfile | `worker/Dockerfile.grnade` (NOT the root Dockerfile — that's structRFM's; 1:1 method↔image↔endpoint) |
| Base image | `pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime`, torch unpinned (modern-GPU-kernel rule) |
| Requirements | `worker/requirements-grnade.txt`; PyG compiled stack installed in the Dockerfile matched to the base torch build. ⚠ Run the checklist B2 import scan at the pinned commit before the first build (deploy doc). |
| Checkpoint env | `GRNADE_CHECKPOINT=/opt/grnade_ckpt/gRNAde_drop3d@0.75_maxlen@500.h5` (baked + checksummed); `GRNADE_REPO=/opt/grnade`, `GRNADE_TEMPERATURE=0.1` |
| Entrypoint | root `handler.py`; runner `worker/run_grnade.py` (real path via upstream `src.*`; `--input/--out` contract + isolated cwd/HOME) |

## Endpoint

- **gRNAde's own endpoint `sciable-grnade` / `scztvh9pjjjoz9`** is built from
  `worker/Dockerfile.grnade` (console GitHub import, 24 GB, 0 min / 1 max). **Real
  inference verified 2026-11-07** on image `...:f910424d6` (commit f910424): a 1ZIH
  12-nt target returned `engine: "grnade"`, `stub: false`, checkpoint loaded, length-12
  A/C/G/U designs, two seeds differing. Cold start ~10-14 s delay + ~5-6 s exec observed.
- ⚠ **App routing still points grnade at the structRFM endpoint.** Set
  `SCIABLE_RUNPOD_ENDPOINT_IDS` so `grnade` → `scztvh9pjjjoz9` (not `5gyt0w5he0m4qq`) and
  restart the app; deployments refresh from that env on startup.
- The old shared endpoint `sciable-grnade-structrfm` / `5gyt0w5he0m4qq` is structRFM's
  image; a gRNAde job there fails loudly (checkpoint missing), never fakes success.

## Smoke test

```bash
curl -s -X POST localhost:8099/api/runs -H 'Content-Type: application/json' \
  -d '{"method":"grnade","use_example":true}'
# poll /api/runs/{id} until succeeded
```

- **Fake backend / stub (no GPU):** returns `design_count` placeholder sequences;
  `raw.report.stub` is `true`, `engine == "stub"`. The expected local result.
- **Real (own endpoint):** `raw.report.engine == "grnade"` (not `"stub"`); designs are
  length == the processed target chain; two seeds give different designs. Validate output
  count, A/C/G/U alphabet, and length against `gRNAde.sample` (not design.py's filtered
  CSV) per `deploy-docs/GRNADE_DEPLOY.md` step 4.
