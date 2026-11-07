---
name: onlinify-method
description: Turn an upstream AI model (a GitHub repo or Python package — not necessarily pure PyTorch) into a new sciable method exposed through the web form + async API, following the manifest + runner + one-registry-line pattern. Does the whole GPU-free control-plane half autonomously (manifest, output kind, fake-backend placeholder, worker runner, tests — green under pytest), then emits the Phase B build files (requirements, Dockerfile, GHA workflow, deploy doc) with a handoff checklist for the GPU/weights steps you run in your own Runpod account. Use when the user invokes /onlinify-method, or asks to "add/spin up/onlinify a method", "wrap <model> as a webservice", or "integrate <RNA model>".
triggers:
  keywords: ["onlinify", "onlinify-method", "add a method", "spin up a method", "wrap a model", "integrate a model", "new method"]
  patterns: ["onlinify <model>", "add <model> as a method", "wrap <model>", "spin up <model>", "make a webservice for <model>"]
---

# /onlinify-method

Integrate a new model as a sciable **method**. The control plane is method-agnostic:
a method = **a manifest + a runner + one `RUNNERS` line**. This skill executes that
pattern end to end, with sensible defaults so it needs minimal supervision.

**Authoritative runbook:** `docs/METHOD_INTEGRATION_CHECKLIST.md` — read it first; this
file is the operational wrapper. Worked examples: `docs/RHOFOLD_DEPLOY.md`,
`docs/STRUCTRFM_DEPLOY.md`. Reference methods to copy: `methods/rhofold/`,
`methods/structrfm/`, `worker/run_rhofold.py`, `worker/run_structrfm.py`.

`$ARGUMENTS` is the target model — a repo URL, a package name, or a prose name
(e.g. "RhoFold+", "https://github.com/ml4bio/RhoFold", "mxfold2").

## Operating principle

**Do Phase A autonomously. Gate Phase B.** Phase A is GPU-free, credential-free, and
ends with a green `pytest` — run it to completion without stopping. Phase B needs a GPU
and the weights (BYOK, the operator's Runpod account) — you produce the files and hand
off; you do **not** build images or hit Runpod yourself.

Stop to ask the user **only** for facts you cannot verify or safely default (batch them
into one `AskUserQuestion`):
- An output representation that's genuinely ambiguous (e.g. a model emits both a matrix
  and a dot-bracket — which is the headline output?).
- A **license risk** you surfaced (weights license unclear/non-redistributable).
Everything else: pick the documented default, state it, proceed.

## Step 1 — Verify upstream (never trust memory)

Use WebSearch/WebFetch (or an Explore/general-purpose agent) to establish, from the real
repo at its current state:
- Official repo + **license for code AND weights separately** (they differ; Apache code
  can ship with restricted weights).
