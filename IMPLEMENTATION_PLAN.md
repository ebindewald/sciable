# Implementation Plan — Sciable v1 (gRNAde on BYOK Runpod)

> Scope authority is `AGENTS.md`; conventions are in `CLAUDE.md`. This plan turns the
> 5-stage build order into concrete modules, schemas, and acceptance checks for **one
> method (gRNAde), one backend (Runpod Serverless)**. Everything that can be faked is
> faked, so stages 1–3 run with no GPU and no credentials.

## Guiding decisions

- **Stack:** Python 3.12, FastAPI, SQLite (via `sqlite3` or SQLModel), Jinja2 server-rendered
  UI, `pydantic` v2 for schemas, `httpx` for Runpod calls, `pytest` for tests, `ruff` for lint.
- **Determinism of local checks:** the Runpod adapter sits behind one interface with a
  `FakeBackend` implementation. The default app config in dev/test uses the fake.
- **No secret ever touches the DB, logs, browser, or job payload.** The DB stores a credential
  *reference* (env var name / secret id); the process reads the actual key at submit time.

## Target repository layout

```
sciable/
  app/
    __init__.py
    main.py              # FastAPI app factory, routes wiring, startup (DB init)
    config.py            # Settings: DB path, backend selector, secret refs, limits
    db.py                # Connection, migrations, schema DDL for the 3 records
    models.py            # Pydantic models: MethodRelease, Deployment, Run, enums
    manifest.py          # Load + validate the versioned method manifest
    validation.py        # Input validation driven by the manifest (PDB, counts, seed)
    runs.py              # Run lifecycle service: create, fingerprint, state transitions
    api.py               # POST /api/runs, GET /api/runs/{id}
    web.py               # GET / (method page), form post -> same submit path
    fingerprint.py       # Stable input fingerprint + idempotency key handling
    backends/
      base.py            # Backend protocol: submit(job) -> provider_id; status(); cancel()
      fake.py            # FakeBackend: deterministic lifecycle for local/CI
      runpod.py          # RunpodBackend: httpx calls to Serverless run/status/cancel
    templates/           # Jinja2: method page, run status/results
    static/
  methods/
    grnade/
      manifest.json      # The versioned method manifest (drives form+API+validation)
      example/           # Bundled RNA-only PDB + expected-shape fixture
  worker/
    Dockerfile           # Immutable worker image (deps + pinned checkpoint)
    handler.py           # Runpod request handler: subprocess-per-job, isolation, cleanup
    run_grnade.py        # Thin wrapper over pinned upstream inference entrypoint
  tests/
    fixtures/            # Baseline input/invocation/output-shape from Stage 1
    test_manifest.py
    test_validation.py
    test_runs_lifecycle.py   # full lifecycle against FakeBackend
    test_api.py
    test_idempotency.py
    test_isolation.py        # two jobs can't see each other's files
  docs/
    SETUP.md             # (Stage 5) local setup, image build, endpoint setup, teardown
  pyproject.toml
  README.md
```

## Data model (SQLite)

Three tables mirroring the three records in `AGENTS.md`; keep them distinct.

- **method_release** — `id` (immutable str PK), `input_schema` (json), `output_schema` (json),
  `source_digest`, `checkpoint_digest`, `worker_image_digest`, `exec_requirements` (json),
  `example_ref`, `citation`, `created_at`.
- **deployment** — `id` PK, `method_release_id` FK, `endpoint_ref` (Runpod endpoint id),
  `exec_limits` (json: max in-flight, timeout_s, size caps), `credential_ref` (secret name,
  **not** the secret), `created_at`.
- **run** — `id` PK, `deployment_id` FK, `resolved_release_id` FK, `state`
  (`queued|running|succeeded|failed|canceled`), `input_fingerprint`, `idempotency_key`
  (unique), `seed`, `design_count`, `provider_job_id`, `submitted_at`, `finished_at`,
  `error_category`, `outputs_ref`. **No sequence/structure content is stored in logs.**

## Method manifest (single source of truth)

One versioned JSON manifest per method release, loaded at startup, used by validation, the web
form, and the API so they cannot diverge. gRNAde manifest fields:

- `method_id`, `version`, `title`, `citation`, `source_commit`, `checkpoint_digest`.
- `inputs`: one PDB upload (max size), one RNA chain selector, `design_count`
  (default 5, min 1, max 10), `seed` (int).
- `outputs`: FASTA list + structured run record; score fields only if direction/meaning known.
- `limits`: max PDB size, max sequence length (filled from real smoke runs in Stage 1/4),
  timeout, max in-flight.
