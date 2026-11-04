"""RunpodBackend against a mocked HTTP transport — no network, no credentials."""

from __future__ import annotations

import json

import httpx
import pytest

from app.backends.base import JobSpec, SubmitStatus
from app.backends.runpod import BackendError, RunpodBackend
from app.config import Settings
from app.models import RunState


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "testkey")


def _settings() -> Settings:
    return Settings(backend="runpod", runpod_endpoint_id="ep123")


def _backend(handler) -> RunpodBackend:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer testkey"},
    )
    return RunpodBackend(_settings(), client=client)


JOB = JobSpec(
    fingerprint="fp",
    seed=1,
    method_id="grnade",
    output_kind="sequences",
    inputs={"structure": "PDB", "chain": "A", "design_count": 2},
    endpoint_ref="ep123",
)


def test_requires_endpoint_id(monkeypatch):
    with pytest.raises(BackendError):
        RunpodBackend(Settings(backend="runpod", runpod_endpoint_id=None))


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    with pytest.raises(BackendError):
        RunpodBackend(_settings())


def test_submit_accepted():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "job1", "status": "IN_QUEUE"})

    result = _backend(handler).submit(JOB)
    assert result.status is SubmitStatus.ACCEPTED
    assert result.provider_job_id == "job1"
    assert seen["path"] == "/v2/ep123/run"
    assert seen["auth"] == "Bearer testkey"
    assert seen["body"]["input"]["method"] == "grnade"
    assert seen["body"]["input"]["inputs"]["structure"] == "PDB"
    assert seen["body"]["input"]["inputs"]["design_count"] == 2


def test_submit_timeout_is_uncertain():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    result = _backend(handler).submit(JOB)
    assert result.status is SubmitStatus.UNCERTAIN
    assert result.provider_job_id is None


def test_submit_auth_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(BackendError):
        _backend(handler).submit(JOB)


@pytest.mark.parametrize(
    "raw,expected_state,expected_cat",
    [
        ("IN_QUEUE", RunState.QUEUED, None),
        ("IN_PROGRESS", RunState.RUNNING, None),
        ("FAILED", RunState.FAILED, "worker_error"),
        ("CANCELLED", RunState.CANCELED, None),
        ("TIMED_OUT", RunState.FAILED, "timeout"),
    ],
)
def test_status_mapping(raw, expected_state, expected_cat):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "job1", "status": raw})

    st = _backend(handler).status("job1")
    assert st.state is expected_state
    assert st.error_category == expected_cat


def test_status_completed_returns_outputs():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "job1",
                "status": "COMPLETED",
                "output": {"outputs": {"sequences": ["ACGU", "UGCA"]}, "report": {"stub": True}},
            },
        )

    st = _backend(handler).status("job1")
    assert st.state is RunState.SUCCEEDED
    assert st.outputs == {"sequences": ["ACGU", "UGCA"]}
    assert st.raw["backend"] == "runpod"


def test_status_completed_structure_outputs():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "j",
                "status": "COMPLETED",
                "output": {"outputs": {"sequence": "GGGGAAAACCCC", "structure": "((((....))))"}},
            },
        )

    st = _backend(handler).status("j")
    assert st.state is RunState.SUCCEEDED
    assert st.outputs["structure"] == "((((....))))"


def test_status_completed_with_worker_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "j", "status": "COMPLETED", "output": {"error": "no_output"}}
        )

    st = _backend(handler).status("j")
    assert st.state is RunState.FAILED
    assert st.error_category == "no_output"


def test_status_404_is_expired():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    st = _backend(handler).status("gone")
    assert st.state is RunState.FAILED
    assert st.error_category == "expired_or_unknown"


def test_cancel():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/ep123/cancel/job1"
        return httpx.Response(200, json={"status": "CANCELLED"})

    assert _backend(handler).cancel("job1") is True
