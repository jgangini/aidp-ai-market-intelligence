"""Local rate-limit attempts remain counted and individually observable; no network."""
import asyncio
from collections import deque
import hashlib
import json
from types import SimpleNamespace

import oci
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from ai_market_intelligence.runtime import model_bridge


class ModelBudgetExceeded(RuntimeError):
    pass


def service_error(status, cause=None):
    error = oci.exceptions.TransientServiceError(
        status=status, code="fixture", headers={}, message="controlled provider error"
    )
    error.__cause__ = cause
    return error


def test_diagnostic_status_keeps_root_cause_without_retrying_mixed_statuses():
    metadata = model_bridge.model_error_metadata(service_error(503, service_error(429)))
    assert metadata['provider_status'] == 429
    assert metadata['provider_statuses'] == [503, 429]
    assert model_bridge.model_rate_limited(metadata) is False


@pytest.fixture
def local_bridge(monkeypatch, tmp_path):
    identity = {"tenancy": "fixture", "user": "fixture", "fingerprint": "fixture"}
    monkeypatch.setattr(oci.config, "from_file", lambda *args: identity)
    monkeypatch.setattr(model_bridge, "random", lambda: 0.25)
    delays, created = [], []

    async def wait(delay):
        delays.append(delay)
        await asyncio.sleep(0)

    monkeypatch.setattr(model_bridge, "sleep", wait)

    def build(outcomes, *, budget=10, observed=True):
        pending = deque(outcomes)
        sdk_calls = []

        class Client:
            def __init__(self, *args, **kwargs):
                assert isinstance(kwargs["retry_strategy"], oci.retry.NoneRetryStrategy)
                self.base_client = SimpleNamespace(session=SimpleNamespace(close=lambda: None))

            def chat(self, details, **kwargs):
                assert isinstance(kwargs["retry_strategy"], oci.retry.NoneRetryStrategy)
                sdk_calls.append({"request_id": kwargs.get("opc_request_id"), "details": details})
                value = pending.popleft()
                if isinstance(value, BaseException):
                    raise value
                return SimpleNamespace(data=SimpleNamespace(chat_response=value), headers={})

        monkeypatch.setattr(oci.generative_ai_inference, "GenerativeAiInferenceClient", Client)

        class Tracked:
            client = None
            config_file = "fixture"
            profile = "fixture"
            calls = 0
            _complete = None
            settings = {"region": "fixture", "model_id": "fixture", "compartment_id": "fixture",
                        "max_tokens": 100, "reasoning_effort": "LOW", "max_calls": budget}

            def _final_text(self, value):
                return value

            async def complete(self, system, question):
                if self.calls >= int(self.settings["max_calls"]):
                    raise ModelBudgetExceeded("Normal tracker admission refused the call")
                self.calls += 1
                try:
                    return await asyncio.to_thread(self._complete, system, question)
                except Exception as error:
                    raise RuntimeError("Counted model execution failed") from error

            def close(self):
                pass

        tracked = Tracked()
        directory = tmp_path / "observations"
        bridge = model_bridge.StructuredBridge(tracked, {
            "identity_sha256": hashlib.sha256(json.dumps(["fixture"] * 3, separators=(",", ":")).encode()).hexdigest(),
            "guardrails_config": {"policies": []}, "model_endpoint": "https://example.invalid",
        }, observations_dir=directory if observed else None)
        created.append(bridge)
        bound = bridge.with_structured_output({"type": "object", "properties": {}}, "json_schema")
        messages = [SystemMessage(content="fixture instructions"), HumanMessage(content="fixture question")]

        def records():
            return [json.loads(path.read_bytes()) for path in sorted(directory.glob("*.json"))]

        return SimpleNamespace(bridge=bridge, tracked=tracked, sdk_calls=sdk_calls,
                               invoke=lambda: bound.ainvoke(messages), records=records,
                               pending=pending, delays=delays)

    yield build
    for bridge in created:
        bridge.close()


def test_429_then_success_has_two_counted_sdk_requests_and_distinct_observations(local_bridge):
    case = local_bridge([service_error(429), '{"ok":true}'])
    assert asyncio.run(case.invoke()) == {"ok": True}
    records = case.records()
    assert case.tracked.calls == len(case.sdk_calls) == len(records) == 2
    assert len({row["request_id"] for row in case.sdk_calls}) == 2
    assert {row["request_id"] for row in case.sdk_calls} == {row["client_request_id"] for row in records}
    assert [row["attempt"] for row in records] == [1, 2]
    assert len({row["invocation_id"] for row in records}) == 1
    assert len({row["input_sha256"] for row in records}) == 1
    assert len({row["schema_sha256"] for row in records}) == 1
    assert records[0]["state"] == "HTTP_RATE_LIMITED" and records[0]["provider_status"] == 429
    assert records[1]["state"] == "COMPLETE_RESPONSE"
    assert all("HTTP_SUBMISSION_INTENT" in [event["state"] for event in row["history"]] for row in records)
    assert case.delays == [1.25]
    assert all("cost_usd" not in row for row in records)