- `example`: path to bundled RNA-only PDB.

## Backend interface

```python
class Backend(Protocol):
    def submit(self, job: JobSpec) -> SubmitResult: ...      # returns provider_job_id or UNCERTAIN
    def status(self, provider_job_id: str) -> JobStatus: ...  # -> normalized run state
    def cancel(self, provider_job_id: str) -> bool: ...       # where supported
```

- `FakeBackend`: deterministic state machine (`queued`→`running`→`succeeded`) with injectable
  failure/timeout/uncertain paths for tests. Returns fixture FASTA matching upstream shape.
- `RunpodBackend`: maps to Serverless `/run`, `/status/{id}`, `/cancel/{id}`; reads key from the
  secret ref at call time; 0-min/1-max worker config in dev. On submit timeout → return
  `UNCERTAIN` (surface to user, **never auto-resubmit**).

---

## Milestones (map to AGENTS.md build order)

### M0 — Project scaffold
Set up `pyproject.toml` (deps + ruff + pytest), app factory, `config.py`, `db.py` with the three
tables and migrations, empty route modules, CI-runnable `pytest`.
**Done when:** `ruff check` passes and an empty `pytest` run is green; `GET /` returns a stub.

### M1 — Baseline (upstream gRNAde) · *AGENTS.md step 1*
Run the pinned upstream example in a clean env. Pin commit + one checkpoint + one RNA-only PDB.
Capture input, exact invocation, versions, and output checks into `tests/fixtures/`. Verify
redistribution terms for code/checkpoint/example before committing any artifact; record citation.
**Done when:** a documented command reproduces the example and the fixture records output count,
alphabet, sequence length, and residue mapping. Establish real length/file-size limits here.

### M2 — Manifest + validation + run service
Write `methods/grnade/manifest.json`. Implement `manifest.py`, `validation.py` (PDB parse, single
RNA chain, bounded count, seed), `fingerprint.py`, and `runs.py` (create run, state transitions,
idempotency). All backed by `FakeBackend`.
**Done when:** `test_manifest`, `test_validation`, `test_idempotency` pass; invalid/ambiguous
structures are rejected with clear errors.

### M3 — App: API + web, full lifecycle on fake backend · *AGENTS.md step 3*
Implement `POST /api/runs` and `GET /api/runs/{id}`; web method page loads example or uploads a
PDB and submits via the **same path** as the API. Status/results view renders FASTA + run record.
Enforce size/count limits and operator-only result access.
**Done when:** `test_api` + `test_runs_lifecycle` exercise queued→running→succeeded and the
failed/timeout/uncertain paths end-to-end with **no GPU and no credentials**.

### M4 — Worker image + isolation · *AGENTS.md step 2*
`worker/Dockerfile` (immutable, deps + pinned checkpoint baked or fetched-and-checksummed),
`handler.py` running each job in a fresh subprocess with its own workspace/home/temp, args passed
as an array, cleanup on success and failure.
**Done when:** the Stage-1 fixture runs through the handler and `test_isolation` proves two
sequential jobs cannot see each other's files.

### M5 — Adapter vs upstream parity
Implement `RunpodBackend`. Compare adapter output to upstream on the same fixture, version, and
seed: output count, alphabet, sequence length, residue mapping. Document nondeterminism and
justify any tolerances.
**Done when:** a parity test (or documented manual check) passes against the fixture.

### M6 — Live demo (BYOK) · *AGENTS.md step 4*
Run the example on an operator-owned Runpod endpoint; repeat on a second, independently owned
account to prove BYOK and confirm each run bills the intended account. Record cold-start,
execution time, and observed cost; verify endpoint + storage teardown; check spend vs the
$100/mo budget. **Mark pending if credentials/cloud auth are unavailable.**

### M7 — Handoff · *AGENTS.md step 5*
`docs/SETUP.md` + README: local setup, image build, endpoint setup, example API calls, result
interpretation, teardown. Record exact commands and unresolved issues.

## Cross-cutting acceptance checks

- Browser and API share one submission path and one manifest-driven validation.
- Idempotency key dedupes repeated submissions; uncertain submit state is surfaced, never
  auto-resubmitted.
- No Runpod key in DB, logs, browser, payloads, or image; logs carry timing + error *category*
  only, never sequence/structure content.
- Results are framed as candidates for the supplied backbone, not validated biology.

## Deferred (do not build in v1)

Public multi-tenancy, method catalog, MCP, notebook import, persistent model processes, payments,
automated endpoint provisioning. Revisit abstractions only when a second method defines the shared
contract.
