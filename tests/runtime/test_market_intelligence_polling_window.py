"""Actor polling covers its admitted execution window without restarting captures."""
import asyncio
from types import SimpleNamespace

import pytest

from ai_market_intelligence.runtime import collector as module
from ai_market_intelligence.runtime.runtime_core import ProviderReceiptUnverified, ProviderStatusUnavailable
from tests.runtime.test_market_intelligence_research_collector import market_collection_fixture


def polling_fixture(monkeypatch, *, finished_at=None, attempts=None):
    provider, effects, _ = market_collection_fixture()
    collector = module.MarketCollector(provider, effects, poll_attempts=attempts,
                                       poll_seconds=0 if attempts == 1 else 5)
    clock = SimpleNamespace(seconds=0)
    original_sleep = asyncio.sleep

    async def sleep(seconds):
        clock.seconds += seconds
        await original_sleep(0)

    async def read(run_id):
        provider.reads.append(run_id)
        complete = finished_at is not None and clock.seconds >= finished_at
        return {"runId": run_id, "status": "SUCCEEDED" if complete else "RUNNING",
                "datasetId": "existing-dataset" if complete else None,
                "spent_microusd": 500 if complete else None}

    monkeypatch.setattr(module, "monotonic", lambda: clock.seconds)
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    provider.read = read
    call = collector.call("website", {"start": "2026-11-01T00:00:00+00:00",
                                      "end": "2026-11-02T00:00:00+00:00"},
                          urls=["https://example.com/article"])
    return provider, effects, collector, clock, call


@pytest.mark.parametrize("timeout,finished_at", [(120, 85), (180, 115)])
def test_default_polling_waits_for_the_same_actor_beyond_the_old_minute(
    monkeypatch, timeout, finished_at
):
    provider, effects, collector, clock, call = polling_fixture(monkeypatch, finished_at=finished_at)
    call["callOptions"]["timeout"] = timeout
    result, receipt = asyncio.run(collector.operation("capture", call))
    assert result["items"] and receipt["status"] == "SUCCEEDED"
    assert finished_at <= clock.seconds <= timeout + 15
    assert len(provider.starts) == 1 and set(provider.reads) == {receipt["runId"]}
    assert effects.operation("capture")["state"] == "completed"


def test_exhausted_window_preserves_the_receipt_and_returns_pending(monkeypatch):
    provider, effects, collector, clock, call = polling_fixture(monkeypatch)
    result, receipt = asyncio.run(collector.operation("capture", call))
    assert result is None and receipt["status"] == "RUNNING"
    assert clock.seconds == call["callOptions"]["timeout"] + 15
    assert len(provider.starts) == 1 and len(provider.reads) <= 49
    operation = effects.operation("capture")
    assert operation["state"] == "running" and operation["receipt"] == receipt


def test_explicit_single_check_keeps_offline_and_recovery_controls_fast(monkeypatch):
    provider, effects, collector, clock, call = polling_fixture(monkeypatch, attempts=1)
    result, receipt = asyncio.run(collector.operation("capture", call))
    assert result is None and clock.seconds == 0
    assert len(provider.starts) == len(provider.reads) == 1
    assert effects.operation("capture")["receipt"] == receipt


@pytest.mark.parametrize("interval", [0, 0.5])
def test_automatic_polling_cannot_spin_below_one_second(interval):
    with pytest.raises(ValueError, match="finite bounds"):
        module.MarketCollector(None, None, poll_seconds=interval)


@pytest.mark.parametrize("error_type", [ProviderStatusUnavailable, ProviderReceiptUnverified])
def test_transient_receipt_read_retries_same_capture_and_keeps_safe_diagnostic(monkeypatch, error_type):
    provider, effects, collector, clock, call = polling_fixture(monkeypatch, finished_at=0)
    successful_read = provider.read
    failures = []

    async def read(run_id):
        if not failures:
            failures.append(run_id)
            raise error_type("private provider details must not be recorded")
        return await successful_read(run_id)

    provider.read = read
    result, receipt = asyncio.run(collector.operation("capture", call))
    assert result["items"] and receipt["status"] == "SUCCEEDED" and clock.seconds == 5
    assert len(provider.starts) == 1 and failures == provider.reads == [receipt["runId"]]
    diagnostic = effects.operation("capture")["result"]["diagnostic"]
    expected = {"stage": "provider_read", "error_type": error_type.__name__,
                "retryable": True, "attempt": 1}
    if error_type is ProviderStatusUnavailable:
        expected["reason"] = "mcp_error_envelope"
    assert diagnostic == expected
    assert effects.operation("capture")["state"] == "completed"


def test_persistent_status_failure_uses_finite_window_then_resumes_same_run(monkeypatch):
    provider, effects, collector, clock, call = polling_fixture(monkeypatch, finished_at=0)
    successful_read = provider.read
    reads = []

    async def read(run_id):
        reads.append(run_id)
        raise ProviderStatusUnavailable("synthetic failure")

    provider.read = read
    result, receipt = asyncio.run(collector.operation("capture", call))
    saved = effects.operation("capture")
    assert result is None and clock.seconds == call["callOptions"]["timeout"] + 15
    assert len(provider.starts) == 1 and set(reads) == {receipt["runId"]}
    assert saved["state"] == "running" and saved["receipt"] == receipt
    assert saved["result"]["diagnostic"]["attempt"] == len(reads)
    provider.read = successful_read
    result, same_receipt = asyncio.run(collector.operation("capture", call))
    assert result["items"] and same_receipt["runId"] == receipt["runId"]
    assert len(provider.starts) == 1 and result["diagnostic"] == saved["result"]["diagnostic"]


