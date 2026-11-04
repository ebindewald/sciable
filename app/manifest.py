"""Load and validate versioned method manifests.

One manifest drives input validation, the web form, and the API so they cannot
diverge. It is also the source for the immutable MethodRelease record.

A manifest declares its inputs as a list of typed ``fields`` and its output by
``kind``; the generic contract (``FieldSpec`` / ``OutputSpec``) is what lets a
second method (structRFM) come up by adding a manifest + a runner, without
reshaping the control plane. Method-specific input logic (e.g. gRNAde's PDB
chain parsing) lives behind a per-method validator in ``app.validation``.

Methods live under ``methods/<method_id>/manifest.json``. ``get_manifest`` loads
one by id (default: the active method from settings); ``discover_manifests``
enumerates all of them.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from app.config import REPO_ROOT, get_settings
from app.models import MethodRelease

METHODS_DIR = REPO_ROOT / "methods"

#: Input field types the generic validator and the form renderer understand.
FIELD_TYPES = {"rna_sequence", "pdb_structure", "text", "int"}
#: Output kinds the storage + results views understand.
OUTPUT_KINDS = {"sequences", "structure"}


class FieldSpec(BaseModel):
    """One declared input field. Drives validation, the form widget, and the
    worker payload. Bounds are optional and interpreted per ``type``."""

    name: str
    type: str
    required: bool = False
    label: str | None = None
    help: str | None = None
    default: int | str | None = None
    # Numeric bounds (type == "int").
    min: int | None = None
    max: int | None = None
    # Size bounds: characters for text/rna_sequence, bytes for an uploaded file.
    max_length: int | None = None
    max_bytes: int | None = None

    @model_validator(mode="after")
    def _check_type(self) -> FieldSpec:
        if self.type not in FIELD_TYPES:
            raise ValueError(f"unknown field type {self.type!r} (allowed: {sorted(FIELD_TYPES)})")
        return self


class InputsSpec(BaseModel):
    fields: list[FieldSpec] = Field(min_length=1)

    def field(self, name: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.name == name), None)


class OutputSpec(BaseModel):
    kind: str
    format: str | None = None

    @model_validator(mode="after")
    def _check_kind(self) -> OutputSpec:
        if self.kind not in OUTPUT_KINDS:
            raise ValueError(f"unknown output kind {self.kind!r} (allowed: {sorted(OUTPUT_KINDS)})")
        return self


class Manifest(BaseModel):
    method_id: str
    version: str
    title: str
    citation: str
    source_commit: str
    checkpoint_digest: str
    worker_image_digest: str
    provisional: bool = False
    #: One-line description of what the method does, shown on the form page.
    description: str | None = None
    #: Runner script (under worker/) the handler invokes for this method.
    runner: str
    inputs: InputsSpec
    outputs: OutputSpec
    limits: dict = Field(default_factory=dict)
    example_ref: str | None = None
    example_summary: str | None = None
    example_details: str | None = None
    notes: str | None = None

    @property
    def release_id(self) -> str:
        return f"{self.method_id}@{self.version}"

    def example_path(self) -> Path | None:
        if not self.example_ref:
            return None
        return REPO_ROOT / self.example_ref

    def to_release(self, created_at: str) -> MethodRelease:
        return MethodRelease(
            id=self.release_id,
            version=self.version,
            input_schema={"fields": [f.model_dump() for f in self.inputs.fields]},
            output_schema=self.outputs.model_dump(),
            source_digest=self.source_commit,
            checkpoint_digest=self.checkpoint_digest,
            worker_image_digest=self.worker_image_digest,
            exec_requirements=dict(self.limits),
            example_ref=self.example_ref,
            citation=self.citation,
            created_at=created_at,
        )


def _manifest_path(method_id: str) -> Path:
    return METHODS_DIR / method_id / "manifest.json"


def load_manifest(method_id: str) -> Manifest:
    data = json.loads(_manifest_path(method_id).read_text())
    return Manifest.model_validate(data)


def discover_manifests() -> dict[str, Manifest]:
    """Every method under ``methods/`` that has a manifest.json, keyed by id."""
    found: dict[str, Manifest] = {}
    if not METHODS_DIR.exists():
        return found
    for child in sorted(METHODS_DIR.iterdir()):
        if (child / "manifest.json").exists():
            m = load_manifest(child.name)
            found[m.method_id] = m
    return found


@lru_cache
def get_manifest(method_id: str | None = None) -> Manifest:
    """The active method's manifest. ``method_id`` defaults to ``settings.method``."""
    return load_manifest(method_id or get_settings().method)
