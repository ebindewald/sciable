# Deploying real RhoFold+ on your own Runpod endpoint

How to replace the RhoFold **stub** with real inference and prove it works. The app,
form, validation, isolation, results UI, API, worker runner, and Dockerfile
are already done and tested; this is the GPU + weights step, run in your Runpod account.

**Upstream:** <https://github.com/ml4bio/RhoFold> (Apache-2.0 code) ·
**Checkpoint:** `rhofold_pretrained_params.pt` (<https://huggingface.co/cuhkaih/rhofold>) ·
**Paper:** Nature Methods 2024, <https://doi.org/10.1038/s41592-024-02487-0>

See `docs/METHOD_INTEGRATION_CHECKLIST.md` for the general pattern; this is RhoFold's
Phase B.

---

## What's stub vs real today

- `worker/run_rhofold.py` runs **real** inference when the checkpoint is present (env
  `RHOFOLD_CHECKPOINT`, baked into the image below) — it invokes upstream `inference.py`
  in single-sequence mode and reads back the predicted PDB + per-residue pLDDT.
  Otherwise it falls back to a **stub** (deterministic placeholder PDB, `engine="stub"`).
- The web UI shows a **Mockup** banner while `methods/rhofold/manifest.json` has
  `"provisional": true`. Flip it to `false` in step 4, after validation.
- The tell it's still a stub: the structure is a straight line of P atoms and pLDDT
  depends only on input length. Real RhoFold returns sequence-dependent coordinates.

## Prerequisites

- A Runpod account + API key (server-side; referenced by env-var *name*, never stored).
---

## Steps 1+2 — Endpoint + image (one console step)

Follow **`docs/ENDPOINT_SETUP.md`** — the golden path and the ONLY supported deploy
mechanism (no GHCR, no Docker Hub, nothing published outside the GitHub repo). In
short: console → New Endpoint → Import Git Repository → expand "Repository
Configuration" → **Dockerfile Path `/worker/Dockerfile.rhofold`**, Branch `main` →
name `sciable-rhofold`, Queue, 24 GB tier (all card types), **0 min / 1 max workers**,
600 s timeout → Deploy. Runpod builds the image itself into its private registry
(~5 min observed; done 2026-11-07 → endpoint `bbm66nm4lkagtj`) and rebuilds on every
push to `main`. Copy the **endpoint id**.

**For a reproducible/pinned image** replace the Dockerfile placeholders:
`RHOFOLD_COMMIT=<full-sha>` (don't ship `main`) and `RHOFOLD_CKPT_SHA256` — download
the checkpoint once, `sha256sum` it, and **verify its redistribution license** (code
is Apache-2.0; the weights' terms are separate). Put the built image digest in
`methods/rhofold/manifest.json` (`worker_image_digest`).

## Step 3 — Point the app at it and run

```bash
export RUNPOD_API_KEY=<your-key>
SCIABLE_BACKEND=runpod \
SCIABLE_METHOD=rhofold \
SCIABLE_RUNPOD_ENDPOINT_ID=<endpoint-id> \
uvicorn app.main:app --port 8099
```

Submit and confirm it's **real**, not the stub:

```bash
curl -s -X POST localhost:8099/api/runs -H 'Content-Type: application/json' \
  -d '{"method":"rhofold","sequence":"GGCACUUCGGUGCC"}'
# then GET /api/runs/{id} until succeeded
```

Proof points:
- `raw.engine == "rhofold"` (not `"stub"`).
- The PDB has real, varied coordinates; two different sequences give **different**
  structures; pLDDT varies per residue in a sequence-dependent way.

## Step 4 — Validate against upstream, then un-flag

Run upstream directly on the same sequence + checkpoint, single-sequence mode:

```bash
python inference.py --input_fas query.fasta --single_seq_pred True \
  --ckpt rhofold_pretrained_params.pt --device cuda:0 --output_dir ref_out
```

Compare the adapter vs upstream on the same input: **same residue count, same sequence
order, matching coordinates (within any documented nondeterminism), same pLDDT.** If they
match:
- Set `methods/rhofold/manifest.json` → `"provisional": false` and fill the pinned
  `source_commit` / `checkpoint_digest` / `worker_image_digest`.
- Set real `limits` (`max_sequence_length`, `job_timeout_s`) from the smoke run; document
  cold-start, runtime, and cost against the ~$100/mo budget.

## Teardown

Scale the endpoint to 0 (or delete it) when idle; confirm in Runpod billing that nothing
is running.

---

## Known rough edges (expect a tweak on first run)

- **torch 2.7 compatibility** — RhoFold pins torch 1.10/py3.7 upstream; we build on 2.7.1
  for modern-GPU kernels (the sm_120-hang lesson from structRFM). RhoFold is older than
  structRFM's plain-BERT modules, so API-compat is unverified — the first build/smoke run
  is where you'll find and pin any drift (`worker/requirements-rhofold.txt`).
- **pLDDT source** — the image defaults to `RHOFOLD_RELAX_STEPS=0` and reads
  `unrelaxed_model.pdb` (no OpenMM/pdbfixer). If the unrelaxed PDB has no pLDDT in its
  B-factor column, the runner fails loud ("no parseable pLDDT"): set
  `RHOFOLD_RELAX_STEPS=1000` and uncomment `openmm`/`pdbfixer` in the requirements, then
  rebuild.
- **Runtime weight download is unconditional upstream** — `inference.py` calls
  `snapshot_download(repo_id='cuhkaih/rhofold', local_dir=Path(ckpt).parent)` with **no**
  "already present" guard (verified against `main`). The image handles this by baking the
  **full** repo into that dir at build time (via `snapshot_download`, so the HF offline
  metadata is baked too) and running with `HF_HUB_OFFLINE=1`, so the call resolves locally
  with no network. On the smoke run, confirm there's no outbound fetch / hang at
  model-load. If upstream changes the repo id or filename, update `RHOFOLD_CKPT_REPO` /
  `RHOFOLD_CHECKPOINT`.
- **`inference.py` flags** — verified against the current `main` (`--input_fas`, `--ckpt`,
  `--device`, `--relax_steps`, `--output_dir`, `--single_seq_pred` taking a value; writes
  `unrelaxed_model.pdb` always, `relaxed_<steps>_model.pdb` only when steps > 0; OpenMM is
  imported lazily). Re-verify these at your pinned commit.
- **Per-method endpoints** — the Runpod backend resolves the endpoint from the **active
  method** via `runpod_endpoint_for(method)`: it uses `SCIABLE_RUNPOD_ENDPOINT_IDS` (a
  `{method_id: endpoint_id}` map) if set, else falls back to the single
  `SCIABLE_RUNPOD_ENDPOINT_ID`. Give rhofold its **own** endpoint —
  `SCIABLE_RUNPOD_ENDPOINT_IDS='{"rhofold":"<rhofold-ep>"}'`, or just run with
  `SCIABLE_METHOD=rhofold` + a matching `SCIABLE_RUNPOD_ENDPOINT_ID`. Reusing another
  method's endpoint returns `unknown_method` (its image has no rhofold runner) — this is
  exactly how the first rhofold run failed.