def test_unknown_social_read_failure_is_diagnosed_and_propagated_without_retry(monkeypatch):
    from tests.runtime.test_market_intelligence_research_collector import market_collect

    provider, effects, collector, clock, _ = polling_fixture(monkeypatch)
    reads = []

    async def read(run_id):
        reads.append(run_id)
        raise ValueError("private malformed provider data")

    provider.read = read
    with pytest.raises(ValueError, match="private malformed provider data"):
        market_collect(collector, source="instagram")
    assert len(provider.starts) == len(reads) == 1 and clock.seconds == 0
    saved = next(row for row in effects.read()[0]["operations"].values() if row["kind"] == "apify")
    assert saved["state"] == "running" and saved["receipt"]["runId"] == reads[0]
    assert saved["result"]["diagnostic"] == {
        "stage": "provider_read", "error_type": "ValueError", "retryable": False, "attempt": 1,
    }


def test_single_read_override_preserves_pending_diagnostic_without_sleep(monkeypatch):
    provider, effects, collector, clock, call = polling_fixture(monkeypatch, attempts=1)

    async def read(run_id):
        provider.reads.append(run_id)
        raise ProviderReceiptUnverified("cost not settled")

    provider.read = read
    result, receipt = asyncio.run(collector.operation("capture", call))
    assert result is None and clock.seconds == 0
    assert len(provider.starts) == len(provider.reads) == 1
    assert effects.operation("capture")["receipt"] == receipt
    assert effects.operation("capture")["result"]["diagnostic"]["retryable"] is True


def test_foreign_receipt_is_not_retried_or_saved_as_the_admitted_run(monkeypatch):
    provider, effects, collector, clock, call = polling_fixture(monkeypatch)

    async def read(run_id):
        provider.reads.append(run_id)
        return {"runId": "foreign-run", "status": "SUCCEEDED", "spent_microusd": 500}

    provider.read = read
    with pytest.raises(ValueError, match="another admitted run"):
        asyncio.run(collector.operation("capture", call))
    saved = effects.operation("capture")
    assert saved["receipt"]["runId"] != "foreign-run"
    assert len(provider.starts) == len(provider.reads) == 1 and clock.seconds == 0
    assert saved["result"]["diagnostic"]["retryable"] is False


def test_sdk_error_then_receipt_completes_same_capture_through_real_provider_decoder(monkeypatch):
    import json
    from tests.runtime.test_market_intelligence_status_unavailable import provider as mcp_provider

    provider, effects, collector, clock, call = polling_fixture(monkeypatch)
    apify, tools = mcp_provider(None)
    responses = ["MCP tool 'get-actor-run' invocation failed: private SDK response"]

    async def read(run_id):
        tools['get-actor-run'].result = responses.pop() if responses else {
            'runId': run_id, 'status': 'SUCCEEDED', 'datasetId': 'existing-dataset',
            'usageTotalUsd': 0.0005, 'finishedAt': '2026-01-01T00:00:00Z',
        }
        return await apify.read(run_id)

    provider.read = read
    result, receipt = asyncio.run(collector.operation('capture', call))
    assert result['items'] and receipt['status'] == 'SUCCEEDED' and clock.seconds == 5
    assert len(provider.starts) == 1 and len(tools['get-actor-run'].calls) == 2
    assert {row['runId'] for row in tools['get-actor-run'].calls} == {receipt['runId']}
    assert not tools['call-actor'].calls
    assert effects.operation('capture')['result']['diagnostic']['reason'] == 'sdk_status_invocation_failed'
    assert 'private SDK response' not in json.dumps(effects.read()[0])


@pytest.mark.parametrize('conflict', [False, True])
def test_invalid_provider_receipt_stays_fatal_and_preserves_structured_diagnostic(monkeypatch, conflict):
    from tests.runtime.test_market_intelligence_status_unavailable import provider as mcp_provider
    from ai_market_intelligence.runtime.runtime_core import ProviderResponseInvalid

    provider, effects, collector, clock, call = polling_fixture(monkeypatch)
    raw = ({'structuredContent': {'runId': 'receipt-1', 'status': 'RUNNING'}, '_meta': {}, 'meta': {'different': True}}
           if conflict else {'runId': 'foreign-run', 'status': 'RUNNING'})
    apify, tools = mcp_provider(raw)
    provider.read = apify.read
    with pytest.raises(ProviderResponseInvalid):
        asyncio.run(collector.operation('capture', call))
    saved = effects.operation('capture')
    assert saved['state'] == 'running' and saved['receipt']['runId'] == 'receipt-1'
    assert len(provider.starts) == len(tools['get-actor-run'].calls) == 1 and clock.seconds == 0
    assert saved['result']['diagnostic']['retryable'] is False
    assert saved['result']['diagnostic']['reason'] == ('conflicting_mcp_metadata' if conflict else 'unexpected_run_identity')
