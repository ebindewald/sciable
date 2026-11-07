# Adding a method — the repeatable checklist

A mechanical runbook for spinning up a new model as a method, distilled from gRNAde,
structRFM, and RhoFold+. Follow it top to bottom; each step says **what to edit** and
**done when**. The design goal holds: a new method is a **manifest + a runner + one
registry line** — the control plane is method-agnostic, so you add files, you don't
reshape it.

Two phases. **Phase A is GPU-free and finishes locally in one sitting** (green
`pytest`, no credentials). **Phase B needs a GPU + the weights** and runs in your own
Runpod account. Ship A, review, then do B.

---

## Before you start — scope it in 5 answers

Fill these in; they determine every edit below. (Have an agent verify the upstream
facts — repo, license, entry point, checkpoint URL — don't trust memory.)

| Question | Example (RhoFold+) |
|---|---|
| `method_id` (dir name, lowercase) | `rhofold` |
| Upstream repo + **license** (code AND weights separately) | ml4bio/RhoFold, Apache-2.0 code; weights license = verify |
| Inputs → pick field **types** (see table) | `sequence` (`rna_sequence`), `seed` (`int`) |
| Output → pick a **kind** (see table) | `structure_3d` / `pdb` |
| How it runs: CLI entry + checkpoint + **does it auto-download?** | `inference.py --ckpt …`; auto-downloads → must pre-bake |

**Field types** (`app/manifest.py` `FIELD_TYPES`): `rna_sequence`, `pdb_structure`,
`text`, `int`. **Output kinds** (`OUTPUT_KINDS`): `sequences`, `structure`
(dot-bracket), `structure_3d` (PDB+pLDDT), `contact_map` (L×L matrix). If none fit,
adding a new type/kind is Step A4 — otherwise skip it.

---

## Phase A — control plane (no GPU, no weights)

### A1. Manifest + example
- **Add** `methods/<id>/manifest.json` (copy an existing one). Required fields:
  `method_id, version, title, citation, source_commit, checkpoint_digest,
  worker_image_digest, runner, inputs, outputs`. Set `provisional: true` and leave
  pins as `PENDING-PIN` / `PENDING-BUILD`.
- **Add** `methods/<id>/example/<file>` (a small RNA-only example) and point
  `example_ref` at it.
- Put every deferred decision in `notes` (MSA mode, auto-download, license-to-verify,
  provisional bounds) so Phase B reads like a to-do list.
- **Done when:** `python -c "from app.manifest import load_manifest; load_manifest('<id>')"` is clean.

### A2. New output kind? (skip if reusing one)
Only if the output doesn't match an existing kind. Touch exactly these four:
1. `app/manifest.py` — add the string to `OUTPUT_KINDS`.
2. `app/api.py` — a `serialize_run` branch + a `<kind>_metrics()` helper.
3. `app/backends/fake.py` — a deterministic placeholder in `_produce` + its generator.
4. `app/web.py` + `app/templates/run.html` — a render branch.
- **Done when:** nothing else needs editing — `runs.py`, `db.py`, `models.py`, the
  backend contract, and the API endpoints stay untouched. If you're editing those,
  stop; you've left the generic path.

### A3. Validation hook? (usually skip)
Generic per-type validation (alphabet, length, int bounds) covers most methods — no
hook needed (structRFM, RhoFold added none). Add one in
`app/validation.py` `_METHOD_VALIDATORS` **only** for cross-field/structural logic
(e.g. gRNAde parsing a PDB to resolve a chain).

### A4. Lifecycle test
- **Add** `tests/test_<id>.py` (copy `tests/test_rhofold.py`): catalog, method page,
  submit→poll→output shape, bad input = 422, over-length = 422, use-example, run-page
  render shows the mockup banner.
- **Done when:** `ruff check . && pytest` is fully green.

### A5. Worker runner (stub path green now, real path wired for B)
- **Add** `worker/run_<id>.py` (copy `worker/run_structrfm.py` or `run_rhofold.py`):
  `--input/--out` JSON contract; **real-vs-stub branch gated on the checkpoint env
  var**; stub emits a deterministic placeholder matching the fake backend's; print one
  trailing JSON report line. The real path **raises on failure — never silently falls
  back to stub**.
- **Add one line** to `worker/handler.py` `RUNNERS`: `"<id>": "run_<id>.py"`.
- **Add** a stub-path test in `tests/test_isolation.py` (asserts `engine="stub"`,
  output shape, isolation).
- **Done when:** `pytest tests/test_isolation.py` green.

### A6. Run the server and click through (don't trust pytest alone)
- Launch on the fake backend and exercise the real submit→status→results path:
  `SCIABLE_BACKEND=fake SCIABLE_METHOD=<id> uvicorn app.main:app --port 8099`
- Submit the example (UI form or `curl -s -XPOST localhost:8099/api/runs -H 'content-type:
  application/json' -d '{"sequence":"..."}'`), open the run page, confirm the output
  renders and the Mockup banner shows. Catches manifest/serialization/UI issues pytest
  misses.
- **Done when:** a fake-backend run succeeds end-to-end in the browser/API. ⚠ The fake
  backend runs the **stub**, so it canNOT catch GPU/image/endpoint failures (e.g.
  `unknown_method`) — those surface only in Phase B / `/onlinify-debug`.
- **Commit Phase A here.**

---

## Phase B — worker image + live BYOK run (GPU + weights)

See `docs/RHOFOLD_DEPLOY.md` / `docs/STRUCTRFM_DEPLOY.md` for the worked example.

### B1. Pin the three things (reproducibility)
- **Upstream commit** → `source_commit` and the Dockerfile build-arg (never ship
  `main`/`master`).
- **Checkpoint**: download once, `sha256sum` it → `checkpoint_digest` + the Dockerfile
  build-arg. **Pre-bake it into the image** and point the code at the local path so it
  can't auto-download at runtime (breaks isolation + the immutable-image rule).
- **Verify weights redistribution terms** before publishing the image — code license ≠
  weights license.

### B2. Build files
- **Add** `worker/requirements-<id>.txt` and `worker/Dockerfile.<id>`.
- ⚠ **The torch-GPU-arch trap** (bit structRFM *and* applies to RhoFold): upstream's
  pinned old torch has no kernels for GPUs newer than Ampere, and Runpod hands out
  Blackwell (sm_120) slices — the job then **hangs** moving the model to GPU. Build on
  `pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime` and **do not pin torch** in
  requirements (pip would replace it with a kernel-less build). Keep the runner's
  fail-fast GPU arch check.
- ⚠ **Root Dockerfile is single-use**: Runpod auto-detects only the repo-root
  `Dockerfile` (structRFM's). A second method uses its own
  `worker/Dockerfile.<id>` built via **GitHub Actions → GHCR** (Option C), not
  Runpod's GitHub auto-build.
- ⚠ **Dep drift**: upstream's py3.7 pins (`numpy==1.21.2`, …) won't build on the
  py3.11 base. Use modern compatible versions; pin what the first build proves. (See
  the `pyarrow==17` note in `requirements-structrfm.txt` for the flavor of surprise.)

### B3. Build the image (no local Docker needed)
- **Add** `.github/workflows/build-<id>-worker.yml` (copy the structRFM/RhoFold one):
  builds `linux/amd64`, pushes `ghcr.io/<owner>/<id>-worker`, prints the digest.
- Push → watch **Actions** → make the GHCR package **Public** (or add a Runpod
  registry credential).
- **Done when:** the job summary shows the image + digest.

### B4. Endpoint + live run
- Runpod → Serverless → **New Endpoint → Docker Image** → the GHCR ref.
  **0 min / 1 max workers** (dev-budget rule — no idle GPU spend).
- ⚠ **Each method needs an endpoint whose image serves it.** `bootstrap.py` points
  every deployment at the single `SCIABLE_RUNPOD_ENDPOINT_ID`; reuse another method's
  endpoint and its image's `handler.py RUNNERS` won't have your runner → the job returns
  `error: unknown_method` in ~70ms (this is exactly how RhoFold first failed: wired to
  the structRFM endpoint). Give the method its **own** endpoint (or a combined image that
  bakes in this runner + weights), and set the deployment's `endpoint_ref` accordingly.
  First run `unknown_method`? → wrong image on the endpoint; see `/onlinify-debug`.
- Point the app at it and submit:
  ```bash
  export RUNPOD_API_KEY=<key>
  SCIABLE_BACKEND=runpod SCIABLE_METHOD=<id> \
  SCIABLE_RUNPOD_ENDPOINT_ID=<endpoint-id> uvicorn app.main:app --port 8099
  ```
- **The stub-vs-real tell:** real output has `raw.engine == "<id>"` (not `"stub"`), and
  two different sequences give **different** results (the stub depends only on input
  size). Confirm both.

### B5. Validate against upstream, then un-flag
- Run upstream directly on the **same** input + checkpoint + seed. Check: output
  **count, alphabet, length, residue order/mapping** match the adapter. Document any
  nondeterminism and the tolerance you accept.
- Replace `PENDING-*` in the manifest with the real pins; set
  `provisional: false` (the Mockup banner disappears).
- Set the real `limits` (`max_sequence_length`, `job_timeout_s`) from the smoke run —
  watch cold-start + runtime against the ~$100/mo budget. Document the actuals.

### B6. Teardown
- Scale the endpoint to 0 (or delete it); confirm in Runpod billing that nothing runs.

---

## The 10-second mental model
> **manifest + runner + one `RUNNERS` line.** New output kind = 4 known touch points.
> Never touch `runs.py` / `db.py` / `models.py` / endpoints. Phase A is GPU-free and
> green locally; Phase B pins, bakes, builds on torch 2.7, proves real ≠ stub, un-flags.

## Gotcha index (things that will bite, pre-answered)
- Job **hangs** on GPU → old torch, no sm_120 kernels → 2.7.1 base, unpinned torch.
- Runpod "**no Dockerfile found**" → it only auto-detects root `Dockerfile`; use GHA + GHCR for a 2nd method.
- Runpod **build fails with empty log** → it's their builder; Rebuild once, else GHA.
- Model **downloads weights at runtime** → pre-bake + checksum; point code at the local path.
- Output **looks too clean / same per length** → still the stub; check `raw.engine`.
- `pip` **downgrades/crashes** on upstream's old pins → they're py3.7; use modern versions on the 2.7 base.
- Run returns **`unknown_method` / "No runner for method"** → the endpoint's image doesn't serve this method (reused a shared/stale endpoint). Deploy the method's own image/endpoint; check `deployment.endpoint_ref`. (Debug with `/onlinify-debug`.)
