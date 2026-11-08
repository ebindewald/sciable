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

---

## Method tags on the homepage

**Idea.** Surface each method's `tags` on the landing page — as chips on the method card, and
(optionally) as a filter/grouping so a visitor can narrow to e.g. `RNA`, `3D-structure`,
`single-sequence`.

**Current state.** Tags already exist in the **method cards** (`method-cards/<slug>.yaml`, e.g.
grnade: `[RNA, inverse-folding, sequence-design, 3D-structure, geometric-deep-learning]`), but they
do **not** flow anywhere machine-facing: not in `methods/<slug>/manifest.json`, not in the manifest
model (`app/manifest.py`), not in `app/templates/index.html`.

**Work.**
1. Plumb tags into the manifest so the homepage (which renders from manifests, not cards) can see
   them — either copy `tags` from the card into `manifest.json`, or load the card alongside. Add a
   `tags: list[str]` field to the manifest model.
2. Render tags as chips on the landing card (and the method page) in `index.html` / `method.html`.
3. Optional: client-side filter/group by tag (no backend needed for a handful of methods).

**Guardrails / notes.** Keep tags a display/browse aid only — never let them drive validation or
routing (that stays manifest-schema + explicit method id). Normalize casing/spelling across cards
(e.g. rhofold uses `3D structure` with a space vs grnade's `3D-structure`) so chips coalesce.

**Why not now.** Pure browse-UX polish; no method depends on it. Naturally pairs with a future
method-catalog/search once there are more than a few methods.

---

## Candidate methods to onlinify next

RNA-first, spanning large foundation models down to tractable small ones. Feasibility is judged
against the current tier (**24 GB AMPERE, 0–1 workers, ~$100/mo**); a 40B-class model does not fit
24 GB and would need a bigger (pricier) GPU. As always: verify **code AND weights licenses**
separately before any build, prefer a self-contained single-sequence mode, and pre-bake + checksum
the checkpoint.

- **Evo2** (Arc Institute / NVIDIA) — genomic foundation model over DNA/RNA/protein nucleotide
  sequences (generation, variant-effect prediction, embeddings), very long context (~1M tokens).
  Sizes **7B and 40B**. *Feasibility:* 40B won't fit 24 GB; 7B fits at fp16 only with **short**
  context (long context explodes KV-cache memory) — anything ambitious likely needs a larger GPU
  tier (cost ↑). *Smaller alternatives to settle for:* **Evo-1** (7B, StripedHyena), **Nucleotide
  Transformer** (~50M–2.5B), **RNA-FM** (~100M), **GenSLM**. Start small to prove the lane; scale the
  GPU only if the science demands it.
- **RibonanzaNet** — RNA chemical-reactivity / secondary-structure model. **Lowest-hanging fruit:**
  its checkpoints already ship in the gRNAde HF repo (`chaitjo/gRNAde` — the ~43 MB `ribonanzanet.pt`
  / `ribonanzanet_ss.pt` we already looked at), small and 24 GB-trivial. Natural next method, and it
  would later let gRNAde expose the scoring/filtering path we deliberately skipped.
- **RNA-FM** — RNA foundation-model embeddings (~100M params); tiny and fast. Good "embeddings/array
  output" method to prove the manifest + results UI handle vector outputs, not just sequences/structures.
- **Boltz-1** (or **Chai-1**) — open AlphaFold3-style biomolecular structure prediction incl.
  RNA and complexes. High interest; heavier (careful GPU sizing + MSA handling). A flagship to attempt
  once the small methods are smooth.
- **ESM-2** (if widening to protein) — protein language model, 8M→15B; the small variants
  (35M–650M) are 24 GB-trivial and give embeddings / variant scoring. A useful breadth test that the
  framework generalizes beyond RNA.
- **Emu2 / BioEmu** (Microsoft Research) — generative model that samples a protein's **structural
  ensemble** (a distribution of equilibrium conformations), not a single static fold. Scientifically
  rich: complements single-structure predictors (RhoFold / Boltz) by capturing flexibility and
  multiple states. *Integration note:* the output is **many structures per run**, so it would need a
  new output kind (structure-ensemble / multi-model PDB + per-conformer metadata) — a good forcing
  function for the manifest/UI to handle ensembles, and it pairs naturally with the run-bundle idea
  above (zip of all conformers). *Feasibility:* generative ensemble sampling is heavier than a single
  forward pass but likely far more tractable on 24 GB than a 40B LM; **verify exact model
  size, any MSA/embedding input requirement, and sampling cost per ensemble at build** (details are
  at the edge of current knowledge — confirm against the upstream repo/paper, don't trust memory).

**Selection heuristic.** Prove the lane with a small, self-contained model first (RibonanzaNet or
RNA-FM), then attempt a flagship (Evo2-7B short-context, or Boltz-1) once the per-method cost is low
and we know the GPU/cost envelope.

---

## Batch mode / persistent model (amortize the per-job reload)

**Idea.** Today each job runs in a fresh subprocess that reloads the model (~1–3 s) — fine for
interactive single requests (3 s ≈ 0.1 s to a human), but a fixed tax that dominates in **batch**:
folding N sequences costs N×(load + infer) instead of one load + N×infer. On ~1,000 sequences
that's ~50 min of pure reloading.

**Options when batch matters (not before):**
- A **batch input** (many sequences in one job) so one subprocess loads the model once and loops —
  keeps per-job isolation (one job = one workspace) while amortizing the load. Simplest win; a new
  input shape + output list.
- A **persistent model process** (load once per warm worker, serve many requests) — the item
  deferred in `AGENTS.md`. Fastest per request but gives up the clean per-job subprocess isolation,
  so it's a real trade, not a free win.
- Runpod-side: raise `idleTimeout` / enable **FlashBoot** to cut cold-start reloads between bursts.

**Why not now.** Interactive latency is already fine; this only pays off under batch/throughput
workloads (e.g. folding or probing many sequences at once). Revisit when that use case is real.
