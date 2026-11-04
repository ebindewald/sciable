# Deploying real structRFM (M1) on your own Runpod endpoint

How to replace the structRFM **stub** with real inference and prove it works. The
app, form, validation, isolation, results UI, and API are already done and
tested; this is only the "make it predict" step — the part that needs a GPU and
the weights, so it runs in your Runpod account, not locally.

**Upstream:** <https://github.com/heqin-zhu/structRFM> (MIT) ·
**Checkpoint:** `structRFM_SSP_bpRNA1m.pt` (GitHub release asset) ·
**Paper:** Nature 2026 / bioRxiv 2025.08.06.668731

---

## What's stub vs real today

- `worker/run_structrfm.py` runs **real** inference when the SSP script +
  checkpoint are present (env `STRUCTRFM_SSP_SCRIPT` + `STRUCTRFM_SSP_CHECKPOINT`,
  baked into the GPU image below). Otherwise it falls back to a **stub** that
  emits a length-only placeholder and marks `engine="stub"`.
- The web UI shows a **Mockup** banner while `methods/structrfm/manifest.json` has
  `"provisional": true`. You flip that to `false` in step 4, after validation.
- The tell that it's still a stub: every sequence of a given length returns the
  same structure (e.g. `AAAAAAAAAAAA` → `((((....))))`, which is biologically
  impossible). Real structRFM returns `............` for all-A.

## Prerequisites

- A Runpod account + API key (kept server-side; referenced by env-var *name*).
- A container registry you can push to (Docker Hub, GHCR, …).
- Docker with `buildx` (the Runpod GPU hosts are `linux/amd64`).

---

## Step 1 — Get the worker image built

The image recipe is `worker/Dockerfile.structrfm`. Two ways to build it.

### Option A (recommended, no local Docker) — Runpod builds from GitHub

Runpod can pull your repo, build the Dockerfile on its own infrastructure
(linux/amd64 — no Mac/Apple-Silicon arch issues), and deploy the endpoint for you.

1. Push this repo to GitHub (it must contain `worker/Dockerfile.structrfm`).
2. Runpod console → **Serverless → New → deploy from GitHub** → connect your
   GitHub account → pick the repo → set **Dockerfile path** = `worker/Dockerfile.structrfm`.
3. Runpod builds + stores the image and creates the endpoint (continue at step 2).

Caveats: the build must finish in **30 minutes** (ours clones upstream, installs
torch 2.0.1 + transformers + structRFM + BPfold, and downloads the ~300 MB
checkpoint — usually fine), one GitHub account per Runpod account, and the first
build runs **unpinned** (the Dockerfile skips the checksum and prints the
checkpoint's sha256 in the build log — grab it to pin later). Docs:
<https://docs.runpod.io/serverless/github-integration>

### Option B — build locally with Docker Desktop and push

For a reproducible/pinned image, build it yourself. Replace the three pins first
(reproducibility — see CLAUDE.md "pinned-and-checksummed weights"):

1. **Pick the upstream commit.** Find the commit behind the SSP release you want
   and use its full hash as `STRUCTRFM_COMMIT` (don't ship `master`).
2. **Checksum the checkpoint.** Download it once and hash it:
   ```bash
   wget https://github.com/heqin-zhu/structRFM/releases/latest/download/structRFM_SSP_bpRNA1m.pt
   sha256sum structRFM_SSP_bpRNA1m.pt   # -> put this in STRUCTRFM_SSP_SHA256
   ```
   Also pin `STRUCTRFM_RELEASE` to a specific release tag instead of `latest`.
3. **Build + push** for the GPU hosts' architecture:
   ```bash
   docker build --platform linux/amd64 -f worker/Dockerfile.structrfm \
     --build-arg STRUCTRFM_COMMIT=<full-sha> \
     --build-arg STRUCTRFM_RELEASE=<tag> \
     --build-arg STRUCTRFM_SSP_SHA256=<sha256> \
     -t <registry>/structrfm-worker:<tag> .
   docker push <registry>/structrfm-worker:<tag>
   ```
   Record the pushed image digest in `methods/structrfm/manifest.json`
   (`worker_image_digest`) and the commit/checkpoint hashes in `source_commit` /
   `checkpoint_digest`.

## Step 2 — Create the Runpod Serverless endpoint

- New Serverless endpoint from `<registry>/structrfm-worker:<tag>`.
- **Workers: 0 min / 1 max** (dev budget rule — no idle GPU spend).
- A GPU with enough VRAM for the base model (~300 MB weights + activations; a
  small GPU is fine).
- Copy the **endpoint id**.

## Step 3 — Point the app at it and run

The key is read at submit time from the env var *named* by
`SCIABLE_RUNPOD_API_KEY_ENV` (default `RUNPOD_API_KEY`); it is never stored.

```bash
export RUNPOD_API_KEY=<your-key>
SCIABLE_BACKEND=runpod \
SCIABLE_METHOD=structrfm \
SCIABLE_RUNPOD_ENDPOINT_ID=<endpoint-id> \
uvicorn app.main:app --port 8099
```

Submit and check it's **real**, not the stub:

```bash
# A real prediction: all-A cannot pair -> expect mostly/all dots, NOT a hairpin.
curl -s -X POST localhost:8099/api/runs -H 'Content-Type: application/json' \
  -d '{"method":"structrfm","sequence":"AAAAAAAAAAAA"}'
# then GET /api/runs/{id} until succeeded; confirm the raw output shows engine=structRFM
```

Proof points:
- `raw.engine == "structRFM"` (not `"stub"`).
- `AAAAAAAAAAAA` → no base pairs; a genuine sequence folds sequence-dependently
  (two different sequences of equal length give **different** structures).

## Step 4 — Validate against upstream, then un-flag

Run upstream directly on the same sequence and compare:

```bash
python3 scripts/structRFM_SSP.py --gpu 0 --output_format dbn \
  --checkpoint_path structRFM_SSP_bpRNA1m.pt \
  --input_fasta query.fasta --output_dir ref_out
```

Check the adapter vs upstream on the same sequence + checkpoint: **same length,
same alphabet, same dot-bracket, same residue order.** If they match:

- Set `methods/structrfm/manifest.json` → `"provisional": false` (Mockup banner
  disappears) and fill in the pinned `source_commit` / `checkpoint_digest` /
  `worker_image_digest`.
- Document any nondeterminism and the tolerance you accept.

## Teardown

Scale the endpoint to 0 workers (or delete it) when not in use, and confirm in
the Runpod billing view that nothing is still running. Check real usage against
the ~$100/month dev budget.

---

## Known rough edges (expect a tweak on first run)

- **`.dbn` parsing** — `run_structrfm.py:_parse_dbn` globs the output dir for a
  bracket-line whose length matches the input. If structRFM's file naming/format
  differs, adjust that one function once you see real output.
- **SSP script location** — the Dockerfile clones the repo for
  `scripts/structRFM_SSP.py`; verify it exists at your pinned commit (the PyPI
  `structRFM` package may not ship the `scripts/` CLI).
- **Per-method endpoints** — on the Runpod backend every method currently resolves
  to the single `SCIABLE_RUNPOD_ENDPOINT_ID`. Verify structRFM alone for now;
  giving each method its own endpoint is a small follow-up when real gRNAde lands
  (the Deployment record already carries a per-method `endpoint_ref`).
