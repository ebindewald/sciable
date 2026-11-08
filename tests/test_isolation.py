"""Worker job isolation: private dirs, no cross-job leakage, cleanup, no shell injection."""

from __future__ import annotations

import pytest

from worker.handler import WorkerError, run_job


def _grnade(inputs: dict) -> dict:
    return {"method": "grnade", "inputs": inputs}


def test_job_runs_in_isolated_dirs(tmp_path, example_pdb, monkeypatch):
    monkeypatch.setenv("SCIABLE_ALLOW_STUB", "1")  # no checkpoint in CI
    result = run_job(
        _grnade({"structure": example_pdb, "chain": "A", "design_count": 3, "seed": 1}),
        base_dir=tmp_path,
        cleanup=False,
    )
    assert len(result.outputs["sequences"]) == 3
    assert all(set(s) <= set("ACGU") for s in result.outputs["sequences"])
    # The job ran in a private HOME under the per-job base — verified on the filesystem,
    # since the runner no longer echoes the absolute home path (it leaked the job id).
    homes = list(tmp_path.glob("*/home"))
    assert len(homes) == 1 and homes[0].is_dir()
    assert result.runner_report["fresh_home"] is True  # HOME started empty


def test_sequential_jobs_cannot_see_each_others_files(tmp_path, example_pdb, monkeypatch):
    monkeypatch.setenv("SCIABLE_ALLOW_STUB", "1")  # no checkpoint in CI
    run_job(_grnade({"structure": example_pdb, "seed": 1}), base_dir=tmp_path)  # cleanup=True
    # A succeeded and its whole job dir (incl. its .grnade_cache) was removed on completion.
    assert list(tmp_path.iterdir()) == []

    b = run_job(_grnade({"structure": example_pdb, "seed": 2}), base_dir=tmp_path, cleanup=False)
    # B got a fresh, private HOME that never saw A's cache.
    assert b.runner_report["fresh_home"] is True
    # Exactly one job dir remains — B's — with its own private HOME.
    remaining = list(tmp_path.iterdir())
    assert len(remaining) == 1 and (remaining[0] / "home").is_dir()


def test_cleanup_on_failure(tmp_path, example_pdb):
    failing = tmp_path / "boom.py"
    failing.write_text("import sys; sys.exit(1)\n")
    with pytest.raises(WorkerError) as exc:
        run_job(_grnade({"structure": example_pdb, "seed": 1}), base_dir=tmp_path, runner=failing)
    assert exc.value.category == "worker_error"
    assert [p for p in tmp_path.iterdir() if p.is_dir()] == []


def test_missing_output_is_error(tmp_path, example_pdb):
    noop = tmp_path / "noop.py"
    noop.write_text("print('{}')\n")  # exits 0 but writes no out.json
    with pytest.raises(WorkerError) as exc:
        run_job(_grnade({"structure": example_pdb, "seed": 1}), base_dir=tmp_path, runner=noop)
    assert exc.value.category == "no_output"


def test_args_passed_as_array_no_shell_injection(tmp_path, example_pdb, monkeypatch):
    monkeypatch.setenv("SCIABLE_ALLOW_STUB", "1")  # no checkpoint in CI
    sentinel = tmp_path / "pwned"
    malicious_chain = f"A; touch {sentinel}"
    result = run_job(
        _grnade({"structure": example_pdb, "chain": malicious_chain, "seed": 1}),
        base_dir=tmp_path,
        cleanup=False,
    )
    # The chain reached the runner as data in input.json, not a shell command.
    assert result.runner_report["chain_received"] == malicious_chain
    assert not sentinel.exists()


def test_unknown_method_is_error(tmp_path):
    with pytest.raises(WorkerError) as exc:
        run_job({"method": "nope", "inputs": {}}, base_dir=tmp_path)
    assert exc.value.category == "unknown_method"


