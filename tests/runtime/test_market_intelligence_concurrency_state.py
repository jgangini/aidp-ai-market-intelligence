"""Concurrent collection commits and concurrent model effects; no external services."""

import asyncio
import copy
from types import SimpleNamespace

import pytest

from ai_market_intelligence.runtime.effects import MarketEffectPending, MarketEffects, MarketRecordedModel
from ai_market_intelligence.runtime.state import MarketRunStore
from tests.runtime.test_market_intelligence_graph import MarketSnapshot, market_evidence


def collection(source, *, status="complete", evidence=True):
    return {"status": status, "evidence": [market_evidence(source)] if evidence else [],
            "limitations": [], "operation_id": "operation-" + source}


def request_store(snapshot=None):
    store = MarketRunStore(snapshot or MarketSnapshot(), "fixture-concurrent")
    store.begin("fixture-request", "Controlled fixture question")
    return store


def test_collection_merge_retries_against_fresh_state_without_losing_another_source():
    class InterleavedSnapshot(MarketSnapshot):
        before_write = None

        def write(self, value, etag):
            callback, self.before_write = self.before_write, None
            if callback is not None:
                callback()
            super().write(value, etag)

    snapshot = InterleavedSnapshot()
    first = request_store(snapshot)
    second = MarketRunStore(snapshot, "fixture-concurrent")
    snapshot.before_write = lambda: second.merge_collection("instagram", collection("instagram"))
    first.merge_collection("x", collection("x"))
    value, _ = first.read()
    assert set(value["collections"]) == {"x", "instagram"}
    assert set(value["evidence"]) == {"evidence-x-1", "evidence-instagram-1"}
    assert value["state_revision"] == 2


def test_collection_replay_is_identical_and_keeps_input_detached_from_the_store():
    store = request_store()
    incoming = collection("x")
    expected = copy.deepcopy(incoming)
    store.merge_collection("x", incoming)
    store.merge_collection("x", expected)
    incoming["evidence"][0]["text"] = "Changed after commit"
    value, _ = store.read()
    assert value["collections"] == {"x": expected}
    assert value["evidence"] == {"evidence-x-1": expected["evidence"][0]}


def test_changed_existing_evidence_rejects_the_entire_collection_commit():
    store = request_store()
    store.merge_collection("x", collection("x"))
    before, before_etag = store.read()
    incoming = collection("instagram")
    incoming["evidence"].append({**market_evidence("x"), "text": "Conflicting content"})
    with pytest.raises(ValueError, match="existing evidence identity changed"):
        store.merge_collection("instagram", incoming)
    after, after_etag = store.read()
    assert after == before and after_etag == before_etag


def test_pending_collection_can_complete_but_terminal_content_is_immutable():
    store = request_store()
    store.merge_collection("x", collection("x", status="pending", evidence=False))
    final = collection("x")
    store.merge_collection("x", final)
    changed = {**final, "limitations": ["Changed terminal result"]}
    before, etag = store.read()
    with pytest.raises(ValueError, match="terminal collection cannot change"):
        store.merge_collection("x", changed)
    assert store.read() == (before, etag)


def test_completed_task_ids_can_be_persisted_without_changing_the_selected_index():
    store = request_store()
    assert store.read()[0]["completed_tasks"] == []
    store.save(task_index=2, completed_tasks=["task-instagram"])
    value, _ = store.read()
    assert value["task_index"] == 2 and value["completed_tasks"] == ["task-instagram"]


def recorded_model(ask):
    effects = MarketEffects(MarketSnapshot(), "fixture-concurrent", "fixture-request")
    return MarketRecordedModel(SimpleNamespace(ask=ask), effects), effects


def test_thread_local_model_clients_keep_schemas_separate_and_close_with_executor(monkeypatch):
    import hashlib
    import json
    from threading import Barrier
    import oci
    from langchain_core.messages import SystemMessage, HumanMessage
    from ai_market_intelligence.runtime.model_bridge import StructuredBridge

    identity = {"tenancy": "fixture", "user": "fixture", "fingerprint": "fixture"}
    monkeypatch.setattr(oci.config, "from_file", lambda *a: identity)
    barrier = Barrier(2, timeout=3)
    clients, closed = [], []
    class Client:
        def __init__(self, *args, **kwargs):
            clients.append(self)
            self.base_client = SimpleNamespace(session=SimpleNamespace(close=lambda: closed.append(self)))
        def chat(self, details, **kwargs):
            barrier.wait()
            schema = details.chat_request.response_format.json_schema.schema
            return SimpleNamespace(data=SimpleNamespace(chat_response=json.dumps(schema)), headers={})
    monkeypatch.setattr(oci.generative_ai_inference, "GenerativeAiInferenceClient", Client)
    tracked = SimpleNamespace(client=None, config_file="fixture", profile="DEFAULT", calls=0,
        settings={"region": "fixture", "model_id": "fixture", "compartment_id": "fixture",
                  "max_tokens": 100, "reasoning_effort": "LOW"},
        _complete=None, _final_text=lambda value: value, close=lambda: closed.append("owner"))
    async def complete(system, question):
        tracked.calls += 1
        return await asyncio.to_thread(tracked._complete, system, question)
    tracked.complete = complete
    bridge = StructuredBridge(tracked, {"identity_sha256": hashlib.sha256(json.dumps(
        ["fixture"] * 3, separators=(",", ":")).encode()).hexdigest(),
        "guardrails_config": {"policies": []}, "model_endpoint": "https://example.invalid"})
    async def run():
        return await asyncio.gather(*[
            bridge.with_structured_output({"title": source, "type": "object"}, "json_schema").ainvoke(
                [SystemMessage(content="fixture"), HumanMessage(content=source)])
            for source in ("x", "web")])
    results = asyncio.run(run())
    assert [row["title"] for row in results] == ["x", "web"]
    assert tracked.calls == 2 and len(clients) == 2 and not closed
    tracked.close()
    assert closed[-1] == "owner" and set(closed[:-1]) == set(clients)


