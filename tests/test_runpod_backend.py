"""RunpodBackend: FAILED-detail capture (fix #1) and per-deployment endpoint
routing (fix #2).

Fix #2 history: the backend first bound ONE endpoint at startup from the active
method. A multi-method server then routed cross-method submissions to the wrong
endpoint, where the runner's stub fallback produced a silent fake success
(rhofold, 2026-11-07). The endpoint now comes from each job's deployment
(``JobSpec.endpoint_ref``; status/cancel take it explicitly), and bootstrap
refreshes a stale deployment ``endpoint_ref`` from current settings.
"""

from __future__ import annotations

import httpx
import pytest

from app.backends.base import JobSpec
from app.backends.runpod import BackendError, RunpodBackend
from app.bootstrap import ensure_seeded
from app.db import get_conn, init_db
from app.models import RunState


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "testkey")


def _backend(settings, update, responder):
    s = settings.model_copy(update={"backend": "runpod", **update})
    return RunpodBackend(s, client=httpx.Client(transport=httpx.MockTransport(responder)))


# -- fix #1: FAILED jobs carry the real category + detail -------------------------


def test_failed_job_captures_category_and_detail(settings):
    """Runpod FAILED with a top-level error (our returned {"error": ...}) → the
    specific category and the stderr-tail detail are surfaced, not a bare label."""

    def responder(_req):
        return httpx.Response(
            200,
            json={
                "status": "FAILED",
                "error": "unknown_method",
                "output": {"detail": "No runner for method 'rhofold'."},
            },
        )

    st = _backend(settings, {"runpod_endpoint_id": "ep"}, responder).status("job-1", "ep")
    assert st.state is RunState.FAILED
    assert st.error_category == "unknown_method"
    assert st.raw["detail"] == "No runner for method 'rhofold'."


def test_completed_job_with_error_in_output_is_failed(settings):
    """Handler-caught WorkerError surfaced as COMPLETED + output.error."""

    def responder(_req):
        return httpx.Response(
            200,
            json={"status": "COMPLETED", "output": {"error": "worker_error", "detail": "exit 1"}},
        )

    st = _backend(settings, {"runpod_endpoint_id": "ep"}, responder).status("job-1", "ep")
    assert st.state is RunState.FAILED
    assert st.error_category == "worker_error"
    assert st.raw["detail"] == "exit 1"


def test_timeout_category_is_not_clobbered(settings):
    """A TIMED_OUT job keeps the 'timeout' category even if an error key is present."""

    def responder(_req):
        return httpx.Response(
            200, json={"status": "TIMED_OUT", "error": "worker_error", "output": {"detail": "x"}}
        )

    st = _backend(settings, {"runpod_endpoint_id": "ep"}, responder).status("job-1", "ep")
    assert st.state is RunState.FAILED
    assert st.error_category == "timeout"


def test_succeeded_job_returns_outputs_and_raw(settings):
    def responder(_req):
        return httpx.Response(
            200,
            json={
                "status": "COMPLETED",
                "output": {"outputs": {"sequences": ["GG"]}, "report": {"e": 1}},
            },
        )

    st = _backend(settings, {"runpod_endpoint_id": "ep"}, responder).status("job-1", "ep")
    assert st.state is RunState.SUCCEEDED
    assert st.outputs == {"sequences": ["GG"]}
    assert st.raw["report"] == {"e": 1}


# -- fix #2: endpoint routes per deployment (JobSpec.endpoint_ref) ------------------


def _job(method_id: str, endpoint_ref: str) -> JobSpec:
    return JobSpec(
        fingerprint="f",
        seed=None,
        method_id=method_id,
        output_kind="sequences",
        inputs={"x": 1},
        endpoint_ref=endpoint_ref,
    )


def test_submit_routes_to_jobspec_endpoint(settings):
    """The deployment's endpoint wins — even when the active-method setting
    points elsewhere (the old startup-bound design's silent-stub trap)."""
    calls: list[str] = []

    def responder(req):
        calls.append(req.url.path)
        return httpx.Response(200, json={"id": "j1", "status": "IN_QUEUE"})

    be = _backend(settings, {"method": "structrfm", "runpod_endpoint_id": "ep-other"}, responder)
    be.submit(_job("rhofold", "ep-rho"))
    assert calls == ["/v2/ep-rho/run"]


def test_status_and_cancel_use_given_endpoint(settings):
    calls: list[str] = []

    def responder(req):
        calls.append(req.url.path)
        return httpx.Response(200, json={"id": "j1", "status": "IN_PROGRESS"})

    be = _backend(settings, {"runpod_endpoint_id": "ep-other"}, responder)
    be.status("j1", "ep-rho")
    be.cancel("j1", "ep-rho")
    assert calls == ["/v2/ep-rho/status/j1", "/v2/ep-rho/cancel/j1"]


@pytest.mark.parametrize("ref", ["", "PENDING-ENDPOINT-ID"])
def test_unset_endpoint_ref_raises(settings, ref):
    be = _backend(settings, {}, lambda r: httpx.Response(200))
    with pytest.raises(BackendError):
        be.submit(_job("rhofold", ref))


def test_bootstrap_refreshes_stale_endpoint_ref(settings, manifest):
    """A deployment seeded under an older config follows the current endpoint
    mapping on the next startup (INSERT OR IGNORE alone kept the stale ref)."""
    s1 = settings.model_copy(update={"backend": "runpod", "runpod_endpoint_id": "ep-old"})
    init_db(s1.db_path)
    dep_id = ensure_seeded(s1, manifest)
    s2 = s1.model_copy(
        update={"runpod_endpoint_ids": {manifest.method_id: "ep-new"}}
    )
    assert ensure_seeded(s2, manifest) == dep_id
    with get_conn(s2.db_path) as conn:
        row = conn.execute(
            "SELECT endpoint_ref FROM deployment WHERE id = ?", (dep_id,)
        ).fetchone()
    assert row["endpoint_ref"] == "ep-new"
