"""Worker job isolation: private dirs, no cross-job leakage, cleanup, no shell injection."""

from __future__ import annotations

from pathlib import Path

import pytest

from worker.handler import WorkerError, run_job


def _grnade(inputs: dict) -> dict:
    return {"method": "grnade", "inputs": inputs}


def test_job_runs_in_isolated_dirs(tmp_path, example_pdb):
    result = run_job(
        _grnade({"structure": example_pdb, "chain": "A", "design_count": 3, "seed": 1}),
        base_dir=tmp_path,
        cleanup=False,
    )
    assert len(result.outputs["sequences"]) == 3
    assert all(set(s) <= set("ACGU") for s in result.outputs["sequences"])
    # The job's HOME is private, under the base dir — not the real $HOME.
    assert Path(result.runner_report["home"]).is_relative_to(tmp_path)


def test_sequential_jobs_cannot_see_each_others_files(tmp_path, example_pdb):
    a = run_job(_grnade({"structure": example_pdb, "seed": 1}), base_dir=tmp_path)
    a_home = Path(a.runner_report["home"])

    b = run_job(_grnade({"structure": example_pdb, "seed": 2}), base_dir=tmp_path, cleanup=False)
    b_home = Path(b.runner_report["home"])

    assert a_home != b_home
    assert ".grnade_cache" not in b.runner_report["seen_at_start"]
    assert not a_home.exists()  # A's directory was removed on completion

    remaining = [p.name for p in tmp_path.iterdir()]
    assert remaining == [b_home.parent.name]


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


def test_args_passed_as_array_no_shell_injection(tmp_path, example_pdb):
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


def test_structrfm_runner_produces_dot_bracket(tmp_path):
    result = run_job(
        {"method": "structrfm", "inputs": {"sequence": "GGGGAAAACCCC"}},
        base_dir=tmp_path,
        cleanup=False,
    )
    assert result.outputs["sequence"] == "GGGGAAAACCCC"
    assert set(result.outputs["structure"]) <= set("().")
    assert len(result.outputs["structure"]) == 12


def test_handler_entrypoint_shape(tmp_path, example_pdb, monkeypatch):
    monkeypatch.setenv("WORKER_WORK_ROOT", str(tmp_path / "jobs"))
    from worker.handler import handler

    res = handler({"input": _grnade({"structure": example_pdb, "design_count": 2, "seed": 9})})
    assert len(res["outputs"]["sequences"]) == 2
    assert res["report"]["stub"] is True
