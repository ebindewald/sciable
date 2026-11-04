# BYOK & the "billing company with a science appendix" — design notes

Rationale behind scoping the Runpod/BYOK layer in sciable, and a parked
startup idea that fell out of the analysis. Written 2026-11-04.

> **TL;DR** — For a single open method under BYOK-with-no-billing there is no
> technical moat, so sciable is deliberately scoped to **single-operator, for
> myself**. The genuinely interesting product that fell out of this is the
> *inverse*: a billing/marketplace platform where the billing is the body and
> the science tools are replaceable organs. That's a real business, it partly
> exists already, and its hard parts are payments-marketplace and GPU-economics
> hard — not science hard. Not mine to build, but the core primitive (the
> versioned method manifest) is exactly what sciable already prototyped.

---

## 1. The BYOK impossibility

Every GPU platform (Runpod included) **fuses two roles into one account**:

- **Execution owner** — owns the machine image, warm process, filesystem, the
  isolation boundary.
- **Billing identity** — whose balance the GPU-seconds are drawn from.

The thing I kept wanting ("my warm host machine, but each job billed to the
*user's* key") **splits** those two. That split does not exist on Runpod — the
account that owns the endpoint is, by definition, the account that pays. There
is no seam to slide a second identity into.

The abstract name for the missing primitive is **requester-pays compute** /
**delegated billing** — the compute analog of a requester-pays storage bucket
(which *does* exist). Nobody offers it for GPU.

### What a hypothetical "Runpod2" would need

A **scoped, delegable billing token**: the user authorizes *"charge my account
for jobs running on host X, up to $Y, for endpoint-signature Z,"* and the
**platform** (not me) verifies and meters it. That's OAuth-for-billing. Its
absence is *the* reason the warm-shared-host model can't be built today — it's a
provider-side trust mechanism, not something bolt-on-able from outside.

---

## 2. What actually works today — and why each option is rejected

Because the seam doesn't exist, you must pick which role you keep. You can't
own the warm machine **and** bill the user.

| Option | Who pays Runpod | I touch money? | I hold a key? | "My warm machine"? | Verdict |
|---|---|---|---|---|---|
| **Mint keys from my account, invoice users** | Me, then bill back | **Yes** (billing infra) | Yours | Yes | ❌ reseller in disguise |
| **User owns endpoint + key** | User, directly | No | No | No (their endpoint) | ✅ only billing-free path |
| **Single operator = me** | Me | No (my own cost) | My own | Yes | ✅ chosen for sciable |

**Mint-and-invoice is a trap.** Keys minted from my account spend *my* balance,
so that isn't BYOK — it's "I pay Runpod for everything, then invoice users to
get my money back." It drags in metering, invoicing, collections, chargebacks,
tax, float (out-of-pocket until they pay), and liability for a user running up a
bill I'm on the hook for. A payments business bolted onto a science tool. `AGENTS.md`
forbids this in as many words ("never resells GPU time", "no checkout/credits").

**User-owns-endpoint is clean but disintermediating.** The user deploys my
pinned worker image as an endpoint in *their* account, runs it on *their* key,
pays Runpod *directly*. I'm a pure control plane (manifest, form, validation,
run metadata) — zero money, zero key custody, zero liability. **But** once the
user has the image + their own key + their own endpoint, I own nothing in the
loop. The server has no leverage, so no reason to exist. "I might as well put
the setups on GitHub" is the logically correct end state.

---

## 3. The real lesson: no moat is a signal, not a bug

I systematically refused every source of lock-in — won't own compute, won't
touch billing, won't custody keys, won't hide the image. Those are *principled*
refusals. But a product's moat is exactly one of those things. Remove all of
them and there is no technical moat left, and no server-side cleverness restores
it without re-introducing the custody/billing I'm avoiding.

For a **single** open method, the honest answer is: there is no defensible
product, and trying to manufacture one would betray the privacy/no-custody
principles that make the project worth doing. → **Scope sciable to single-
operator.** This isn't a half-built marketplace apologizing for missing parts;
it's a clean single-operator reproducible runner.

---

## 4. The parked idea — "a billing company with a science appendix"

The inversion: in sciable, billing was the tumor and science the body. **Flip
it** — billing/marketplace is the *body*, each science tool is a *replaceable
organ* — and every headache becomes someone else's job. The provider never
touches billing; the platform never touches science.

