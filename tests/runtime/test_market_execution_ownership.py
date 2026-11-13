"""Transport interruption preserves one root owner until admitted work is drained."""
import asyncio
import json
import sys

import pytest

from ai_market_intelligence.runtime.runtime_core import Conflict
from ai_market_intelligence.runtime.state import MarketRunStore, MarketExecutionBusy
from tests.runtime.test_market_intelligence_graph import MarketSnapshot
from tests.runtime.test_market_intelligence_research_runtime import (
    market_active_config, market_native_fixture, market_native_turn,
)


def test_request_ownership_is_conditional_and_never_expires_or_releases_another_owner():
    store = MarketRunStore(MarketSnapshot(), "candidate")
    store.begin("one", "Question")
    store.acquire_execution("first")
    with pytest.raises(MarketExecutionBusy):
        store.acquire_execution("second")
    store.interrupt_execution("first")
    assert store.execution_info()["owner_held"]
    with pytest.raises(Conflict):
        store.release_execution("second", "interrupted")
    store.release_execution("first", "interrupted")
    store.acquire_execution("second")
    with pytest.raises(Conflict):
        store.release_execution("first", "interrupted")
    assert store.execution_info()["owner_held"]


def test_historical_running_without_execution_ownership_is_unknown_not_adopted():
    snapshot = MarketSnapshot()
    store = MarketRunStore(snapshot, "candidate")
    store.begin("one", "Question")
    snapshot.value.pop("execution")
    assert store.execution_info() == {"schema_version": "1.0", "state": "unknown",
                                     "owner_held": False, "continuation_safe": False}
    with pytest.raises(MarketExecutionBusy):
        store.acquire_execution("new")


async def packets(runtime, command):
    messages = await runtime.invoke(json.dumps(command), subject_id="fixture-user",
                                    thread_id="fixture-session", trace_id="fixture-trace", stream=True)
    return [json.loads(message.content) async for message in messages]


def request_value(storage):
    return next(json.loads(raw) for key, (raw, _) in storage.objects.items() if key.endswith("/request.json"))


@pytest.mark.parametrize("uncertain", [False, True])
def test_cancel_drains_model_under_fence_then_replays_and_resumes_without_duplicate_capture(tmp_path, monkeypatch, uncertain):
    config = market_active_config()
    config["services"]["enabled_sources"] = ["facebook"]
    runtime, storage, model, provider, _ = market_native_fixture(tmp_path, monkeypatch, config=config)
    model.sources = ("facebook",)
    ask = model.ask
    command = {"request_id": "one", "question": "Question"}

    async def run():
        entered, finish, interrupted = asyncio.Event(), asyncio.Event(), asyncio.Event()
        store_type = sys.modules["core.state"].MarketRunStore
        mark = store_type.interrupt_execution

        def record(store, owner):
            mark(store, owner)
            interrupted.set()

        monkeypatch.setattr(store_type, "interrupt_execution", record)

        async def blocked(name, schema, payload):
            result = await ask(name, schema, payload)
            if name == "market_analyze":
                entered.set()
                await finish.wait()
                if uncertain:
                    raise ConnectionError("Synthetic unknown provider outcome")
            return result

        model.ask = blocked
        active = asyncio.create_task(packets(runtime, command))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            captures = list(provider.starts)
            assert captures
            before_calls = list(model.calls)
            duplicate = await packets(runtime, command)
            assert duplicate[-1]["response"]["continuation_safe"] is False
            assert duplicate[-1]["execution"]["owner_held"] is True
            assert model.calls == before_calls and provider.starts == captures
    
            active.cancel()
            await asyncio.wait_for(interrupted.wait(), 5)
            assert request_value(storage)["status"] == "interrupted"
            active.cancel()
            await asyncio.sleep(0)
            assert request_value(storage)["execution"]["owner"] is not None
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(active, 5)
            value = request_value(storage)
            assert value["execution"] == {"state": "interrupted", "owner": None}
            assert value["response"] is None
            replay = (await packets(runtime, {"action": "replay", "request_id": "one", "after_sequence": value["next_sequence"]-1}))[-1]
            assert replay["replay"]["snapshot"] is None
            assert replay["response"] is None
            assert replay["execution"] == {"schema_version": "1.0", "state": "interrupted",
                                            "owner_held": False, "continuation_safe": not uncertain}
            resumed = await packets(runtime, command)
            if uncertain:
                assert resumed[-1]["response"]["status"] == "pending"
                assert resumed[-1]["response"]["continuation_safe"] is False
                assert model.calls == before_calls
            else:
                assert resumed[-1]["response"]["status"] in {"complete", "partial"}
                assert model.calls.count("market_analyze") == 1
            assert provider.starts == captures
        finally:
            finish.set()
            if not active.done():
                active.cancel()
                await asyncio.gather(active, return_exceptions=True)

    asyncio.run(run())


