"""Input validation: generic field rules + per-method hooks (gRNAde, structRFM)."""

from __future__ import annotations

import pytest

from app.manifest import load_manifest
from app.validation import ValidationError, parse_pdb_chains, validate_submission

# -- gRNAde (pdb_structure + chain resolution) --------------------------------


def test_accepts_example(manifest, example_pdb):
    result = validate_submission(manifest, raw={"structure": example_pdb, "seed": 7})
    assert result.values["chain"] == "A"
    assert result.values["design_count"] == 5  # manifest default
    assert result.seed == 7
    assert result.values["sequence_length"] == 6


def test_empty_input_rejected(manifest):
    with pytest.raises(ValidationError) as exc:
        validate_submission(manifest, raw={"structure": ""})
    assert exc.value.code == "empty_input"


def test_no_rna_chain_rejected(manifest):
    dna = (
        "ATOM      1  P    DA A   1       0.000   0.000   0.000  1.00  0.00           P\n"
        "ATOM      2  P    DT A   2       1.000   0.000   0.000  1.00  0.00           P\n"
    )
    with pytest.raises(ValidationError) as exc:
        validate_submission(manifest, raw={"structure": dna})
    assert exc.value.code == "no_rna_chain"


def _relabel_chain(pdb_text: str, new_chain: str) -> str:
    out = []
    for line in pdb_text.splitlines():
        if line.startswith(("ATOM", "HETATM")) and len(line) > 21:
            line = line[:21] + new_chain + line[22:]
        out.append(line)
    return "\n".join(out)


def test_ambiguous_multi_rna_chain_rejected(manifest, example_pdb):
    chain_b = _relabel_chain(example_pdb, "B")
    combined = example_pdb + "\n" + chain_b
    assert len(parse_pdb_chains(combined)) >= 2
    with pytest.raises(ValidationError) as exc:
        validate_submission(manifest, raw={"structure": combined})
    assert exc.value.code == "ambiguous_chain"


def test_explicit_chain_selection(manifest, example_pdb):
    raw = {"structure": example_pdb, "chain": "A", "design_count": 3}
    result = validate_submission(manifest, raw=raw)
    assert result.values["chain"] == "A"
    assert result.values["design_count"] == 3


def test_unknown_chain_rejected(manifest, example_pdb):
    with pytest.raises(ValidationError) as exc:
        validate_submission(manifest, raw={"structure": example_pdb, "chain": "Z"})
    assert exc.value.code == "unknown_chain"


def test_design_count_out_of_range(manifest, example_pdb):
    with pytest.raises(ValidationError) as exc:
        validate_submission(manifest, raw={"structure": example_pdb, "design_count": 99})
    assert exc.value.code == "design_count_out_of_range"


# -- structRFM (rna_sequence) -------------------------------------------------


def test_structrfm_accepts_sequence():
    m = load_manifest("structrfm")
    r = validate_submission(m, raw={"sequence": "GGGGAAAACCCC", "seed": 1})
    assert r.values["sequence"] == "GGGGAAAACCCC"
    assert r.seed == 1


def test_structrfm_rejects_bad_alphabet():
    m = load_manifest("structrfm")
    with pytest.raises(ValidationError) as exc:
        validate_submission(m, raw={"sequence": "GGGZZZCCC"})
    assert exc.value.code == "invalid_alphabet"


def test_structrfm_strips_fasta_and_normalizes():
    m = load_manifest("structrfm")
    r = validate_submission(m, raw={"sequence": ">hdr\nggggaaaa cccc\n"})
    assert r.values["sequence"] == "GGGGAAAACCCC"  # upper-cased, whitespace/header stripped


def test_structrfm_missing_sequence_rejected():
    m = load_manifest("structrfm")
    with pytest.raises(ValidationError) as exc:
        validate_submission(m, raw={})
    assert exc.value.code == "missing_sequence"
