# AGENTS.md

## Mission

Build **Lovable for Scientists**. Turn a working research example into a versioned scientific method with a web form and an asynchronous API. Prove the path with **gRNAde running in the operator’s own Runpod account (BYOK)**. Keep the first release small enough for one person to run as a side project, with a $100/month development budget. Runpod bills the account that owns the execution resources. The app has no checkout, credits, or compute resale.

## First demo: design RNA sequences for a target structure

- Use [upstream gRNAde](https://github.com/chaitjo/geometric-rna-design). Start from its documented inference example and pin a commit, one checkpoint, and one small RNA-only example structure. Verify code, checkpoint, and example redistribution terms separately before publishing artifacts. Record the upstream citation and sources.
- Support one PDB input and one RNA chain. Provide a built-in example. Accept a bounded design count and a seed. Start with five designs by default and a cap of ten. Establish the supported length and file-size limits from actual smoke runs and document them. Reject unsupported or ambiguous structures with a clear error.
- Return candidate sequences as FASTA and a structured run record. Show the sequences in the browser. Expose model scores only if the adapter can explain their meaning and direction. Describe designs as candidates for the supplied backbone. A successful run does not validate their folding or biological function.
- Use the upstream inference path as the reference. Check output count, alphabet, sequence length and residue mapping against the processed target. Compare the adapter with upstream on the same fixture, version and seed under equivalent conditions. Document any nondeterminism and use justified tolerances where needed.

## Architecture

Keep three records distinct. Store them in a simple single-instance app initially. FastAPI and SQLite are reasonable defaults for the control plane. Use a small server-rendered UI unless existing code calls for something else.

| Record | Responsibility |
| --- | --- |
| Method release | Immutable ID, input and output schemas, source and checkpoint digests, worker image digest, execution requirements, example and citation |
| Deployment | Maps a method release to an operator-owned endpoint, execution limits and credential reference |
| Run | Links a deployment and resolved release to job state, input fingerprint, seed, provider job ID, timing and outputs |

Use one versioned method manifest to drive validation, the web form and the API. Keep the Runpod adapter behind a narrow submit/status interface, with cancellation where supported. The method contract must also fit conventional code and composite methods. Implement only one method and backend now.

Start with a bundled example and an endpoint pre-created by the operator using the pinned worker image. Provide a setup guide for the second operator to create their own endpoint and connect it. Automated provisioning and general notebook import are later milestones. For this demo, capture the translation from working example to manifest, environment recipe and request handler as a repeatable process.

## Execution and data boundaries

- Build dependencies into an immutable worker image. Bake in approved weights or fetch a pinned artifact and verify its checksum. Share only read-only code and assets between runs. Keep private inputs and outputs out of all shared caches.
- Use a fresh subprocess with its own writable workspace, home and temp paths for each gRNAde job. Treat uploaded PDBs as data, pass arguments as an array, and clean up on success or failure. One process per job separates accidental state for this trusted adapter. Arbitrary submitted code would require a separate isolation design.
- For v1, use authenticated single-operator deployments. Keep the operator's Runpod key server-side in a secret store or environment variable. Never place it in browser code, logs, job payloads or the worker image. Use the minimum available key scope. Document key rotation and endpoint ownership verification. To prove BYOK, use a second app configuration and an independently owned endpoint. Avoid collecting third-party credentials in a public shared instance.
- Use the [Runpod Serverless docs](https://docs.runpod.io/serverless) to verify the request schema, authorization, job states, limits and teardown before implementing live calls. Prefer zero minimum workers and one maximum worker for development where supported. Cap in-flight jobs and execution time. Measure cold and warm runs separately. Document how to remove the endpoint and any separately billed storage. Treat the development budget as an operating limit and check actual usage in the provider account.
- Keep demo payloads and results small. Enforce upload and result size limits. Restrict result access to the submitting operator and document retention. Record timing and error category without logging sequence or structure contents.

## API and web flow

1. The user opens the method page, loads the example or uploads a PDB, and sets design count and seed.
2. `POST /api/runs` validates the inputs, creates a local run and returns its ID. The browser and API use the same submission path.
3. `GET /api/runs/{id}` reports queued, running, succeeded, failed or canceled when supported. Return a useful error for invalid input, worker failure or timeout. Serve FASTA and run metadata only to the authorized operator.
4. Use an idempotency key for repeated submissions. If provider submission times out after the request may have been accepted, surface the uncertain state. Avoid automatic resubmission that could incur a second GPU charge.

## Build order and acceptance

1. **Baseline.** Run the pinned upstream example in a clean environment. Save its input, invocation, versions and output checks.
2. **Worker.** Build the image and run the same fixture through its request handler. Confirm two sequential jobs with different inputs cannot see each other’s files.
3. **App.** Add the manifest, form, run API and status/results view. Exercise the full lifecycle with a fake backend so local checks require no GPU or credentials.
4. **Live demo.** Run the example on an operator-owned Runpod endpoint. Repeat with a separately authorized account and confirm each run appears in the intended account. Record cold-start time, execution time and observed cost where available. Verify cleanup after the smoke run.
5. **Handoff.** Write a README with local setup, image build, endpoint setup, example API calls, result interpretation and teardown. Record exact commands and unresolved issues. Mark live checks as pending when credentials or cloud authorization are unavailable.

Keep the first iteration focused on one verified scientific job. Defer public multi-tenancy, a catalog, MCP, automatic notebook conversion, persistent model processes and payment infrastructure until the demo works. Add abstractions when a second method shows what actually needs to be shared.