def test_aclose_marks_interruption_without_committing_user_cancellation(tmp_path, monkeypatch):
    runtime, storage, _, _, _ = market_native_fixture(tmp_path, monkeypatch)

    async def run():
        messages = await runtime.invoke(json.dumps({"request_id": "one", "question": "Question"}),
            subject_id="fixture-user", thread_id="fixture-session", trace_id="fixture-trace", stream=True)
        async for message in messages:
            if json.loads(message.content)["kind"] == "event":
                await messages.aclose()
                break

    asyncio.run(run())
    value = request_value(storage)
    assert value["status"] == "interrupted" and not value["cancelled"]
    assert value["execution"] == {"state": "interrupted", "owner": None}


def test_effects_are_rechecked_under_the_acquired_owner_before_building_graph(tmp_path, monkeypatch):
    runtime, _, model, provider, _ = market_native_fixture(tmp_path, monkeypatch)
    store_type = sys.modules["core.state"].MarketRunStore
    acquire = store_type.acquire_execution
    built = []
    build = runtime.build_research_graph

    def claim_then_change_effects(store, owner):
        acquire(store, owner)
        value, _ = store.read()
        request = {"revision": store.revision, "request_id": value["request_id"],
                   "session_binding": value["session_binding"]}
        effects = runtime.request_effects(store.scope, request)
        admitted = effects.claim("unresolved", "model", {"fixture": "interleaved outcome"})
        effects.update("unresolved", admitted["claim"], state="uncertain")

    def observe_build(*args):
        built.append(True)
        return build(*args)

    monkeypatch.setattr(store_type, "acquire_execution", claim_then_change_effects)
    monkeypatch.setattr(runtime, "build_research_graph", observe_build)
    result = market_native_turn(runtime)
    assert result[-1]["response"]["continuation_safe"] is False
    assert not built and not model.calls and not provider.starts


def test_close_after_terminal_event_finishes_session_and_cached_retry_starts_no_effects(tmp_path, monkeypatch):
    runtime, storage, model, provider, _ = market_native_fixture(tmp_path, monkeypatch)

    async def run():
        messages = await runtime.invoke(json.dumps({"request_id": "one", "question": "Question"}),
            subject_id="fixture-user", thread_id="fixture-session", trace_id="fixture-trace", stream=True)
        async for message in messages:
            packet = json.loads(message.content)
            if packet["kind"] == "event" and packet["event"]["type"] == "run.completed":
                await messages.aclose()
                return
        raise AssertionError("Terminal event missing")

    asyncio.run(run())
    value = request_value(storage)
    assert value["response"] and value["execution"] == {"state": "completed", "owner": None}
    session = next(json.loads(raw) for key, (raw, _) in storage.objects.items() if "/sessions/" in key)
    assert session["active"] is None and session["requests"]["one"]["finished"]
    before = (list(model.calls), list(provider.starts))
    result = market_native_turn(runtime, request_id="one", question="Question")
    assert result[-1]["response"]["status"] in {"complete", "partial"}
    assert (model.calls, provider.starts) == before
    assert market_native_turn(runtime, request_id="two", question="Another question")[-1]["response"]["status"] in {"complete", "partial"}


@pytest.mark.parametrize("summary", [
    {"schema_version": "1.0", "state": "running", "owner_held": True, "continuation_safe": True},
    {"schema_version": "1.0", "state": "interrupted", "owner_held": False, "continuation_safe": "true"},
    {"schema_version": "1.0", "state": "unknown", "owner_held": False, "continuation_safe": True},
    {"schema_version": "1.0", "state": "interrupted", "owner_held": False, "continuation_safe": True, "owner": "private"},
])
def test_public_execution_summary_rejects_unsafe_or_private_fields(summary):
    from ai_market_intelligence.runtime.wire import market_wire_line
    with pytest.raises(ValueError):
        market_wire_line("snapshot", request_id="one", execution=summary)


@pytest.mark.parametrize("failure", ["notification", "cleanup"])
def test_failed_interruption_persistence_or_cleanup_never_releases_ownership(failure):
    from ai_market_intelligence.runtime.wire import market_stream_graph

    async def run():
        closed, released = asyncio.Event(), []

        class Graph:
            async def astream(self, *args, **kwargs):
                try:
                    await asyncio.Event().wait()
                    yield None
                finally:
                    closed.set()
                    if failure == "cleanup":
                        raise OSError("Synthetic failed graph cleanup")

        def mark():
            if failure == "notification":
                raise OSError("Synthetic failed interruption write")

        stream = market_stream_graph(Graph(), None, {"request_id": "one"}, {},
            heartbeat_seconds=0.001, on_interrupt=mark, on_closed=released.append)
        assert json.loads(await stream.__anext__())["kind"] == "heartbeat"
        with pytest.raises(OSError):
            await stream.aclose()
        assert closed.is_set() and not released

    asyncio.run(run())
