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

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth import require_operator
from app.backends.runpod import BackendError
from app.manifest import FieldSpec, Manifest
from app.models import Run, RunState
from app.runs import (
    UNCERTAIN_SUBMISSION,
    RunConflictError,
    RunNotCancelableError,
    RunService,
)
from app.templating import templates
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


def dot_bracket_pairs(structure: str) -> list[tuple[int, int]]:
    """Resolve a dot-bracket string into 0-indexed base pairs (i, j), i < j.

    Standard round brackets only; unbalanced brackets are ignored rather than
    raising, so a malformed model output still renders what it can."""
    stack: list[int] = []
    pairs: list[tuple[int, int]] = []
    for i, ch in enumerate(structure):
        if ch == "(":
            stack.append(i)
        elif ch == ")" and stack:
            pairs.append((stack.pop(), i))
    return sorted(pairs)


def render_structure_svg(sequence: str, structure: str) -> str:
    """A server-rendered arc diagram: each base pair is an arc over the sequence.

    One accent hue, theme-aware via CSS classes (see base.html); nested pairs rise
    higher so hairpins and multiloops read at a glance. Returns an empty string if
    there is nothing to draw."""
    n = len(structure)
    if n == 0:
        return ""
    cell, max_arc, pad = 22, 70, 10
    baseline = max_arc + 6
    letter_y = baseline + 16
    tick_y = baseline + 30
    width = n * cell + 2 * pad
    height = tick_y + 6

    def x(i: int) -> float:
        return pad + i * cell + cell / 2

    paired = {i for pair in dot_bracket_pairs(structure) for i in pair}

    arcs: list[str] = []
    for i, j in dot_bracket_pairs(structure):
        h = min(max_arc, 10 + (j - i) * 3)
        xm = (x(i) + x(j)) / 2
        arcs.append(
            f'<path class="rna-arc" d="M {x(i):.1f} {baseline} '
            f'Q {xm:.1f} {baseline - 2 * h:.1f} {x(j):.1f} {baseline}"/>'
        )

    letters: list[str] = []
    for i in range(n):
        base = sequence[i] if i < len(sequence) else "·"
        cls = "rna-base paired" if i in paired else "rna-base"
        letters.append(f'<text class="{cls}" x="{x(i):.1f}" y="{letter_y}">{base}</text>')
        if (i + 1) % 10 == 0:
            letters.append(f'<text class="rna-tick" x="{x(i):.1f}" y="{tick_y}">{i + 1}</text>')

    return (
        f'<svg class="rna-structure" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        f'aria-label="Secondary-structure arc diagram, {len(paired) // 2} base pairs">'
        f'{"".join(arcs)}{"".join(letters)}</svg>'
    )


def plddt_from_pdb(pdb_text: str) -> list[float]:
    """Per-residue pLDDT from a PDB's B-factor column, in file order.

    RhoFold writes the per-residue pLDDT into the B-factor (cols 61-66) of every
    atom in a residue, so we read the first atom seen per (chain, resSeq). Lines
    too short or with an unparseable B-factor are skipped rather than raising, so
    a malformed structure still yields what it can."""
    seen: set[tuple[str, str]] = set()
    plddt: list[float] = []
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM", "HETATM")) or len(line) < 66:
            continue
        key = (line[21:22], line[22:26])
        if key in seen:
            continue
        seen.add(key)
        try:
            plddt.append(round(float(line[60:66]), 2))
        except ValueError:
            continue
    return plddt


def structure_3d_metrics(pdb_text: str) -> dict:
    """Summary of a predicted 3D structure (residue/atom counts, pLDDT range)."""
    plddt = plddt_from_pdb(pdb_text)
    atoms = sum(1 for ln in pdb_text.splitlines() if ln.startswith(("ATOM", "HETATM")))
    return {
        "length": len(plddt),
        "atoms": atoms,
        "plddt_mean": round(sum(plddt) / len(plddt), 2) if plddt else 0.0,
        "plddt_min": min(plddt) if plddt else 0.0,
        "plddt_max": max(plddt) if plddt else 0.0,
    }


#: Above this length we don't render an inline heatmap (too many SVG cells).
_HEATMAP_MAX_L = 120


