# Backlog — nice-to-have enhancements (not v1-urgent)

Ideas worth doing, parked with enough spec to pick up later. Not part of the M0–M7
build order in `IMPLEMENTATION_PLAN.md`; pull one here into a milestone when it's time.

---

## Downloadable run bundle (reproducibility zip)

**Idea.** Give the operator a one-click `run-<id>.zip` so a run is "equivalent to having
run the program on your own machine" — the easy-runtime promise without preventing power
users from digging deeper. Inspired by tools like MCFold that hand back the whole run dir.

**Default (no method-card config).** The zip mirrors exactly **what's shown on the results
page**, nothing more:
- `input/` — the operator's own input (sequence / PDB / params + seed)
- `outputs/` — the result as displayed (e.g. RhoFold: predicted PDB; gRNAde: the FASTA/designs)
- `metadata.json` — method id + version, provenance digests (`source_commit`,
  `checkpoint_digest`, `worker_image_digest`), seed, `engine` (real/stub), start/stop
  timestamps, runtime, exit code, and processor info (GPU name / torch device).

**Opt-in extension via the method card.** A method author can declare extra files to fold in
— e.g. intermediate files from helper programs (EternaFold, X3DNA, RhoFold relax outputs). Shape
TBD, roughly:
```yaml
# method-cards/<slug>.yaml
run-bundle:
  include:
    - "rhofold_out/unrelaxed_model.pdb"
    - "eternafold/*.bpseq"
```
Paths are relative to the job work dir; missing optional files are skipped, not an error.

**Guardrails (decided in discussion — these are the spec, not options):**
1. **Whitelist only** — build the zip from the job work dir + a curated `metadata.json`.
   **Never serialize the process environment** (it holds `RUNPOD_API_KEY`); env is out, always.
2. **Never bundle weights or upstream code** — huge, and redistribution-restricted. The capsule
   *references* them by digest, never includes them.
3. **Access control = results access** — served only to the submitting operator via
   `require_operator`; no public/guessable URL.
4. Operator's own content (their sequence/structure) in *their* access-controlled download is
   fine; the "never log content" rule still applies to shared, persistent logs — a separate place.
5. **Size cap + retention** — refuse/truncate above a byte cap; auto-expire old bundles (watch the
   ~$100/mo budget if stored in cloud).
6. **Capture-before-cleanup** — run → zip the whitelisted set → store in the operator-scoped
   results store → *then* `rmtree` the live job dir. Live dirs stay ephemeral/private (isolation
   preserved); the frozen zip is the only persistent artifact.
7. **Relative paths inside the zip** (`outputs/…`, not `/var/lib/sciable/jobs/<id>/…`) — portable,
   and consistent with the default-surface rule that internal paths aren't advertised.

**Sketch.** New `GET /api/runs/{id}/bundle` (operator-only) streaming the stored zip; worker/handler
captures the whitelisted set before cleanup; `metadata.json` assembled from the run record +
runner report. Ship the lean default first; add the method-card `include` list when a method needs it.

**Why not now.** Real inference + honest mockup/stub handling is the v1 priority; this is additive
polish with no blocker depending on it.
