# Sciable

**Lovable for Scientists** — turn a working research example into a versioned
scientific method with a web form and an asynchronous API.

v1 ships exactly one method: **gRNAde** (RNA sequence design for a target 3D
structure), designed to run on a GPU in the **operator's own Runpod account
(BYOK)**. The app owns no compute, has no checkout/credits, and never resells GPU
time. See [`AGENTS.md`](AGENTS.md) for scope and [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)
for build order.

> **Status.** The control plane and the worker's job-isolation layer are built and
> tested end to end **with no GPU and no credentials** (a deterministic fake
> backend drives the run lifecycle). The live gRNAde model, the Runpod adapter,
> and the real BYOK run are **pending** — they require the upstream gRNAde
> checkpoint and a Runpod account. Steps that need those are marked **PENDING**
> below. The bundled example structure and the design sequences produced by the
> fake backend are **synthetic placeholders**, not biological results.

## What works today

- One versioned **method manifest** drives input validation, the web form, and the API.
- `POST /api/runs` + `GET /api/runs/{id}` with the same submission path for the
  browser and API clients; idempotent resubmission; in-flight caps.
- Server-rendered method page and live run/results view (FASTA).
- A **worker** that runs each job in an isolated subprocess (private
  workspace/HOME/temp, cleanup on success and failure) — proven not to leak files
  between jobs.
- Swappable backend: `fake` (default, local/CI) or `runpod` (PENDING M5).

## Local setup

Requires Python 3.12 (the `.devcontainer` provides it).

```bash
pip install -e ".[dev]"      # install app + dev tools
ruff check .                 # lint
pytest                       # full suite (no GPU, no credentials)
```

Run the app against the fake backend:

```bash
uvicorn app.main:app --reload --port 8099
```

Open <http://localhost:8099/>, tick **Use bundled example structure**, and submit.
The results page refreshes until the run reaches `succeeded` and shows candidate
FASTA sequences.

### Configuration

Settings use the `SCIABLE_` env prefix (see [`app/config.py`](app/config.py)):

| Variable | Default | Meaning |
| --- | --- | --- |
| `SCIABLE_BACKEND` | `fake` | `fake` or `runpod` (PENDING) |
| `SCIABLE_DB_PATH` | `./sciable.db` | SQLite file |
| `SCIABLE_OPERATOR_TOKEN` | *(unset)* | When set, the API requires `Authorization: Bearer <token>` |
| `SCIABLE_RUNPOD_API_KEY_ENV` | `RUNPOD_API_KEY` | **Name** of the env var holding the Runpod key (the key itself is never stored) |

## Example API calls

Submit with the bundled example (JSON):

```bash
curl -s -X POST localhost:8099/api/runs \
  -H 'content-type: application/json' \
  -d '{"use_example": true, "design_count": 5, "seed": 1}'
# -> 201 {"id": "...", "state": "queued", ...}
```

Submit your own PDB (multipart):

```bash
curl -s -X POST localhost:8099/api/runs \
  -F pdb_file=@your_structure.pdb -F design_count=5 -F seed=1
```

Poll until terminal (polling reconciles state):

```bash
curl -s localhost:8099/api/runs/<id>
# -> {"state": "succeeded", "sequences": ["ACGU...", ...], ...}
```

An identical resubmission returns the **same** run (idempotency) rather than
launching a second job. Pass `Idempotency-Key: <key>` or `idempotency_key` to set
your own key.

## Interpreting results

- Outputs are **candidate sequences for the supplied backbone**. A `succeeded` run
  does **not** validate folding, stability, or biological function.
- Sequences are returned as FASTA over the RNA alphabet (`ACGU`).
- Model scores are **not** exposed until the adapter can explain their meaning and
  direction (`outputs.scores` is `false` in the manifest).
- `submission_uncertain: true` means the provider's acceptance of the job is
  unknown (e.g. a submit timeout). It is **not** auto-resubmitted — check your
  Runpod account before retrying to avoid a second GPU charge.

## Live / BYOK run — PENDING

Running on real GPUs requires the upstream gRNAde checkpoint and a Runpod account.
See [`docs/SETUP.md`](docs/SETUP.md) for the operator endpoint-setup and teardown
procedure and the current blockers.

## Repository layout

```
app/        FastAPI control plane (config, db, manifest, validation, runs, api, web)
worker/     Deployable worker: handler.py (isolation) + run_grnade.py (stub) + Dockerfile
methods/    grnade/manifest.json + bundled example structure
tests/      pytest suite (control plane + worker isolation)
docs/       Operator setup & teardown guide
```

## Known limitations / pending work

- **M1** — pin the real upstream gRNAde commit + checkpoint and record the baseline
  fixture; replace the stub runner. Manifest digests are placeholders until then.
- **M5** — implement the Runpod backend and verify adapter-vs-upstream parity.
- **M6** — live BYOK demo on two independently owned Runpod accounts; record
  cold-start, execution time, and cost; verify teardown.
- Length/file-size limits in the manifest are provisional until confirmed by real
  smoke runs.
- Single-operator auth only; multi-tenancy, a method catalog, MCP, notebook import,
  and payments are intentionally deferred.