**Shape:** users log in, hold a credit balance, browse web tools hosted by the
platform but authored/controlled by third parties, run them, get results, and
their balance is billed per run. Tool authors focus on science. **Bonus (and
actually the moat, see below): authors get a revenue share.**

### It partly exists already — calibrate before pitching

- **Tamarind Bio, Neurosnap, BioLM** — almost exactly this for bio-AI: hosted
  web UIs to run AlphaFold / protein-design / structure tools, pay-per-run, no
  infra for the user. Closest direct comps.
- **Hugging Face Spaces + Inference** — two-sided at scale (authors publish
  Gradio apps, others run them), but author monetization is thin.
- **Replicate** — "push a model, users run it per-second, we bill and host" —
  the compute-marketplace mechanics minus science curation.
- **Code Ocean / DNAnexus / Terra** — reproducibility-and-billed-compute
  incumbents (Terra runs genomics tools billed to your cloud account).

Market is real (validated by existence); the *easy* version is taken.

### Where the moat and the pain actually are — not where you'd guess

- Billing tech is **not** the moat. Stripe + a metering table + credit balances
  is a few weeks. Anyone can build it.
- The moat is **two-sided liquidity** (chicken-and-egg: no tools → no users → no
  tools) + **trust/curation** (is this tool correct, reproducible, safe with my
  proprietary sequences?).
- The brutal under-appreciated part is **GPU margin.** You front the compute and
  bill on top; cold starts, idle pods, and spot-price swings can make gross
  margin *negative* on exactly the heavy scientific jobs that are the point.
  This strains Replicate and kills thinner players. A "billing company" whose
  COGS is volatile GPU time is a hard business, not a passive toll booth.
- **Weight/code redistribution terms** (already flagged in `CLAUDE.md`) — a
  marketplace hosting other people's models inherits every licensing question,
  at scale.

### The genuinely differentiating wedge: author rev-share

Not optional — it's the moat. Paying the scientist who published the tool a cut
per run aligns **supply** and solves cold-start from the hard side. "Turn your
paper into a hosted tool that *earns*" is a real reason for a lab to show up.
That's the Steam insight: the storefront wins by making creators money, not by
having the best checkout. Nobody has nailed this in science.

### The tie-back

The primitive this business needs is the thing sciable already prototyped: the
**versioned method manifest** — one contract driving validation + form + API,
with pinned source/checkpoint digests, isolation, and provenance — is exactly
the unit a marketplace would list, meter, and bill. I didn't fail to find the
business; I built its core component and then correctly decided I don't want to
run the payments-and-GPU-margin company wrapped around it. The manifest is the
asset; the billing company is for someone with a different risk appetite (and
capital for GPU float).

---

## 4a. The real moat — data gravity as a cross-owner network effect

The strongest moat for the marketplace isn't rev-share (incentives) — it's
**data gravity** (Dave McCrory, ~2010): data accretes mass; services fall toward
it because moving petabyte-scale scientific data costs far more than moving the
computation.

**The non-obvious twist: it becomes a network effect across *independently-owned*
services.** Normally microservices compose irrespective of location (network
calls). But when datasets are huge, co-locating services on the same filesystem
turns the handoff from a serialize-transfer-deserialize over the internet into a
**local file reference / zero-copy read** — even though the code-level interface
looks nearly identical. So a platform co-hosting many thematically-related
microservices gains a benefit that accrues *even though each service is owned by
someone else*.

**Why it's a network effect, not just an optimization:** each additional
co-located, related service raises the value of every other one, because the set
of zero-egress compositions grows combinatorially. Tool B sharing a filesystem
with A can consume A's 300 GB output by reference instead of transferring it; add
C and there are more pipelines. Value of *joining* scales with how many adjacent
tools already live there = demand-side increasing returns. Plus supply-side
lock-in: once your reference data + intermediates live on the platform's scratch,
leaving means re-uploading terabytes. **Data gravity _is_ switching cost.**

**Precedent (de-risks the thesis):** "bring the algorithm to the data" in
genomics — UK Biobank Research Analysis Platform (on DNAnexus), Terra/AnVIL,
Seqera/Nextflow on shared Lustre/GPFS scratch. Petabyte cohorts can't move, so
compute co-locates. Clouds encode the same law in pricing (free same-region
transfer, brutal egress). Incumbents co-locate *your* tools with *your* data;
the novel move is co-locating *strangers'* tools with *each other*.

