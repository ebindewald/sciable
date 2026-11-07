"""RunpodBackend: FAILED-detail capture (fix #1) and per-method endpoint
resolution (fix #2).

These are the two gaps that left a rhofold run showing a bare ``worker_error``
with no detail, submitted to the wrong (shared) endpoint.
"""

from __future__ import annotations

import httpx
import pytest

from app.backends.base import JobSpec
from app.backends.runpod import BackendError, RunpodBackend
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

    st = _backend(settings, {"runpod_endpoint_id": "ep"}, responder).status("job-1")
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

    st = _backend(settings, {"runpod_endpoint_id": "ep"}, responder).status("job-1")
    assert st.state is RunState.FAILED
    assert st.error_category == "worker_error"
    assert st.raw["detail"] == "exit 1"


def test_timeout_category_is_not_clobbered(settings):
    """A TIMED_OUT job keeps the 'timeout' category even if an error key is present."""

    def responder(_req):
        return httpx.Response(
            200, json={"status": "TIMED_OUT", "error": "worker_error", "output": {"detail": "x"}}
        )

    st = _backend(settings, {"runpod_endpoint_id": "ep"}, responder).status("job-1")
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

    st = _backend(settings, {"runpod_endpoint_id": "ep"}, responder).status("job-1")
    assert st.state is RunState.SUCCEEDED
    assert st.outputs == {"sequences": ["GG"]}
    assert st.raw["report"] == {"e": 1}


# -- fix #2: endpoint resolves per active method ----------------------------------


def _submit_path(settings, update):
    calls: list[str] = []

    def responder(req):
        calls.append(req.url.path)
        return httpx.Response(200, json={"id": "j1", "status": "IN_QUEUE"})

    be = _backend(settings, update, responder)
    be.submit(
        JobSpec(
            fingerprint="f",
            seed=None,
            method_id=update.get("method", "grnade"),
            output_kind="sequences",
            inputs={"x": 1},
            endpoint_ref="unused",
        )
    )
    return calls


def test_endpoint_resolves_per_method(settings):
    calls = _submit_path(
        settings,
        {"method": "rhofold", "runpod_endpoint_id": "ep-default",
         "runpod_endpoint_ids": {"rhofold": "ep-rho"}},
    )
    assert calls == ["/v2/ep-rho/run"]


def test_endpoint_falls_back_to_global(settings):
    calls = _submit_path(
        settings,
        {"method": "grnade", "runpod_endpoint_id": "ep-default",
         "runpod_endpoint_ids": {"rhofold": "ep-rho"}},
    )
    assert calls == ["/v2/ep-default/run"]


def test_missing_endpoint_raises(settings):
    s = settings.model_copy(
        update={"backend": "runpod", "method": "rhofold",
                "runpod_endpoint_id": None, "runpod_endpoint_ids": {}}
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    with pytest.raises(BackendError):
        RunpodBackend(s, client=httpx.Client(transport=transport))
