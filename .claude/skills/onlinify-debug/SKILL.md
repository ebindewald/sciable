---
name: onlinify-debug
description: Debug a failed, stuck, or wrong-looking run in the sciable "onlinify" app, on the fake or Runpod backend. Knows where the run record, logs, and artifacts live; how to run the server to reproduce; and — critically — how to pull the REAL worker traceback from the Runpod API, which the app does NOT persist for FAILED jobs. Use when a run shows FAILED / worker_error / an odd state, a run is stuck, output looks wrong, or the user invokes /onlinify-debug.
triggers:
  keywords: ["onlinify-debug", "run failed", "worker_error", "debug run", "why did the run fail", "stuck run", "runpod failed"]
  patterns: ["why did run <id> fail", "debug <id>", "this run failed", "the run is stuck", "run returned worker_error"]
---

# /onlinify-debug

Diagnose a run without searching the repo from scratch each time. The golden rule
for this app: **for a Runpod run, the real error is NOT in the local DB** — the
backend records only a content-free `error_category` and discards Runpod's
`output` on FAILED jobs (`app/backends/runpod.py` `status()` reads `output` only
when `SUCCEEDED`). So the traceback almost always has to come from the Runpod API
(Step 3). Don't stop at "worker_error" — that's a label, not the cause.

`$ARGUMENTS` is the run id (the short id shown in the UI is a prefix of the full
hex id).

## Where everything lives (the map)

| What | Where |
|---|---|
| Run records | `sciable.db` → `run` table (cols: `id, deployment_id, resolved_release_id, state, input_fingerprint, idempotency_key, seed, inputs, provider_job_id, submitted_at, finished_at, error_category, outputs_ref`) |
| Deployment → endpoint | `sciable.db` → `deployment` table (`endpoint_ref`, `exec_limits`, `credential_ref`) |
| Local run artifacts | `runs/<outputs_ref>.outputs.json`, `runs/<outputs_ref>.raw.json`, `*.fasta` |
| Method runners / dispatch | `worker/handler.py` (`RUNNERS` dict), `worker/run_<method>.py` |
| Runpod status mapping | `app/backends/runpod.py` `_STATUS_MAP`, `status()` |
| Runpod key | `.env` → `RUNPOD_API_KEY` (name from `settings.runpod_api_key_env`). **Never print its value.** |
| `sqlite3` CLI | NOT installed — query via `python3` + the `sqlite3` module |

## Step 0 — (optional) reproduce on the local server

To exercise the control plane (manifest, validation, submit→status→results, UI,
bounds) with no GPU/credentials:

```bash
SCIABLE_BACKEND=fake uvicorn app.main:app --port 8099    # fake backend = deterministic stub
# submit:  curl -s -XPOST localhost:8099/api/runs -H 'content-type: application/json' \
#            -d '{"sequence":"GGCACUUCGGUGCC"}' ; then GET /api/runs/<id>
```
⚠️ The fake backend runs the **stub** and will SUCCEED — it does **not** reproduce
GPU/image/endpoint failures (those are Runpod-only). Use it to rule the control
plane *in or out*, not to reproduce a `worker_error` from Runpod.

## Step 1 — Get the run record

```python
python3 -c "
import sqlite3
c=sqlite3.connect('sciable.db'); c.row_factory=sqlite3.Row
r=c.execute(\"SELECT * FROM run WHERE id LIKE ?\", ('<SHORT_ID>%',)).fetchone()
print(dict(r) if r else 'not found')
"
```
Note `state`, `error_category`, `provider_job_id`, `deployment_id`, `outputs_ref`.

## Step 2 — Local artifacts (SUCCEEDED or handler-caught failures)

If `outputs_ref` is set: read `runs/<outputs_ref>.outputs.json` (the method
output) and `runs/<outputs_ref>.raw.json` (the runner report; `raw.engine` tells
you `stub` vs real). For a FAILED Runpod job these are usually **absent** → go to
Step 3.

## Step 3 — Pull the REAL traceback from Runpod (the important step)

The detail lives in the Runpod job result, not the DB. Endpoint = that
deployment's `endpoint_ref` (or `settings.runpod_endpoint_id`).