def test_live_submission_from_another_recorder_still_blocks_a_new_request():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def ask(*args):
            entered.set()
            await release.wait()
            return {"ok": True}
        model, effects = recorded_model(ask)
        other = MarketRecordedModel(model.model, effects)
        active = asyncio.create_task(model.ask("first", {}, {}))
        await entered.wait()
        try:
            with pytest.raises(MarketEffectPending):
                await other.ask("another-owner", {}, {})
        finally:
            release.set()
            await active
        assert len(effects.read()[0]["operations"]) == 1
    asyncio.run(run())


def test_repeated_cancellation_drains_and_records_an_admitted_model_call():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def ask(*args):
            entered.set()
            await release.wait()
            return {"ok": True}
        model, effects = recorded_model(ask)
        active = asyncio.create_task(model.ask("first", {}, {}))
        await entered.wait()
        active.cancel()
        await asyncio.sleep(0)
        active.cancel()
        await asyncio.sleep(0)
        assert not active.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await active
        assert not model._active_claims
        assert all(row["state"] == "completed" for row in effects.read()[0]["operations"].values())
    asyncio.run(run())


def test_distinct_live_model_calls_overlap_without_an_unresolved_effect_error():
    async def scenario():
        entered, release, second_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []
        active = peak = 0

        async def ask(name, schema, payload):
            nonlocal active, peak
            calls.append(name)
            active += 1
            peak = max(peak, active)
            try:
                if name == "first":
                    entered.set()
                    await release.wait()
                else:
                    second_entered.set()
                return {"name": name}
            finally:
                active -= 1

        model, effects = recorded_model(ask)
        first = asyncio.create_task(model.ask("first", {}, {}))
        await entered.wait()
        second = asyncio.create_task(model.ask("second", {}, {}))
        await asyncio.wait_for(second_entered.wait(), 2)
        assert calls == ["first", "second"] and len(effects.read()[0]["operations"]) == 2
        release.set()
        assert await asyncio.gather(first, second) == [{"name": "first"}, {"name": "second"}]
        assert calls == ["first", "second"] and peak == 2
        assert all(row["state"] == "completed" for row in effects.read()[0]["operations"].values())

    asyncio.run(scenario())


def test_duplicate_concurrent_model_requests_use_the_one_committed_reply():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def ask(*args):
            calls.append(args)
            entered.set()
            await release.wait()
            return {"reply": "stored"}

        model, effects = recorded_model(ask)
        first = asyncio.create_task(model.ask("same", {}, {"question": "fixture"}))
        await entered.wait()
        second = asyncio.create_task(model.ask("same", {}, {"question": "fixture"}))
        await asyncio.sleep(0)
        release.set()
        assert await asyncio.gather(first, second) == [{"reply": "stored"}] * 2
        assert len(calls) == len(effects.read()[0]["operations"]) == 1

    asyncio.run(scenario())


def test_cancelled_model_waiter_never_reserves_or_starts_an_effect():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def ask(name, schema, payload):
            calls.append(name)
            entered.set()
            await release.wait()
            return {"name": name}

        model, effects = recorded_model(ask)
        first = asyncio.create_task(model.ask("first", {}, {}))
        await entered.wait()
        waiting = asyncio.create_task(model.ask("first", {}, {}))
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert calls == ["first"] and len(effects.read()[0]["operations"]) == 1
        release.set()
        assert await first == {"name": "first"}

    asyncio.run(scenario())


def test_cancelled_model_consumer_keeps_the_lock_until_its_admitted_reply_is_recorded():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def ask(name, schema, payload):
            calls.append(name)
            entered.set()
            await release.wait()
            return {"name": name}

        model, effects = recorded_model(ask)
        first = asyncio.create_task(model.ask("first", {}, {}))
        await entered.wait()
        first.cancel()
        waiting = asyncio.create_task(model.ask("first", {}, {}))
        await asyncio.sleep(0)
        assert not first.done() and calls == ["first"]
        assert len(effects.read()[0]["operations"]) == 1
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert await model.ask("first", {}, {}) == {"name": "first"}
        assert calls == ["first"]

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError, asyncio.CancelledError])
def test_uncertain_model_reply_keeps_later_queued_calls_blocked(failure):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def ask(name, schema, payload):
            calls.append(name)
            entered.set()
            await release.wait()
            raise failure()

        model, effects = recorded_model(ask)
        first = asyncio.create_task(model.ask("uncertain", {}, {}))
        await entered.wait()
        release.set()
        with pytest.raises(failure):
            await first
        with pytest.raises(MarketEffectPending):
            await model.ask("later", {}, {})
        assert calls == ["uncertain"]
        assert [row["state"] for row in effects.read()[0]["operations"].values()] == ["uncertain"]

    asyncio.run(scenario())