- The inference entry point and its **exact** CLI flags / API call (fetch the actual
  `inference.py`/`infer` source — don't paraphrase the README).
- The checkpoint: name, URL, approx size, and **whether it auto-downloads at runtime**
  (if so, it must be pre-baked — a hard constraint).
- Any MSA/database/network dependency (prefer a self-contained single-sequence mode;
  defer MSA-upload).

Fill the **5 scoping answers** from `METHOD_INTEGRATION_CHECKLIST.md` ("Before you
start"). Report them back in one short block before editing.

## Step 2 — Map to the contract

- Inputs → field types in `app/manifest.py` `FIELD_TYPES`
  (`rna_sequence | pdb_structure | text | int`).
- Output → a kind in `OUTPUT_KINDS`
  (`sequences | structure | structure_3d | contact_map`).
- **New kind only if none fit.** If needed, it's exactly 4 touch points (Step A2 of the
  checklist): `app/manifest.py`, `app/api.py` (serialize + metrics), `app/backends/fake.py`
  (placeholder), `app/web.py` + `templates/run.html`. Touching `runs.py`/`db.py`/
  `models.py`/endpoints means you've left the generic path — stop and reconsider.

## Step 3 — Phase A (do it all, then prove it)

Follow checklist steps A1–A5, copying the closest existing method:
1. `methods/<id>/manifest.json` (`provisional: true`, pins = `PENDING-PIN`/`PENDING-BUILD`,
   every deferred decision captured in `notes`).
2. `methods/<id>/example/<file>` — a small RNA-only example; set `example_ref`.
3. New output kind across the 4 touch points (only if Step 2 said so). The fake-backend
   placeholder **must be byte-identical** to the runner's stub generator.
4. `worker/run_<id>.py` — `--input/--out` JSON; **real-vs-stub branch gated on the
   checkpoint env var**; real path **raises on failure, never falls back to stub**; keep
   the fail-fast GPU arch check. Add the `RUNNERS` line in `worker/handler.py`.
5. Tests: `tests/test_<id>.py` (copy `tests/test_rhofold.py`) + a stub-path case in
   `tests/test_isolation.py`.

**Gate:** run `ruff check . && pytest`. Iterate until fully green. **Then actually run
the server and exercise it by hand** — pytest alone misses manifest/serialization/UI
glitches: `SCIABLE_BACKEND=fake SCIABLE_METHOD=<id> uvicorn app.main:app --port 8099`,
submit the example (UI form or `curl -XPOST /api/runs`), open the run page, confirm the
output renders and the Mockup banner shows. (Fake backend = stub, so this proves the
control plane, not the GPU path.) Do not proceed to Phase B until both pass. This is the
natural commit point (the user does commits — suggest the command, don't run it).

## Step 4 — Phase B files (emit, don't execute)

Produce, with `PENDING`/`REPLACE_WITH_VERIFIED_SHA256` placeholders:
- `worker/requirements-<id>.txt` — on the **torch 2.7.1 / CUDA 12.8 base**, torch
  **unpinned** (the modern-GPU-kernel rule); modern py3.11-compatible versions, NOT
  upstream's old py3.7 pins.
- `worker/Dockerfile.<id>` — pre-bake + checksum the checkpoint (no runtime download),
  pin the upstream commit via build-arg, non-root. Use `worker/Dockerfile.<id>` (NOT the
  root Dockerfile — Runpod auto-detects only one, already taken) and build it via GHA.
- `.github/workflows/build-<id>-worker.yml` — copy the RhoFold one; builds linux/amd64 →
  GHCR, prints the digest.
- `docs/<ID>_DEPLOY.md` — copy the structRFM/RhoFold deploy doc; the operator's Phase B
  runbook (pin, build, endpoint at 0/1 workers, prove real≠stub via `raw.engine`,
  validate vs upstream, flip `provisional:false`, teardown).

## Step 5 — Report

Summarize in this shape:
- The 5 scoping facts + any default you chose and why.
- Phase A: files added/changed, `pytest` result (counts).
- Any **license or compatibility risk** flagged (e.g. torch-2.7 API-compat unverified).
- Phase B handoff: the ordered checklist (pin → verify weights license → build via GHA →
  **its own endpoint** → smoke run **(confirm the endpoint actually serves this method —
  a reused/stale image returns `error: unknown_method` in ~70ms)** → validate → un-flag →
  teardown) and which doc to follow. Each method needs an endpoint whose image includes
  its runner + weights; `bootstrap.py` defaults every deployment to the one
  `SCIABLE_RUNPOD_ENDPOINT_ID`, so reusing another method's endpoint silently fails.
  (Debugging a failed run: `/onlinify-debug`.)
- The run command to drive it locally now on the fake backend:
  `SCIABLE_METHOD=<id> uvicorn app.main:app --port 8099`.

## Guardrails (from CLAUDE.md / AGENTS.md — non-negotiable)
- Secrets (Runpod key) stay server-side; never in code, logs, payloads, or image.
- Job isolation: fresh subprocess, private work/home/tmp, args as an array, cleanup.
- Pin commit + checkpoint + example; verify redistribution terms before publishing.
- Bounds (length/size/in-flight/timeout) are provisional until a real smoke run; log any cap.
- Log timing + error category only — never sequence/structure contents.
- Designs/predictions are **candidates**; a successful run does not validate function.
- Stay narrow: one method, one backend. No new shared abstractions until a method proves
  the need.