```python
python3 -c "
import os, json, urllib.request, urllib.error
env={}
for line in open('.env'):                      # load .env WITHOUT printing values
    line=line.strip()
    if line and not line.startswith('#') and '=' in line:
        k,v=line.split('=',1); env[k.strip()]=v.strip().strip('\"').strip(\"'\")
key=env.get('RUNPOD_API_KEY') or os.environ.get('RUNPOD_API_KEY')
endpoint='<ENDPOINT_REF>'; job='<PROVIDER_JOB_ID>'
url=f'https://api.runpod.ai/v2/{endpoint}/status/{job}'
req=urllib.request.Request(url, headers={'Authorization':f'Bearer {key}'})
try:
    print(json.dumps(json.load(urllib.request.urlopen(req, timeout=30)), indent=2)[:4000])
except urllib.error.HTTPError as e:
    print('HTTP', e.code, e.read().decode()[:500])
"
```
This is a **status read — no GPU charge.** Never resubmit a failed job blindly
(double-charge risk); fix the root cause, then rerun once.

## Interpreting the Runpod result

| Signal | Meaning / likely cause |
|---|---|
| `error: unknown_method`, tiny `executionTime` | The endpoint's deployed **image doesn't serve this method** — its `handler.py RUNNERS` lacks the runner, or `deployment.endpoint_ref` points at the wrong (e.g. shared) image. Rebuild/redeploy the right image, or fix `endpoint_ref`. |
| `output.error` + `output.detail` (stderr tail), `status: FAILED/COMPLETED` | Handler-caught `WorkerError`. `detail` is the runner's last stderr. Read it: missing checkpoint, GPU arch unsupported, `inference.py` nonzero, bad output. |
| `status: TIMED_OUT` | Job exceeded the handler timeout (`WORKER_JOB_TIMEOUT_S`, default 540s) or the endpoint's. |
| HTTP 404 | Result TTL expired (or wrong endpoint/job id). Rerun to capture a fresh one. |
| real path fell to `engine: stub` but you expected real | `RHOFOLD_CHECKPOINT`/equivalent not set or missing in the image → baked-weights step skipped. |

## The endpoint/deployment gotcha (bit us on rhofold)

`bootstrap.py` seeds **every** deployment's `endpoint_ref` from the single
`settings.runpod_endpoint_id`. So multiple methods can collide on one endpoint —
and a method only works if that endpoint's image (a) has the method in
`RUNNERS` **and** (b) has its weights baked in. Always check: does
`deployment.endpoint_ref` point at an image that actually serves this method?

**And the image builds from GitHub HEAD, not your working tree.** If you edited
`worker/` (a new runner, a `handler.py RUNNERS` line, a Dockerfile fix) but didn't
**commit + push** and then rebuild + redeploy the endpoint, the endpoint still runs the
old image → `unknown_method`. First triage for a method that "should work now": is the
deployed image built from the latest pushed commit? (`unknown_method` after a local fix =
almost always an unpushed/unrebuilt image.)

**Endpoint existence is a manual, human step** (Runpod web console, Docker Image flow) —
nothing in the repo creates it. If it was never created, or `SCIABLE_RUNPOD_ENDPOINT_ID(S)`
points at a non-existent id → Runpod `/run` returns **404 → `BackendError` → API 502**. If
it points at *another method's* endpoint → **`unknown_method`**. Check: does a Runpod
endpoint for this method actually exist, built from this method's image, and does the app's
endpoint id match it?

## Known gaps worth fixing (don't rediscover these)

1. `app/backends/runpod.py` `status()` discards `output.error`/`output.detail`
   for FAILED jobs → persist them into the run's `raw` so the traceback lands in
   the DB and Step 3 becomes unnecessary.
2. No per-method endpoint config → add one so bootstrap stops defaulting every
   deployment to `settings.runpod_endpoint_id`.
3. A submit-time (or deploy-time) pre-flight that verifies the target endpoint
   serves the method would catch `unknown_method` before spending a job.

## Security / guardrails
- **Never echo `RUNPOD_API_KEY`** (or any secret). Load it, use it, don't print it.
- Status/API reads are free; **never auto-resubmit** a failed job (could double-charge GPU).
- Log timing + error *category* only — never sequence/structure contents.
