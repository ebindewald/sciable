# Operator Setup & Teardown (Runpod BYOK)

This guide gets gRNAde running on **your own** Runpod account. The control-plane
adapter (`RunpodBackend`) and a deployable serverless worker are **built**. The
only thing still stubbed is the model itself: the worker currently emits
deterministic **placeholder** designs until the M1 baseline pins real gRNAde.
Deploying now proves the entire BYOK path — account, API key, endpoint, job
submit/status/cancel, isolation, and billing — with trivial GPU cost.

Design goal: your GPU runs bill **your** account. Sciable holds no credits, resells
no compute, and keeps your Runpod key server-side only (referenced by env-var
name, never stored).

## Step 1 — Register and get an API key (you)

1. Create an account at <https://runpod.io> and add a small amount of credit
   (a few dollars is plenty for the stub worker, which needs no GPU time to speak of).
2. Console → **Settings → API Keys → Create API Key**. Give it the minimum scope
   that allows creating/invoking Serverless endpoints. Copy the key somewhere safe.

## Step 2 — Get the worker image onto Runpod (you + this repo)

**Recommended: GitHub integration** (Runpod builds the image for you — no local
Docker, avoids any architecture mismatch).

1. Put this repository on GitHub (private is fine). It is not yet a git repo:
   ```bash
   git init && git add -A && git commit -m "Sciable control plane + worker"
   # create a repo on GitHub, then:
   git remote add origin git@github.com:<you>/sciable.git
   git push -u origin main
   ```
2. In the Runpod console, authorize the GitHub app, then **Serverless → Deploy
   Endpoint → from GitHub**. Point it at your repo and set:
   - **Dockerfile path:** `worker/Dockerfile`
   - **Build context:** repository root (`.`)
   Runpod builds the image, stores it in its registry, and creates the endpoint.

**Alternative: build and push yourself** (needs Docker + a registry account, and
an `linux/amd64` build since Runpod GPU hosts are x86_64):
```bash
docker build --platform linux/amd64 -f worker/Dockerfile -t <registry>/grnade-worker:v0 .
docker push <registry>/grnade-worker:v0
```
Then create the endpoint from that image tag.

## Step 3 — Configure the endpoint (you)

When creating the Serverless endpoint, use development-safe limits:

- **Min workers: 0, Max workers: 1** — no idle GPU cost; one job at a time.
- A conservative **execution timeout** (the app also caps in-flight jobs).
- The **stub** worker needs no GPU — a CPU worker is fine and cheapest. Switch to a
  GPU worker only after M1 swaps in the real model.

Copy the **Endpoint ID** shown in the console.

## Step 4 — Connect Sciable to your endpoint (local)

```bash
export RUNPOD_API_KEY=<your-key>              # value lives only in your shell/secret store
export SCIABLE_BACKEND=runpod
export SCIABLE_RUNPOD_ENDPOINT_ID=<endpoint-id>
export SCIABLE_OPERATOR_TOKEN=<pick-anything> # require a bearer token on the API

uvicorn app.main:app --port 8099
```

The deployment record stores only the endpoint ID, execution limits, and a
credential **reference** (`RUNPOD_API_KEY`) — never the key value.

## Step 5 — Run the example end to end (you)

```bash
curl -s -X POST localhost:8099/api/runs \
  -H "Authorization: Bearer $SCIABLE_OPERATOR_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"use_example": true, "design_count": 5, "seed": 1}'
# -> {"id": "...", "state": "queued", ...}

curl -s localhost:8099/api/runs/<id> \
  -H "Authorization: Bearer $SCIABLE_OPERATOR_TOKEN"
# poll until "state":"succeeded" with "sequences":[...]
```

Confirm in your Runpod console that the job ran **there** (it billed your account).
Note cold-start and execution time. To prove BYOK, repeat steps 1–5 with a second,
independently owned Runpod account and confirm each run appears in the intended
account.

> Until M1, the returned sequences are placeholders. Everything else — the job
> actually executing in your Runpod account — is real.

## Step 6 — Teardown (you)

1. **Set the endpoint to 0 workers** (or delete it) in the console.
2. **Delete the endpoint** if you're done.
3. **Remove any network volume / storage** attached to it (billed separately).
4. **Confirm $0 ongoing charges** in Runpod billing — the app can't see this.
5. Rotate/revoke the API key if no longer needed; unset `RUNPOD_API_KEY`.

## Remaining blocker

| Blocker | Unblocks | Needs |
| --- | --- | --- |
| Upstream gRNAde commit + checkpoint not pinned (M1) | real designs instead of placeholders | network to the upstream repo; redistribution-terms review; swap `run_grnade.py` + switch Dockerfile to a CUDA base + GPU worker |
