"""JSON API routes.

One submission endpoint (``POST /api/runs``) backs both API clients and the
browser form — same validation and the same ``RunService.create_run`` path.
The endpoint content-negotiates: browsers (Accept: text/html) are redirected to
the human results page; API clients receive JSON.

``GET /api/runs/{id}`` reconciles state with the backend on read, so a polling
client observes queued -> running -> succeeded.

Submission parsing and output shaping are driven by the active manifest's
declared input fields and output kind, so the endpoints are method-agnostic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth import require_operator
from app.manifest import FieldSpec, Manifest
from app.models import Run, RunState
from app.runs import UNCERTAIN_SUBMISSION, RunConflictError, RunService
from app.validation import ValidationError

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# -- output reading / metrics -------------------------------------------------


def read_outputs(outputs_ref: str | None) -> dict:
    if not outputs_ref:
        return {}
    path = Path(outputs_ref)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def read_raw_output(run: Run) -> dict | None:
    """Load the worker's raw output saved next to the outputs, if any."""
    if not run.outputs_ref:
        return None
    path = Path(run.outputs_ref).parent / f"{run.id}.raw.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def sequence_metrics(sequences: list[str]) -> list[dict]:
    """Per-design metrics derived from the sequences (length, GC content)."""
    metrics = []
    for i, seq in enumerate(sequences, start=1):
        gc = seq.count("G") + seq.count("C")
        metrics.append(
            {
                "id": f"design_{i}",
                "length": len(seq),
                "gc_percent": round(100 * gc / len(seq), 1) if seq else 0.0,
            }
        )
    return metrics


def structure_metrics(structure: str) -> dict:
    """Summary of a dot-bracket structure (length, paired/unpaired counts)."""
    pairs = structure.count("(")
    unpaired = structure.count(".")
    return {
        "length": len(structure),
        "base_pairs": pairs,
        "unpaired": unpaired,
        "paired_percent": round(100 * (2 * pairs) / len(structure), 1) if structure else 0.0,
    }


def _public_inputs(run: Run, manifest: Manifest) -> dict:
    """Input values safe to echo back (large structure text replaced by size)."""
    pub: dict[str, Any] = {}
    for f in manifest.inputs.fields:
        v = run.inputs.get(f.name)
        if f.type == "pdb_structure":
            if v:
                pub[f"{f.name}_bytes"] = len(str(v).encode("utf-8"))
        else:
            pub[f.name] = v
    if "sequence_length" in run.inputs:
        pub["sequence_length"] = run.inputs["sequence_length"]
    return pub


def serialize_run(run: Run, manifest: Manifest) -> dict:
    uncertain = run.state == RunState.QUEUED and run.error_category == UNCERTAIN_SUBMISSION
    body: dict = {
        "id": run.id,
        "method": manifest.method_id,
        "state": run.state.value,
        "seed": run.seed,
        "inputs": _public_inputs(run, manifest),
        "input_fingerprint": run.input_fingerprint,
        "submitted_at": run.submitted_at,
        "finished_at": run.finished_at,
        "error_category": run.error_category,
        "submission_uncertain": uncertain,
    }
    if run.state == RunState.SUCCEEDED:
        outputs = read_outputs(run.outputs_ref)
        if manifest.outputs.kind == "structure":
            body["structure"] = outputs.get("structure")
            body["sequence"] = outputs.get("sequence")
            if outputs.get("structure"):
                body["metrics"] = structure_metrics(outputs["structure"])
        else:
            sequences = outputs.get("sequences", [])
            body["sequences"] = sequences
            body["metrics"] = sequence_metrics(sequences)
        raw = read_raw_output(run)
        if raw is not None:
            body["raw"] = raw
    return body


# -- submission parsing -------------------------------------------------------


def _primary_input_field(manifest: Manifest) -> FieldSpec | None:
    """The field an example/upload fills — the first structure/sequence input."""
    return next(
        (f for f in manifest.inputs.fields if f.type in ("pdb_structure", "rna_sequence")),
        None,
    )


def _example_text(request: Request) -> str:
    path = request.app.state.manifest.example_path()
    return path.read_text() if path else ""


async def _parse_submission(request: Request) -> tuple[dict[str, Any], str | None]:
    """Build a raw {field_name: value} dict from a JSON body or multipart form,
    driven by the active manifest's declared fields."""
    manifest: Manifest = request.app.state.manifest
    idempotency_key = request.headers.get("Idempotency-Key")
    ctype = request.headers.get("content-type", "")
    raw: dict[str, Any] = {}

    if ctype.startswith("application/json"):
        data = await request.json()
        for f in manifest.inputs.fields:
            raw[f.name] = data.get(f.name)
        use_example = bool(data.get("use_example"))
        idempotency_key = data.get("idempotency_key") or idempotency_key
    else:
        form = await request.form()
        for f in manifest.inputs.fields:
            if f.type == "pdb_structure":
                value: Any = None
                upload = form.get(f"{f.name}_file")
                if upload is not None and hasattr(upload, "read"):
                    value = (await upload.read()).decode("utf-8", errors="replace")
                elif form.get(f.name):
                    value = str(form.get(f.name))
                raw[f.name] = value
            else:
                v = form.get(f.name)
                raw[f.name] = None if v in (None, "") else str(v)
        use_example = bool(form.get("use_example"))
        idempotency_key = form.get("idempotency_key") or idempotency_key

    if use_example:
        primary = _primary_input_field(manifest)
        if primary is not None and not raw.get(primary.name):
            raw[primary.name] = _example_text(request)

    return raw, idempotency_key


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


@router.post("/runs", dependencies=[Depends(require_operator)])
async def create_run(request: Request):
    service: RunService = request.app.state.run_service
    manifest: Manifest = request.app.state.manifest
    deployment_id: str = request.app.state.deployment_id
    raw, idempotency_key = await _parse_submission(request)

    try:
        run = service.create_run(
            deployment_id=deployment_id,
            raw=raw,
            idempotency_key=idempotency_key,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except RunConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "in_flight_limit", "message": str(exc)},
        ) from exc

    if _wants_html(request):
        return RedirectResponse(url=f"/runs/{run.id}", status_code=status.HTTP_303_SEE_OTHER)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=serialize_run(run, manifest),
        headers={"Location": f"/api/runs/{run.id}"},
    )


@router.get("/runs/{run_id}", dependencies=[Depends(require_operator)])
def get_run(run_id: str, request: Request):
    service: RunService = request.app.state.run_service
    manifest: Manifest = request.app.state.manifest
    run = service.poll_run(run_id)  # reconcile with backend on read
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    return serialize_run(run, manifest)


@router.get("/runs/{run_id}/raw", dependencies=[Depends(require_operator)])
def get_run_raw(run_id: str, request: Request):
    """The worker's raw program output for a finished run."""
    service: RunService = request.app.state.run_service
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    raw = read_raw_output(run)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No raw output for this run."
        )
    return raw