def contact_metrics(matrix: list[list[float]]) -> dict:
    """Summary of an L×L contact/attention matrix."""
    n = len(matrix)
    flat = [v for row in matrix for v in row]
    strong = sum(1 for v in flat if v >= 0.8)
    return {
        "length": n,
        "max": round(max(flat), 4) if flat else 0.0,
        "mean": round(sum(flat) / len(flat), 4) if flat else 0.0,
        "strong_cells": strong,  # cells >= 0.8 (candidate contacts, incl. off-diagonal)
    }


def render_contact_heatmap(
    sequence: str, matrix: list[list[float]], label: str = "contact map"
) -> str:
    """Server-rendered L×L heatmap (sequential single-hue via cell opacity).

    Off-diagonal intensity reveals long-range / crossing (pseudoknot) signal that a
    nested dot-bracket cannot represent. ``label`` names the matrix for the SVG's
    accessibility label (method-specific — e.g. "attention map" vs "base-pairing
    probability matrix"). Returns '' if too large to render inline."""
    n = len(matrix)
    if n == 0 or n > _HEATMAP_MAX_L:
        return ""
    cell = max(4, min(18, 360 // n))
    pad = 14
    size = n * cell
    labels = n <= 40
    cells: list[str] = []
    for i, row in enumerate(matrix):
        for j, v in enumerate(row):
            v = 0.0 if v < 0 else 1.0 if v > 1 else v
            cells.append(
                f'<rect class="hm-cell" x="{pad + j * cell}" y="{pad + i * cell}" '
                f'width="{cell}" height="{cell}" fill-opacity="{v:.3f}"/>'
            )
    ticks: list[str] = []
    if labels:
        for i in range(n):
            base = sequence[i] if i < len(sequence) else "·"
            ticks.append(
                f'<text class="hm-base" x="{pad + i * cell + cell / 2:.1f}" '
                f'y="{pad - 3}">{base}</text>'
            )
    w = size + 2 * pad
    return (
        f'<svg class="contact-heatmap" viewBox="0 0 {w} {w}" width="{w}" height="{w}" '
        f'role="img" aria-label="{label}, {n}x{n}">'
        f'{"".join(cells)}{"".join(ticks)}</svg>'
    )


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
        kind = manifest.outputs.kind
        if kind == "contact_map":
            body["sequence"] = outputs.get("sequence")
            body["matrix"] = outputs.get("matrix")
            body["matrix_type"] = outputs.get("matrix_type")
            if outputs.get("matrix"):
                body["metrics"] = contact_metrics(outputs["matrix"])
        elif kind == "structure":
            body["structure"] = outputs.get("structure")
            body["sequence"] = outputs.get("sequence")
            if outputs.get("structure"):
                body["metrics"] = structure_metrics(outputs["structure"])
        elif kind == "structure_3d":
            body["pdb"] = outputs.get("pdb")
            body["sequence"] = outputs.get("sequence")
            body["plddt"] = outputs.get("plddt")
            if outputs.get("pdb"):
                body["metrics"] = structure_3d_metrics(outputs["pdb"])
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


def _example_text(manifest: Manifest) -> str:
    path = manifest.example_path()
    return path.read_text() if path else ""


def resolve_method(request: Request, submitted: str | None) -> Manifest:
    """Resolve the submitted method name. There is NO default: the landing page
    is the chooser, and an API submission must name its method explicitly."""
    manifests: dict[str, Manifest] = request.app.state.manifests
    if not submitted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "missing_method",
                "message": "Submission must name a 'method'. "
                f"Available: {', '.join(sorted(manifests))}.",
            },
        )
    manifest = manifests.get(submitted)
    if manifest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "unknown_method", "message": f"No method '{submitted}'."},
        )
    return manifest


def manifest_for_run(request: Request, run: Run) -> Manifest:
    method_id = run.resolved_release_id.split("@", 1)[0]
    return resolve_method(request, method_id)


async def _parse_submission(request: Request) -> tuple[Manifest, dict[str, Any], str | None]:
    """Resolve the target method, then build a raw {field_name: value} dict from a
    JSON body or multipart form, driven by that method's declared fields."""
    idempotency_key = request.headers.get("Idempotency-Key")
    ctype = request.headers.get("content-type", "")
    raw: dict[str, Any] = {}

    if ctype.startswith("application/json"):
        data = await request.json()
        manifest = resolve_method(request, data.get("method"))
        for f in manifest.inputs.fields:
            raw[f.name] = data.get(f.name)
        use_example = bool(data.get("use_example"))
        idempotency_key = data.get("idempotency_key") or idempotency_key
    else:
        form = await request.form()
        manifest = resolve_method(request, form.get("method"))
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
            raw[primary.name] = _example_text(manifest)

    return manifest, raw, idempotency_key


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _submission_error(
    request: Request,
    manifest: Manifest,
    code: str,
    message: str,
    status_code: int,
    headline: str,
) -> Response:
    """A failed submission as HTML for browsers, JSON for API clients.

    A web form submit must render a page, never a raw JSON body; API clients get
    the machine-readable ``{"detail": {"code", "message"}}`` shape and status."""
    if _wants_html(request):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"manifest": manifest, "headline": headline, "message": message},
            status_code=status_code,
        )
    return JSONResponse(
        status_code=status_code, content={"detail": {"code": code, "message": message}}
    )


