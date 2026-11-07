# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

`AGENTS.md` is the authoritative source for scope and intent; `IMPLEMENTATION_PLAN.md` tracks the build order (milestones M0–M7). The **control plane** (`app/`) and the **worker's job-isolation layer** (`worker/`) are built and tested end to end against a deterministic **fake backend** — no GPU, no credentials. Pending milestones: **M1** (pin real upstream gRNAde commit + checkpoint; replace the stub runner), **M5** (Runpod backend + upstream parity), **M6** (live BYOK demo). Manifest digests and length/size limits are **provisional** until M1/M4 smoke runs confirm them.

Read `AGENTS.md` before starting work. The notes below distill the parts that constrain *how* code should be written; `AGENTS.md` wins on any conflict.

**Deploying a method to Runpod has exactly one supported path** — the operator creates the endpoint in the Runpod console via GitHub import (Runpod builds `worker/Dockerfile.<method>` into its private registry): see `docs/ENDPOINT_SETUP.md`, including its **"What NOT to do"** list (never publish images/packages anywhere public, never invent a new build/deploy mechanism, never re-litigate the pinned torch 2.7.1/cu128 base).

## Commands

Python 3.12 (provided by `.devcontainer`).

```bash
pip install -e ".[dev]"          # install app + dev tools (ruff, pytest)
ruff check .                     # lint (must pass; CI gate)
pytest                           # full suite — no GPU, no credentials
pytest tests/test_runs_lifecycle.py::test_happy_path_succeeds_and_writes_fasta  # single test
SCIABLE_BACKEND=fake uvicorn app.main:app --reload --port 8099   # GPU-free (STUB output)
```

Settings use the `SCIABLE_` env prefix (`app/config.py`); the default backend is **`runpod`** (real inference), and the GPU-free **`fake`** backend is **opt-in** via `SCIABLE_BACKEND=fake` — it returns STUB output and logs a loud banner, so a forgotten setting fails on the real backend instead of silently serving stubs. For local tests use `SCIABLE_BACKEND=fake`. The Runpod key is referenced by env-var **name** (`SCIABLE_RUNPOD_API_KEY_ENV`), never stored.

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
- **Honesty machinery must be automatic + fail-loud, never manual + fail-silent.** A safety check that relies on a human remembering to configure or keep it in sync is *not* a safety check — it is a second thing that can be wrong, and a mock that is default-on and honest-only-if-you-remember is worse than no mock because it manufactures false confidence. Concretely: the **safe/real state is the default** (`SCIABLE_BACKEND=runpod`; the GPU-free `fake` backend is opt-in and prints a loud banner when active); the **real-vs-stub tell lives in the artifact** (`engine` in the output), not a flag someone must flip; the worker **fails loud** on a missing checkpoint rather than silently stubbing (`SCIABLE_ALLOW_STUB=1` is CI/dev-only); and when real inference lands, flip `provisional`/`mockup` immediately so real output is never labelled fake (and vice-versa). Lesson earned 2026-11-07: a `fake`-by-default backend plus lagging mockup flags cost hours of stub-mistaken-for-real confusion.
- **Stub output must be unmistakably degenerate.** A placeholder must look obviously fake *to a non-expert on sight* — e.g. gRNAde stub designs are homopolymers (`AAAA…`, `CCCC…`, cycling A/C/G/U), never plausible-looking varied sequences. This is defense-in-depth so stub output is self-evident even if every honesty flag fails at once. Keep each backend's placeholder byte-identical to its runner's stub so there is one obvious shape, not several.

## Build order

Follow this sequence (from `AGENTS.md`); each stage gates the next:

1. **Baseline** — run the pinned upstream gRNAde example in a clean environment; save input, invocation, versions, and output checks as a fixture.
2. **Worker** — build the image; run the same fixture through its request handler; confirm job isolation.
3. **App** — add the manifest, form, run API, and status/results view. Exercise the full lifecycle against a **fake backend** so local checks need no GPU or credentials.
4. **Live demo** — run on an operator-owned Runpod endpoint; repeat on a second independently owned account to prove BYOK; record cold-start, execution time, and cost; verify cleanup.
5. **Handoff** — README covering local setup, image build, endpoint setup, example API calls, result interpretation, and teardown.

**Validate the adapter against upstream** on the same fixture, version, and seed: check output count, alphabet, sequence length, and residue mapping against the processed target. Document any nondeterminism and justify tolerances.
