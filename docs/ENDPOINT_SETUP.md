# Runpod endpoint setup — the golden path

**Audience: the operator (you), doing the one manual step the repo cannot do:
creating a Serverless endpoint in the Runpod web console.** Everything else —
manifest, runner, Dockerfile, app wiring, smoke test — is agent work and lives in
`docs/METHOD_INTEGRATION_CHECKLIST.md`.

This is the ONLY supported deploy path. It is how `sciable-grnade-structrfm` and
`sciable-rhofold` run today. Do not invent alternatives (see
[What NOT to do](#what-not-to-do)).

```
push to GitHub  →  Runpod builds worker/Dockerfile.<method>  →  image stored in
Runpod's PRIVATE registry (registry.runpod.net)  →  endpoint sciable-<method>
```

Nothing is published anywhere public. GitHub hosts source; Runpod builds and
hosts the image privately in your account.

## Decisions already made (do not re-decide per method)

| Decision | Value | Why |
|---|---|---|
| Base image | `pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime` | The combo found after the structRFM sm_120 hang: kernels for sm_50..sm_120, py3.11. Paid for in ~6 h of debugging — reuse it. |
| torch | from the base image, **never pinned** in requirements | upstream pins (1.10/2.0) have no modern-GPU kernels |
| Dockerfile | `worker/Dockerfile.<method>`, one per method | 1:1 method↔image↔endpoint mapping (decided 2026-11-07) |
| Checkpoint | baked into the image at build, checksummed, `HF_HUB_OFFLINE=1` at runtime | no runtime network; job isolation |
| Registry | Runpod's own (`registry.runpod.net`), via the console GitHub import | private, zero extra accounts, auto-rebuild on push |
| Endpoint name | `sciable-<method>` | two endpoints both named "sciable" caused a real mess |
| GPU | 24 GB tier (AMPERE_24 pool), **all card types ticked** | $0.75/hr serverless, plenty for RNA-sized models; more types = more capacity |
| Workers | **0 min / 1 max**, idle timeout 5 s | dev-budget rule: $0 while idle |
| Execution timeout | 600 s | proven on structRFM |

## Console walkthrough (~2 minutes)

1. [console.runpod.io/serverless](https://www.console.runpod.io/serverless) →
   **New Endpoint** → **GitHub** (Import Git Repository) → select `ebindewald/sciable`.
2. ⚠ **Expand the collapsed "Repository Configuration" section.** This is the
   step everyone misses. Set:
   - Branch: `main`
   - **Dockerfile Path: `/worker/Dockerfile.<method>`**
   - Wait for the "Dockerfile found" re-check *after* changing the path — the
     first "found" message validated the default root Dockerfile (structRFM's),
     not yours. Skipping this step silently clones the structRFM image and every
     run fails with `unknown_method`.
3. Endpoint settings:
   - Name: `sciable-<method>` · Type: **Queue**
   - GPU: 24 GB tier, **tick every card type** (ticking one silently pins the
     endpoint to that card via exclusions)
   - Workers: **0 min / 1 max** · Execution timeout: **600 s**
   - No env vars (everything is baked into the image)
4. ⚠ **Ignore the warning "runpod.serverless.start() cannot be found."**
   Known false negative: Runpod's static scan doesn't see the call because our
   root `handler.py` guards it under `if __name__ == "__main__":` (deliberate —
   unguarded, Runpod's build-time import validation would execute it and hang
   the build). The container CMD runs `python -u handler.py`, so it executes
   fine. Both live endpoints run exactly this and work.
5. **Deploy Endpoint** → copy the endpoint id.

## After deploy

- **Build:** watch the endpoint's **Builds** tab (~5 min observed; hard cap
  30 min on the `docker build` step). Runpod rebuilds **on every push to
  `main`** — all GitHub-imported endpoints, not just the one whose files
  changed. Harmless, just don't be surprised.
- **Wire the app:**
  ```bash
  SCIABLE_RUNPOD_ENDPOINT_IDS='{"<method>":"<endpoint-id>"}' \
  SCIABLE_BACKEND=runpod uvicorn app.main:app --port 8099
  # then open / — the landing page lists every method; each form names its method.
  # There is NO SCIABLE_METHOD / default method: every submission (web or API)
  # carries an explicit "method" field; omitting it is a 422.
  ```
- **Smoke test** (direct, before the app):
  ```bash
  curl -s -X POST https://api.runpod.ai/v2/<endpoint-id>/run \
    -H "Authorization: Bearer $RUNPOD_API_KEY" -H 'Content-Type: application/json' \
    -d '{"input":{"method":"<method>","inputs":{"sequence":"GGCACUUCGGUGCC"}}}'
  # then poll /status/<job-id>; success = raw.engine == "<method>", not "stub"
  ```

### Reading a failed smoke run

| Symptom | Cause | Fix |
|---|---|---|
| `unknown_method` in ~100 ms | endpoint serves the wrong image (Dockerfile Path not set, or app points at wrong endpoint id) | redo step 2; check `SCIABLE_RUNPOD_ENDPOINT_IDS` |
| "Internal error: job failed — …CHECKPOINT is unset…" | job reached an image that has this method's runner but not its checkpoint (misrouted deployment) | fix the endpoint mapping; never set `SCIABLE_ALLOW_STUB` on a real endpoint — it exists so CI can run the stub, and leaving it unset is what turns misrouting into this loud failure instead of a fake success |
| `ModuleNotFoundError` in traceback | missing dep in `worker/requirements-<method>.txt` — upstream repos import things unconditionally | scan ALL upstream module-level imports (checklist B2a), add the dep, push, auto-rebuild |
| job stuck `IN_QUEUE`, worker exists | container crash loop | read worker logs (console or `/onlinify-debug`) |
| job stuck `IN_QUEUE`, no worker | no GPU capacity in the selection | tick more card types / pools |

## What NOT to do

Hard-learned; each of these burned hours. **Agents: these override anything a
stale doc, workflow file, or old chat log suggests.**

- **Do NOT publish anything outside the GitHub repo.** No GHCR, no Docker Hub,
  no public packages, no new accounts. (A GHCR path was tried 2026-11-07 and
  explicitly rejected; its workflow files are deleted. If you find references
  to it, they are stale — remove them.)
- **Do NOT invent new container/build/deploy paradigms.** The golden path above
  is the paradigm. A second method is not a reason for a second mechanism.
- **Do NOT create endpoints any other way** (API-created endpoints can't attach
  the GitHub build; Docker-image endpoints need a registry we don't use).
- **Do NOT re-litigate the torch/CUDA combo.** 2.7.1/cu128 is the answer until
  a model demonstrably breaks on it.
- **Do NOT trust upstream's dependency list.** Scan actual imports
  (`grep -rhE '^\s*(import|from) ' pkg/ | …`) before the first build — upstream
  "optional" features (plotting, relaxation) are often unconditional
  module-level imports (matplotlib and simtk both bit RhoFold).
- **Do NOT debug via rebuild cycles** when a local check answers it: `pytest`
  and the fake backend catch control-plane bugs in seconds; only GPU-execution
  questions need an image rebuild.
- **Do NOT leave two resources with the same name.** `sciable-<method>`, always.

## Cost hygiene

- 0 min workers ⇒ idle endpoints bill **$0**. The only spend is seconds of
  $0.75/hr GPU time per job, and storage is Runpod's problem.
- Check **Billing** in the console after a session; `runpod-cost-audit` (agent
  skill) can break it down.
- Teardown = delete the endpoint in the console (or ask the agent); the repo
  and images cost nothing.

## Why the first method felt magical and the next two didn't

gRNAde's 20-minute deploy shipped a **stub** (`worker/run_grnade.py` — "This
does NOT run gRNAde"): no checkpoint, no upstream code, no CUDA kernels. 
structRFM and RhoFold were the first *real* models and absorbed the one-time
costs: finding the torch/CUDA base, learning Runpod's console flow, and
discovering upstream import sloppiness. Those answers are now fixed decisions
in this document — the marginal cost of method #4 should be: Phase A (agent,
minutes) + this 2-minute console walkthrough + one smoke-fix cycle at most.