@router.post("/runs", dependencies=[Depends(require_operator)])
async def create_run(request: Request):
    service: RunService = request.app.state.run_service
    manifest, raw, idempotency_key = await _parse_submission(request)
    deployment_id: str = request.app.state.deployments[manifest.method_id]

    try:
        run = service.create_run(
            deployment_id=deployment_id,
            raw=raw,
            idempotency_key=idempotency_key,
        )
    except ValidationError as exc:
        return _submission_error(
            request, manifest, exc.code, exc.message,
            status.HTTP_422_UNPROCESSABLE_ENTITY, "That input couldn't be accepted",
        )
    except RunConflictError as exc:
        return _submission_error(
            request, manifest, "in_flight_limit", str(exc),
            status.HTTP_409_CONFLICT, "A run is already in progress",
        )
    except BackendError as exc:
        # The provider rejected the submission (bad key, endpoint disabled or
        # scaled to zero max workers, ...). Report it instead of a bare 500.
        return _submission_error(
            request, manifest, "backend_rejected", str(exc),
            status.HTTP_502_BAD_GATEWAY, "The compute backend rejected the job",
        )

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
    run = service.poll_run(run_id)  # reconcile with backend on read
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    body = serialize_run(run, manifest_for_run(request, run))
    if run.state == RunState.FAILED:
        raw = service.read_raw(run_id)
        if raw and raw.get("detail"):
            body["error_detail"] = raw["detail"]
    return body


@router.post("/runs/{run_id}/cancel", dependencies=[Depends(require_operator)])
def cancel_run(run_id: str, request: Request):
    """Cancel a non-terminal run (also asks the provider to cancel its job, if any).

    Browsers (Accept: text/html) are redirected back to the run page; API
    clients receive the serialized run plus ``provider_cancel``:
    ``"requested"`` / ``"failed"`` / ``"not_applicable"`` (no provider job)."""
    service: RunService = request.app.state.run_service
    try:
        result = service.cancel_run(run_id)
    except RunNotCancelableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "not_cancelable", "message": str(exc)},
        ) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    run, provider_canceled = result

    if _wants_html(request):
        return RedirectResponse(url=f"/runs/{run.id}", status_code=status.HTTP_303_SEE_OTHER)
    body = serialize_run(run, manifest_for_run(request, run))
    body["provider_cancel"] = (
        "not_applicable"
        if provider_canceled is None
        else ("requested" if provider_canceled else "failed")
    )
    return body


@router.get("/runs/{run_id}/raw", dependencies=[Depends(require_operator)])
def get_run_raw(run_id: str, request: Request, download: bool = False):
    """The worker's raw program output (or failure detail) for a finished run.

    ``?download=1`` serves it as a JSON file attachment; otherwise it is returned
    inline for in-browser / API viewing."""
    service: RunService = request.app.state.run_service
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    raw = service.read_raw(run_id)  # works for failed runs too (no outputs_ref)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No raw output for this run."
        )
    if download:
        return Response(
            content=json.dumps(raw, indent=2, sort_keys=True),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="run-{run_id[:8]}-raw.json"'
            },
        )
    return raw


@router.get("/runs/{run_id}/structure.pdb", dependencies=[Depends(require_operator)])
def get_run_pdb(run_id: str, request: Request):
    """The predicted 3D structure as a downloadable PDB file (structure_3d runs)."""
    service: RunService = request.app.state.run_service
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    pdb = read_outputs(run.outputs_ref).get("pdb")
    if not pdb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No PDB structure for this run."
        )
    return Response(
        content=pdb,
        media_type="chemical/x-pdb",
        headers={"Content-Disposition": f'attachment; filename="run-{run_id[:8]}.pdb"'},
    )
