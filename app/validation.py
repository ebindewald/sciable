"""Manifest-driven input validation.

Generic, type-directed validation over the manifest's declared ``inputs.fields``
(int / text / rna_sequence / pdb_structure), plus a per-method hook for logic
that cannot be generic — e.g. gRNAde's requirement of exactly one RNA chain in a
PDB. Anything unsupported or ambiguous is rejected with a clear, category-tagged
error rather than guessed at.

The result is a generic :class:`ValidatedSubmission` (normalized field values +
the resolved seed + the material that defines run identity), so the run service,
fingerprint, and backends stay method-agnostic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.manifest import FieldSpec, Manifest

# Standard RNA residue names (and common variants). DNA / protein residues must
# be rejected — an RNA method cannot speak to them.
_RNA_RESIDUES = {"A", "U", "G", "C", "RA", "RU", "RG", "RC", "I", "N"}
_DNA_RESIDUES = {"DA", "DT", "DG", "DC", "DU", "DI", "DN", "T"}
#: Single-letter alphabet for a typed-in/FASTA RNA sequence (N = unknown).
_RNA_SEQUENCE_ALPHABET = set("ACGUN")


class ValidationError(ValueError):
    """Raised for invalid or ambiguous submissions. ``code`` is a stable category."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedSubmission:
    #: Normalized input values keyed by field name (plus any method-derived keys,
    #: e.g. gRNAde's resolved ``chain`` / ``sequence_length``).
    values: dict[str, Any]
    #: The seed field if the method declares one; cross-cutting (reproducibility
    #: + fake-backend test sentinels).
    seed: int | None
    #: Ordered strings that define run identity (hashed into the fingerprint).
    fingerprint_material: list[str]


# -- generic, per-type field validators ---------------------------------------


def _validate_int(spec: FieldSpec, raw: Any) -> int | None:
    if raw in (None, ""):
        raw = spec.default
    if raw in (None, ""):
        if spec.required:
            raise ValidationError(f"missing_{spec.name}", f"{spec.label or spec.name} is required.")
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"invalid_{spec.name}", f"{spec.name} must be an integer.") from exc
    if spec.min is not None and val < spec.min:
        raise ValidationError(
            f"{spec.name}_out_of_range", f"{spec.name} must be >= {spec.min}."
        )
    if spec.max is not None and val > spec.max:
        raise ValidationError(
            f"{spec.name}_out_of_range", f"{spec.name} must be <= {spec.max}."
        )
    return val


def _validate_text(spec: FieldSpec, raw: Any) -> str | None:
    if raw in (None, ""):
        if spec.required:
            raise ValidationError(f"missing_{spec.name}", f"{spec.label or spec.name} is required.")
        return spec.default if isinstance(spec.default, str) else None
    s = str(raw).strip()
    if spec.max_length is not None and len(s) > spec.max_length:
        raise ValidationError(
            f"{spec.name}_too_long", f"{spec.name} exceeds {spec.max_length} characters."
        )
    return s


def _extract_sequence(raw: Any) -> str:
    """Pull a bare sequence out of plain text or a FASTA record."""
    body = [
        ln.strip()
        for ln in str(raw).splitlines()
        if ln.strip() and not ln.lstrip().startswith(">")
    ]
    return "".join(body).replace(" ", "")


def _validate_rna_sequence(spec: FieldSpec, raw: Any) -> str:
    if raw in (None, ""):
        raise ValidationError(f"missing_{spec.name}", f"{spec.label or spec.name} is required.")
    seq = _extract_sequence(raw).upper().replace("T", "U")
    if not seq:
        raise ValidationError("empty_sequence", "No RNA sequence was provided.")
    bad = set(seq) - _RNA_SEQUENCE_ALPHABET
    if bad:
        raise ValidationError(
            "invalid_alphabet",
            f"Sequence contains non-RNA characters: {sorted(bad)}. Allowed: A, C, G, U (N).",
        )
    if spec.max_length is not None and len(seq) > spec.max_length:
        raise ValidationError(
            "sequence_too_long",
            f"Sequence length {len(seq)} exceeds the {spec.max_length}-nucleotide limit.",
        )
    return seq


def _validate_pdb_structure(spec: FieldSpec, raw: Any) -> str:
    if raw in (None, ""):
        if spec.required:
            raise ValidationError("empty_input", "No structure content was provided.")
        return ""
    text = str(raw)
    nbytes = len(text.encode("utf-8"))
    if nbytes == 0:
        raise ValidationError("empty_input", "No PDB content was provided.")
    if spec.max_bytes is not None and nbytes > spec.max_bytes:
        raise ValidationError(
            "file_too_large", f"Structure exceeds the {spec.max_bytes}-byte limit."
        )
    return text


_FIELD_VALIDATORS: dict[str, Callable[[FieldSpec, Any], Any]] = {
    "int": _validate_int,
    "text": _validate_text,
    "rna_sequence": _validate_rna_sequence,
    "pdb_structure": _validate_pdb_structure,
}


# -- PDB parsing (used by the gRNAde method validator and the web example card) -


