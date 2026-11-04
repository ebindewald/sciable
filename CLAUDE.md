# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

`AGENTS.md` is the authoritative source for scope and intent; `IMPLEMENTATION_PLAN.md` tracks the build order (milestones M0–M7). The **control plane** (`app/`) and the **worker's job-isolation layer** (`worker/`) are built and tested end to end against a deterministic **fake backend** — no GPU, no credentials. Pending milestones: **M1** (pin real upstream gRNAde commit + checkpoint; replace the stub runner), **M5** (Runpod backend + upstream parity), **M6** (live BYOK demo). Manifest digests and length/size limits are **provisional** until M1/M4 smoke runs confirm them.

Read `AGENTS.md` before starting work. The notes below distill the parts that constrain *how* code should be written; `AGENTS.md` wins on any conflict.

## Commands

Python 3.12 (provided by `.devcontainer`).

```bash
pip install -e ".[dev]"          # install app + dev tools (ruff, pytest)
ruff check .                     # lint (must pass; CI gate)
pytest                           # full suite — no GPU, no credentials
pytest tests/test_runs_lifecycle.py::test_happy_path_succeeds_and_writes_fasta  # single test
uvicorn app.main:app --reload --port 8099   # run the app on the fake backend
```

Settings use the `SCIABLE_` env prefix (`app/config.py`); `SCIABLE_BACKEND=fake` is the default. The Runpod key is referenced by env-var **name** (`SCIABLE_RUNPOD_API_KEY_ENV`), never stored.

## What this is

**"Lovable for Scientists":** turn a working research example into a versioned "scientific method" exposed through a web form and an asynchronous API. The first and only method for v1 is **gRNAde** (RNA sequence design for a target structure), running the model on a GPU in the **operator's own Runpod account (BYOK)** — the app never owns compute, has no checkout/credits, and never resells GPU time.

Stay narrow: implement exactly one method and one backend. Defer multi-tenancy, a method catalog, MCP, notebook import, persistent model processes, and payments until the single demo works end to end. Add shared abstractions only when a *second* method proves what actually needs sharing.

## Architecture

Default stack (per `AGENTS.md`): **FastAPI + SQLite**, single instance, server-rendered UI.

Three records must stay distinct — this separation is the core design, so preserve it:

- **Method release** — immutable: ID, input/output schemas, source + checkpoint digests, worker-image digest, execution requirements, example, citation.
- **Deployment** — maps a method release to an operator-owned endpoint, execution limits, and a credential *reference* (not the secret itself).
- **Run** — links a deployment + resolved release to job state, input fingerprint, seed, provider job ID, timing, and outputs.

A single **versioned method manifest** drives validation, the web form, *and* the API — they must not diverge. The Runpod integration lives behind a **narrow submit/status(/cancel) interface** so the backend can be faked locally. Keep the method contract general enough to also fit plain code and composite methods later, but do not build those abstractions now.

### Submission and status flow

- `POST /api/runs` — validates inputs, creates a local run, returns its ID. The browser and the API use the **same** submission path.
- `GET /api/runs/{id}` — reports `queued | running | succeeded | failed | canceled`. Returns useful errors for invalid input, worker failure, or timeout. FASTA + run metadata are served **only to the authorized operator**.
- Use an **idempotency key** for repeated submissions. If provider submission times out after the job may already have been accepted, surface the *uncertain* state — never auto-resubmit, which could incur a second GPU charge.

## Non-negotiable constraints

These are safety/correctness boundaries, not style preferences:

- **Secrets:** the operator's Runpod key stays server-side (secret store or env var). Never in browser code, logs, job payloads, or the worker image. Use the minimum key scope.
- **Job isolation:** run each gRNAde job in a fresh subprocess with its own writable workspace, home, and temp paths. Treat uploaded PDBs as *data*; pass arguments as an array (never shell-interpolate); clean up on success and failure. Two sequential jobs must not be able to see each other's files.
- **Worker image:** immutable, with dependencies and approved/pinned-and-checksummed weights baked in. Share only read-only code/assets between runs; keep private inputs/outputs out of all shared caches.
- **Pinning:** pin the upstream gRNAde commit, one checkpoint, and one small RNA-only example. Verify code/checkpoint/example redistribution terms before publishing any artifact. Record upstream citation and sources.
- **Bounds:** enforce upload and result size limits, a bounded design count (default 5, cap 10) with a seed, and caps on in-flight jobs and execution time. Establish actual length/file-size limits from real smoke runs and document them.
- **Logging/privacy:** record timing and error *category* only — never log sequence or structure contents. Restrict result access to the submitting operator.
- **Budget:** ~$100/month development budget is an operating limit; prefer 0 min / 1 max Runpod workers in dev and check real usage in the provider account.
- **Framing:** designs are *candidates* for the supplied backbone. A successful run does not validate folding or biological function. Expose model scores only if their meaning and direction can be explained.

## Build order

Follow this sequence (from `AGENTS.md`); each stage gates the next:

1. **Baseline** — run the pinned upstream gRNAde example in a clean environment; save input, invocation, versions, and output checks as a fixture.
2. **Worker** — build the image; run the same fixture through its request handler; confirm job isolation.
3. **App** — add the manifest, form, run API, and status/results view. Exercise the full lifecycle against a **fake backend** so local checks need no GPU or credentials.
4. **Live demo** — run on an operator-owned Runpod endpoint; repeat on a second independently owned account to prove BYOK; record cold-start, execution time, and cost; verify cleanup.
5. **Handoff** — README covering local setup, image build, endpoint setup, example API calls, result interpretation, and teardown.

**Validate the adapter against upstream** on the same fixture, version, and seed: check output count, alphabet, sequence length, and residue mapping against the processed target. Document any nondeterminism and justify tolerances.
