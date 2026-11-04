"""Manifests load, validate the generic field/output contract, and map to a release."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.manifest import FieldSpec, discover_manifests, get_manifest, load_manifest


def test_grnade_manifest_loads():
    m = get_manifest("grnade")
    assert m.method_id == "grnade"
    assert m.outputs.kind == "sequences"
    dc = m.inputs.field("design_count")
    assert dc is not None and dc.default == 5 and dc.max == 10
    assert m.inputs.field("structure").type == "pdb_structure"
    assert m.release_id == "grnade@0.1.0-dev"


def test_structrfm_manifest_loads():
    m = load_manifest("structrfm")
    assert m.method_id == "structrfm"
    assert m.outputs.kind == "structure"
    assert m.inputs.field("sequence").type == "rna_sequence"
    assert m.runner == "run_structrfm.py"


def test_discover_finds_both_methods():
    methods = discover_manifests()
    assert {"grnade", "structrfm"} <= set(methods)


def test_example_file_exists(manifest):
    assert manifest.example_path().exists()


def test_to_release_carries_schema_and_digests(manifest):
    release = manifest.to_release(created_at="2026-11-04T00:00:00+00:00")
    assert release.id == manifest.release_id
    assert release.source_digest == manifest.source_commit
    assert release.citation
    assert "fields" in release.input_schema
    assert release.output_schema["kind"] == "sequences"


def test_field_rejects_unknown_type():
    with pytest.raises(ValidationError):
        FieldSpec(name="x", type="bogus")


def test_output_rejects_unknown_kind():
    from app.manifest import OutputSpec

    with pytest.raises(ValidationError):
        OutputSpec(kind="nonsense")


def test_reload_is_consistent():
    assert get_manifest("grnade").release_id == load_manifest("grnade").release_id