def test_rate_limit_stops_after_three_counted_attempts(local_bridge):
    case = local_bridge([service_error(429) for _ in range(4)])
    with pytest.raises(RuntimeError, match="Counted model execution failed"):
        asyncio.run(case.invoke())
    assert case.tracked.calls == len(case.sdk_calls) == len(case.records()) == 3
    assert len(case.pending) == 1 and case.delays == [1.25, 2.25]
    assert [row["attempt"] for row in case.records()] == [1, 2, 3]
    assert all(row["state"] == "HTTP_RATE_LIMITED" for row in case.records())


@pytest.mark.parametrize("failure", [
    TimeoutError("controlled read timeout"),
    service_error(500), service_error(503),
    service_error(503, service_error(429)),
    service_error(429, service_error(503)),
    service_error(429, TimeoutError("ambiguous transport failure")),
    service_error(429, json.JSONDecodeError("invalid provider body", "", 0)),
    RuntimeError("A message mentioning 429 is not an HTTP response"),
])
def test_only_unambiguous_429_is_retried(local_bridge, failure):
    case = local_bridge([failure, '{"ok":true}'])
    with pytest.raises(RuntimeError, match="Counted model execution failed"):
        asyncio.run(case.invoke())
    assert case.tracked.calls == len(case.sdk_calls) == len(case.records()) == 1
    assert case.records()[0]["state"] == "FAILED_UNKNOWN_USAGE"
    assert not case.delays


def test_invalid_model_json_is_preserved_without_another_attempt(local_bridge):
    case = local_bridge(["not JSON", '{"ok":true}'])
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(case.invoke())
    assert case.tracked.calls == len(case.sdk_calls) == len(case.records()) == 1
    assert case.records()[0]["state"] == "LOCAL_VALIDATION_FAILED"
    assert case.records()[0]["response"] == "not JSON" and not case.delays


def test_retry_does_not_bypass_the_tracker_call_budget(local_bridge):
    case = local_bridge([service_error(429), '{"ok":true}'], budget=1)
    with pytest.raises(RuntimeError, match="Counted model execution failed"):
        asyncio.run(case.invoke())
    assert case.tracked.calls == len(case.sdk_calls) == len(case.records()) == 1
    assert not case.delays


def test_unknown_capacity_still_uses_the_normal_tracker_guard(local_bridge):
    case = local_bridge([service_error(429), '{"ok":true}'], budget="1")
    with pytest.raises(ModelBudgetExceeded):
        asyncio.run(case.invoke())
    assert case.tracked.calls == len(case.sdk_calls) == 1
    records = case.records()
    assert len(records) == 2
    assert records[1]["state"] == "LOCAL_ADMISSION_REJECTED"
    assert [event["state"] for event in records[1]["history"]][0] == "CALL_PREPARED"
    assert not any(event["state"] in {"CALL_ADMITTED", "HTTP_SUBMISSION_INTENT"}
                   for event in records[1]["history"])


def test_capacity_consumed_during_backoff_prevents_a_retry(local_bridge, monkeypatch):
    case = local_bridge([service_error(429), '{"ok":true}'], budget=2)

    async def another_decision(delay):
        assert await case.invoke() == {"ok": True}

    monkeypatch.setattr(model_bridge, "sleep", another_decision)
    with pytest.raises(RuntimeError, match="Counted model execution failed"):
        asyncio.run(case.invoke())
    assert case.tracked.calls == len(case.sdk_calls) == len(case.records()) == 2
    assert case.records()[0]["retry_stopped"] == "model_budget"
    assert [row["attempt"] for row in case.records()] == [1, 1]


def test_cancelling_backoff_never_admits_a_second_call(local_bridge, monkeypatch):
    case = local_bridge([service_error(429), '{"ok":true}'])

    async def run():
        entered = asyncio.Event()

        async def blocked(delay):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(model_bridge, "sleep", blocked)
        invocation = asyncio.create_task(case.invoke())
        await entered.wait()
        invocation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await invocation
        assert case.tracked.calls == len(case.sdk_calls) == len(case.records()) == 1
        assert case.records()[0]["retry_wait_cancelled"] is True
        assert case.records()[0]["state"] == "HTTP_RATE_LIMITED"

    asyncio.run(run())


def test_missing_observation_recorder_keeps_single_call_behavior(local_bridge):
    case = local_bridge([service_error(429), '{"ok":true}'], observed=False)
    with pytest.raises(RuntimeError, match="Counted model execution failed"):
        asyncio.run(case.invoke())
    assert case.tracked.calls == len(case.sdk_calls) == 1
    assert not case.records() and not case.delays


def test_exhausted_budget_makes_zero_sdk_requests(local_bridge):
    case = local_bridge(['{"ok":true}'], budget=1)
    case.tracked.calls = 1
    with pytest.raises(ModelBudgetExceeded):
        asyncio.run(case.invoke())
    assert case.tracked.calls == 1 and not case.sdk_calls
    assert case.records()[0]["state"] == "LOCAL_ADMISSION_REJECTED"
    assert not case.delays


def test_429_outside_model_submission_is_not_retried(local_bridge, monkeypatch):
    case = local_bridge(['{"ok":true}'])

    def unavailable_config(*args):
        raise service_error(429)

    monkeypatch.setattr(oci.config, "from_file", unavailable_config)
    with pytest.raises(RuntimeError, match="Counted model execution failed"):
        asyncio.run(case.invoke())
    assert case.tracked.calls == 1 and not case.sdk_calls
    assert case.records()[0]["state"] == "FAILED_UNKNOWN_USAGE" and not case.delays