@dataclass
class _Chain:
    chain_id: str
    residues: list[str]  # residue names in order of first appearance


def parse_pdb_chains(pdb_text: str) -> dict[str, _Chain]:
    """Extract chains and their residue sequences from ATOM/HETATM records.

    Fixed-column parsing (PDB spec): resname = cols 18-20, chain = col 22,
    resseq = cols 23-26.
    """
    chains: dict[str, _Chain] = {}
    seen_residue: set[tuple[str, str]] = set()
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        resname = line[17:20].strip().upper()
        chain_id = line[21:22].strip() or "_"
        resseq = line[22:26].strip()
        key = (chain_id, resseq)
        if key in seen_residue:
            continue
        seen_residue.add(key)
        chains.setdefault(chain_id, _Chain(chain_id, [])).residues.append(resname)
    return chains


def _classify_chain(chain: _Chain) -> str:
    names = {r for r in chain.residues if r}
    if names & _DNA_RESIDUES:
        return "dna"
    if names and names <= _RNA_RESIDUES:
        return "rna"
    return "other"


def _normalize_pdb(pdb_text: str) -> str:
    """Only the coordinate/sequence-relevant records, so cosmetic header or
    whitespace differences do not change a structure's identity."""
    lines = [
        line.rstrip()
        for line in pdb_text.splitlines()
        if line.startswith(("ATOM", "HETATM", "TER"))
    ]
    return "\n".join(lines)


# -- per-method validators (logic that cannot be generic) ---------------------


def _validate_grnade(manifest: Manifest, values: dict[str, Any]) -> None:
    """Resolve the RNA chain and enforce the single-RNA-chain contract."""
    pdb_text = values.get("structure") or ""
    chains = parse_pdb_chains(pdb_text)
    if not chains:
        raise ValidationError(
            "no_residues", "No ATOM/HETATM records with residues were found in the PDB."
        )
    rna_chains = {cid for cid, c in chains.items() if _classify_chain(c) == "rna"}

    chain = values.get("chain")
    if chain:
        if chain not in chains:
            raise ValidationError("unknown_chain", f"Chain '{chain}' is not present in the PDB.")
        if chain not in rna_chains:
            raise ValidationError("not_rna_chain", f"Chain '{chain}' is not a supported RNA chain.")
        selected = chain
    else:
        if len(rna_chains) == 0:
            raise ValidationError("no_rna_chain", "No RNA chain was found in the PDB.")
        if len(rna_chains) > 1:
            raise ValidationError(
                "ambiguous_chain",
                f"Multiple RNA chains ({sorted(rna_chains)}); specify which one to design.",
            )
        selected = next(iter(rna_chains))

    seq_len = len(chains[selected].residues)
    max_len = manifest.limits.get("max_sequence_length")
    if max_len is not None and seq_len > max_len:
        raise ValidationError(
            "sequence_too_long",
            f"Chain '{selected}' length {seq_len} exceeds the {max_len}-residue limit.",
        )
    values["chain"] = selected
    values["sequence_length"] = seq_len


def _validate_ribonanzanet(manifest: Manifest, values: dict[str, Any]) -> None:
    """RibonanzaNet-SS's tokenizer is hardcoded to A/C/G/U (no unknown-base token), so
    an N that the generic RNA alphabet allows would KeyError at inference. Reject it at
    submit with a clear message instead of a worker crash."""
    seq = values.get("sequence") or ""
    bad = set(seq) - set("ACGU")
    if bad:
        raise ValidationError(
            "unsupported_base",
            f"RibonanzaNet-SS supports only A/C/G/U (no {sorted(bad)}).",
        )


_METHOD_VALIDATORS: dict[str, Callable[[Manifest, dict[str, Any]], None]] = {
    "grnade": _validate_grnade,
    "ribonanzanet": _validate_ribonanzanet,
}


# -- entry point --------------------------------------------------------------


def _fingerprint_material(manifest: Manifest, values: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for spec in manifest.inputs.fields:
        v = values.get(spec.name)
        if spec.type == "pdb_structure":
            parts.append(_normalize_pdb(v or ""))
        else:
            parts.append("none" if v is None else str(v))
    return parts


def validate_submission(manifest: Manifest, *, raw: dict[str, Any]) -> ValidatedSubmission:
    """Validate a raw submission against the manifest. ``raw`` maps field names
    to their provided values (strings from a form, or native JSON values)."""
    values: dict[str, Any] = {}
    seed: int | None = None
    for spec in manifest.inputs.fields:
        validator = _FIELD_VALIDATORS.get(spec.type)
        if validator is None:  # pragma: no cover - guarded by Manifest validation
            raise ValidationError("unknown_field_type", f"Unsupported field type {spec.type!r}.")
        value = validator(spec, raw.get(spec.name))
        values[spec.name] = value
        if spec.name == "seed":
            seed = value

    method_validator = _METHOD_VALIDATORS.get(manifest.method_id)
    if method_validator is not None:
        method_validator(manifest, values)

    return ValidatedSubmission(
        values=values,
        seed=seed,
        fingerprint_material=_fingerprint_material(manifest, values),
    )