**The crux — isolation vs. sharing (the make-or-break engineering):** this
directly contradicts sciable's own constraint ("two sequential jobs must not see
each other's files"). Zero-copy handoff *deliberately* lets B read A's output on
disk. So you cannot have a flat shared filesystem — you need a **capability
model**: the platform grants B a scoped, read-only, time-limited handle to
*exactly one artifact*, *only inside the user's authorized pipeline run* — never
a global mount. Get it wrong and co-tenant B reads other users' outputs, or a
malicious A plants something B executes. The mechanism is: sandboxed fresh-
subprocess execution (already built) **plus** a mediated per-run artifact grant.
That mediation layer is the actual defensible product — not the filesystem.

**Tie-back (3rd time the manifest is the keystone):** for B to consume A's output
by reference safely, their I/O contracts must match — and that's the manifest's
typed input/output schema. So the manifest is both the unit you list/meter/bill
(§4) **and** the handshake that makes pass-by-reference safe across owners.
Co-location without typed contracts is chaos; with them, it's a composable
pipeline.

**Strategic consequence:** the platform isn't one flat marketplace — it's a
**set of vertical data-gravity wells** (RNA-with-RNA, imaging-with-imaging; only
thematically-clustered big-data services benefit). Winning move: pick one
data-heavy vertical, win its gravity well, then expand — which also solves the
cold-start liquidity problem from the demand side.

## 4b. Trust model — single-uid fleet, no customer terminals, AI-mediated

To make §4a's data gravity cheap: **all machines (virtual or physical) are owned
by the platform; the OS-level user is the platform, not the customer.** Sharing
files between the platform's own machines is then trivial (shared parallel FS,
same uid, no cross-account ACLs). Customers **never get terminal/shell access** —
they chat with the platform's AI, which makes changes on their behalf. Customers
are *application-level* principals, not OS principals.

This is the correct **data-plane** decision (it's how real SaaS avoids the
multi-tenant-shell nightmare). But be rigorous: it **relocates** the isolation
problem, it does not remove it.

**Soft vs. hard multi-tenancy.** Before: many OS accounts → strong kernel-
enforced isolation for free, but painful co-location. Now: everything runs as the
platform uid → co-location trivial, but the OS provides **zero** isolation
between customers (same principal). All separation is now *application code's*
job, and any bug's blast radius is the whole fleet. A deliberate shift of burden
from kernel to you, not a free win.

**The real threat is the providers' code, not the customer.** The marketplace
premise is running *strangers'* microservices — and they run *as the platform, on
the shared mount*. A malicious/compromised service can read every customer's data
and every provider's artifacts. "No customer terminal" is orthogonal to this.
Two boundaries you must enforce yourself:

- **Boundary 1 — per-microservice sandbox (the hard one = the actual product):**
  each third-party service runs in a strong sandbox (gVisor / Firecracker microVM
  / seccomp+container) with a **read-only mount of only the artifacts its current
  pipeline step is authorized to touch**, plus a scratch dir. Single-uid makes
  this *more* essential — nothing below it will save you.
- **Boundary 2 — the AI is a confused deputy:** it acts with fleet-as-platform
  authority on behalf of a customer, so prompt injection (via a customer file, a
  shared artifact, a provider's tool description) could make it read/mutate
  another customer's data. **Capabilities must be enforced below the AI, in the
  platform, scoped to the acting customer** — the AI never operates in god-mode
  even though the uid is god. Never make the model's good behavior the security
  boundary.

Footnote: "no terminal" ≠ "no code execution" — the microservices *are* arbitrary
code execution; the security model must assume every invoked service is hostile.

**Tie-back (4th time the manifest is the keystone):** the manifest declares what
each microservice may read/write (I/O schema + resource/permission requirements)
— exactly what the sandbox mounts and the capability grant enforce. List/meter/
bill it (§4), gate the zero-copy handoff (§4a), scope the sandbox + the AI's
authority (§4b).

---

## 5. Decision of record

- **sciable:** single-operator, BYOK scoped to "for myself." No multi-user
  keys, no billing, no marketplace. The manifest/isolation/provenance work
  stands on its own as a reproducible-runner demonstration.
- **Marketplace idea:** parked. Real but not mine — payments-marketplace hard +
  GPU-economics hard, not science hard.
- **Runpod2 / delegable billing token:** filed as "what would make warm-shared-
  host BYOK possible," not buildable from outside today.
