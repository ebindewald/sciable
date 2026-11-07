# structRFM — method card (rebuild-from-scratch reference)

Everything method-specific needed to rebuild this method's server from zero.
The generic console walkthrough lives in `docs/ENDPOINT_SETUP.md`; follow it
with the values below. Worked narrative: `docs/STRUCTRFM_DEPLOY.md`.

## What it does

Runs the structure-guided RNA foundation model structRFM on a single RNA
sequence and returns the model's last-layer mean attention map — an L×L matrix
(symmetrized, min-max normalized to 0..1) that correlates with base pairing,
including crossing/pseudoknot interactions. It is a learned representation,
**not** a calibrated secondary-structure prediction.

## Status (2026-11-07)

- **Real inference live** on the shared endpoint (engine `structRFM`).
- `provisional: true` — pins below not yet frozen; upstream validation pending.
- Missing checkpoint at runtime = loud failure ("Internal error: job failed"),
  never a stub success; stub runs only under `SCIABLE_ALLOW_STUB=1` (CI).

## Upstream

| | |
|---|---|
| Repo (MIT) | https://github.com/heqin-zhu/structRFM |
| Paper | Zhu et al., Nature 2026 (bioRxiv 2025.08.06.668731) |
| Pinned commit | **PENDING-PIN** (`source_commit` in manifest.json) |
| Checkpoint | `structRFM_checkpoint.tar.gz` from the repo's GitHub **release** (`STRUCTRFM_RELEASE`, currently `latest` — pin a tag) |
| Checkpoint sha256 | **PENDING** (`STRUCTRFM_CKPT_SHA256` build-arg; first build prints it) |

## Worker image

| | |
|---|---|
| Dockerfile | repo-root **`Dockerfile`** (historical: claimed the root slot before per-method Dockerfiles; new methods use `worker/Dockerfile.<id>`) |
| Base image | `pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime` — torch NOT pinned in requirements (modern-GPU-kernel rule; sm_120 hang lesson) |
| Requirements | `worker/requirements-structrfm.txt` — uses the **programmatic `structRFM.infer` API** (not the SSP script); `pyarrow==17` is load-bearing (18+ refuses numpy<2 at import) |
| Checkpoint env | `STRUCTRFM_CHECKPOINT=/opt/structRFM_ckpt` (dir, baked at build from the release tarball) |
| Entrypoint | root `handler.py` (`CMD ["python","-u","handler.py"]`) — method-agnostic; runner `worker/run_structrfm.py` |
| Runner contract | `--input/--out` JSON; real path raises on failure |

## Endpoint (live instance + rebuild values)

| | |
|---|---|
| Name / id | `sciable-grnade-structrfm` / `5gyt0w5he0m4qq` (shared with gRNAde) |
| Create via | console GitHub import, repo `ebindewald/sciable`, branch `main`, Dockerfile Path `/Dockerfile` |
| GPU | 24 GB tier (`AMPERE_24`), all card types, CUDA floor 12.0 |
| Workers / timeout | 0 min / 1 max, idle 5 s, execution timeout 600 s, 5 GB disk |
| App wiring | `SCIABLE_RUNPOD_ENDPOINT_IDS='{"structrfm":"<endpoint-id>", ...}'` |

## Rebuild from scratch

1. Repo pushed to GitHub (image builds from the **pushed** commit).
2. Console → New Endpoint → Import Git Repository → values above
   (`docs/ENDPOINT_SETUP.md` for the click-path and its traps).
3. Watch the Builds tab (~5 min observed; 30-min hard cap).
4. Put the endpoint id in `SCIABLE_RUNPOD_ENDPOINT_IDS`, restart the app.
5. Smoke test (below). Then pin: commit sha, release tag, checkpoint sha256
   from the build log → manifest + Dockerfile args.

## Smoke test (real ≠ fake tells)

```bash
curl -s -X POST localhost:8099/api/runs -H 'Content-Type: application/json' \
  -d '{"method":"structrfm","sequence":"AAAAAAAAAAAA"}'
# poll /api/runs/{id}; expect state=succeeded and raw.engine == "structRFM"
```

- `raw.engine == "structRFM"` (not `"stub"`).
- All-A input → near-uniform low attention (poly-A cannot pair); two different
  equal-length sequences → **different** matrices.
- Failure "Internal error: job failed … STRUCTRFM_CHECKPOINT" = job landed on
  an image without the checkpoint (misrouted endpoint mapping).

## Interpretation & limits

Attention map ≠ base-pair probabilities: no cutoff is calibrated, and values
are only comparable within one run's matrix. Input: single RNA sequence,
A/C/G/U (+N), manifest caps length. A successful run does not validate any
structure or function claim.