def test_structrfm_runner_produces_matrix(tmp_path, monkeypatch):
    monkeypatch.setenv("SCIABLE_ALLOW_STUB", "1")  # no checkpoint in CI
    result = run_job(
        {"method": "structrfm", "inputs": {"sequence": "GGGGAAAACCCC"}},
        base_dir=tmp_path,
        cleanup=False,
    )
    out = result.outputs
    assert out["sequence"] == "GGGGAAAACCCC"
    assert out["matrix_type"] == "attention"
    m = out["matrix"]
    assert len(m) == 12 and all(len(row) == 12 for row in m)
    assert all(0.0 <= v <= 1.0 for row in m for v in row)
    assert result.runner_report["engine"] == "stub"  # no checkpoint in CI


def test_rhofold_runner_produces_pdb(tmp_path, monkeypatch):
    monkeypatch.setenv("SCIABLE_ALLOW_STUB", "1")  # no checkpoint in CI
    seq = "GGCACUUCGGUGCC"
    result = run_job(
        {"method": "rhofold", "inputs": {"sequence": seq}},
        base_dir=tmp_path,
        cleanup=False,
    )
    out = result.outputs
    assert out["sequence"] == seq
    assert out["engine"] == "stub"  # no checkpoint in CI
    assert out["pdb"].startswith("ATOM") and "END" in out["pdb"]
    assert out["pdb"].count("ATOM") == len(seq)  # one P atom per nucleotide
    # per-residue pLDDT parsed back from the B-factor column matches the output list
    from worker.run_rhofold import _plddt_from_pdb

    assert _plddt_from_pdb(out["pdb"]) == out["plddt"]
    assert len(out["plddt"]) == len(seq)
    assert all(40.0 <= v <= 95.0 for v in out["plddt"])
    assert result.runner_report["residues"] == len(seq)


def test_ribonanzanet_runner_produces_matrix(tmp_path, monkeypatch):
    monkeypatch.setenv("SCIABLE_ALLOW_STUB", "1")  # no checkpoint in CI
    result = run_job(
        {"method": "ribonanzanet", "inputs": {"sequence": "GGGGAAAACCCC"}},
        base_dir=tmp_path,
        cleanup=False,
    )
    out = result.outputs
    assert out["sequence"] == "GGGGAAAACCCC"
    assert out["matrix_type"] == "base_pairing"
    assert out["engine"] == "stub"  # no checkpoint in CI
    m = out["matrix"]
    assert len(m) == 12 and all(len(row) == 12 for row in m)
    assert all(0.0 <= v <= 1.0 for row in m for v in row)
    assert m == [list(r) for r in zip(*m, strict=True)]  # symmetric


@pytest.mark.parametrize("method", ["rhofold", "structrfm", "grnade", "ribonanzanet"])
def test_missing_checkpoint_fails_without_stub_opt_in(tmp_path, monkeypatch, method):
    """No checkpoint + no explicit stub opt-in = a loud failure, never a fake
    success. (A misrouted job on the wrong image was twice reported 'succeeded'
    with stub output before this gate — 2026-11-07.)"""
    monkeypatch.delenv("SCIABLE_ALLOW_STUB", raising=False)
    with pytest.raises(WorkerError) as exc:
        run_job(
            {"method": method, "inputs": {"sequence": "GGCC"}},
            base_dir=tmp_path,
        )
    assert exc.value.category == "worker_error"
    assert "Internal error: job failed" in str(exc.value)


def test_handler_entrypoint_shape(tmp_path, example_pdb, monkeypatch):
    monkeypatch.setenv("WORKER_WORK_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setenv("SCIABLE_ALLOW_STUB", "1")  # no checkpoint in CI
    from worker.handler import handler

    res = handler({"input": _grnade({"structure": example_pdb, "design_count": 2, "seed": 9})})
    assert len(res["outputs"]["sequences"]) == 2
    assert res["report"]["stub"] is True
