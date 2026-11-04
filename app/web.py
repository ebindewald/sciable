"""Server-rendered web routes.

``/`` is a landing page listing every method in the registry; ``/methods/{id}``
renders one method's form. Both the form and the results view are driven by the
method's manifest (declared fields + output kind). Submissions post to
``POST /api/runs`` (the same endpoint API clients use) carrying a ``method`` field;
content negotiation there redirects the browser to the results page below.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from app.api import (
    _primary_input_field,
    manifest_for_run,
    read_outputs,
    read_raw_output,
    render_structure_svg,
    resolve_method,
    sequence_metrics,
    structure_metrics,
)
from app.config import REPO_ROOT
from app.models import TERMINAL_STATES, RunState
from app.runs import UNCERTAIN_SUBMISSION, RunService
from app.validation import _extract_sequence, parse_pdb_chains

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(REPO_ROOT / "app" / "templates"))


def _example_info(manifest) -> dict | None:
    """Summarize the bundled example so the form can describe it inline."""
    path = manifest.example_path()
    if path is None or not path.exists():
        return None
    text = path.read_text()
    info = {"filename": path.name, "bytes": len(text.encode("utf-8"))}
    primary = _primary_input_field(manifest)
    if primary is not None and primary.type == "pdb_structure":
        chains = parse_pdb_chains(text)
        chain_id = next(iter(chains)) if chains else "?"
        info["chain"] = chain_id
        info["length"] = len(chains[chain_id].residues) if chains else 0
    elif primary is not None and primary.type == "rna_sequence":
        seq = _extract_sequence(text).upper()
        info["length"] = len(seq)
        info["sequence"] = seq
    return info


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Landing page: the catalog of available methods."""
    manifests = request.app.state.manifests
    return templates.TemplateResponse(
        request,
        "index.html",
        {"methods": list(manifests.values())},
    )


@router.get("/methods/{method_id}", response_class=HTMLResponse)
def method_page(method_id: str, request: Request):
    manifest = resolve_method(request, method_id)
    primary = _primary_input_field(manifest)
    secondary = [f for f in manifest.inputs.fields if primary is None or f.name != primary.name]
    return templates.TemplateResponse(
        request,
        "method.html",
        {
            "manifest": manifest,
            "example": _example_info(manifest),
            "primary": primary,
            "secondary_fields": secondary,
        },
    )


@router.get("/methods/{method_id}/example", response_class=PlainTextResponse)
def method_example(method_id: str, request: Request):
    """Serve a method's bundled example input so users can inspect what they'll submit."""
    manifest = resolve_method(request, method_id)
    path = manifest.example_path()
    if path is None or not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No bundled example.")
    primary = _primary_input_field(manifest)
    media = "chemical/x-pdb" if primary and primary.type == "pdb_structure" else "text/plain"
    return PlainTextResponse(path.read_text(), media_type=media)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(run_id: str, request: Request):
    service: RunService = request.app.state.run_service
    run = service.poll_run(run_id)  # reconcile on read
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    manifest = manifest_for_run(request, run)

    uncertain = run.state == RunState.QUEUED and run.error_category == UNCERTAIN_SUBMISSION
    succeeded = run.state == RunState.SUCCEEDED
    kind = manifest.outputs.kind
    outputs = read_outputs(run.outputs_ref) if succeeded else {}
    raw = read_raw_output(run) if succeeded else None

    ctx = {
        "run": run,
        "manifest": manifest,
        "kind": kind,
        "terminal": run.state in TERMINAL_STATES,
        "uncertain": uncertain,
        "raw_json": json.dumps(raw, indent=2, sort_keys=True) if raw else None,
        "raw_stdout": (raw or {}).get("stdout"),
    }
    if succeeded and kind == "structure":
        structure = outputs.get("structure") or ""
        sequence = outputs.get("sequence") or ""
        ctx["structure"] = structure
        ctx["sequence"] = sequence
        ctx["struct_metrics"] = structure_metrics(structure) if structure else None
        ctx["structure_svg"] = render_structure_svg(sequence, structure) if structure else ""
    elif succeeded:
        sequences = outputs.get("sequences", [])
        ctx["sequences"] = sequences
        ctx["metrics"] = sequence_metrics(sequences)

    return templates.TemplateResponse(request, "run.html", ctx)
