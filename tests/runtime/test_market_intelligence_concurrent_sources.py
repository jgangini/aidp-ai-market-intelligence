"""Controlled source interleavings; no external tools or model calls."""
import asyncio
import copy

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from tests.runtime.test_market_intelligence_graph import market_fixture
from ai_market_intelligence.runtime.workflow import MarketWorkflow


async def _consume(graph, request_id, events, on_event=None):
    config = {"configurable": {"thread_id": request_id}}
    async for event in graph.astream(
        {"request_id": request_id, "question": "¿Qué reclamaciones aparecen en la muestra?"},
        config,
        stream_mode="custom",
    ):
        events.append(event)
        if on_event is not None:
            on_event(event)
    return (await graph.aget_state(config)).values


def _draft_for(event, source):
    return event["type"] == "draft.upsert" and source in event["source_keys"]


def test_two_source_decisions_overlap_and_keep_review_context_isolated():
    store, model, _, graph = market_fixture(("x", "instagram", "web"))
    ask = model.ask

    async def run():
        two_models = asyncio.Event()
        release = asyncio.Event()
        active = set()
        peak = 0
        events = []

        async def blocked(name, schema, payload):
            nonlocal peak
            if name == "market_analyze":
                source = payload["task"]["source"]
                active.add(source)
                peak = max(peak, len(active))
                assert len(active) <= 2
                if len(active) == 2:
                    two_models.set()
                try:
                    await release.wait()
                    return await ask(name, schema, payload)
                finally:
                    active.remove(source)
            if name == "market_verify" and len(payload["cited_evidence_ids"]) == 1:
                assert len({row["source"] for row in payload["evidence"]}) == 1
                assert len(payload["retrieved_inventory"]["by_source"]) == 1
            return await ask(name, schema, payload)

        model.ask = blocked
        consumer = asyncio.create_task(_consume(graph, "model-overlap", events))
        try:
            await asyncio.wait_for(two_models.wait(), 2)
            assert not consumer.done()
            release.set()
            result = await asyncio.wait_for(consumer, 2)
        finally:
            release.set()
            if not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
        assert peak == 2 and not active
        assert result["response"]["status"] == "complete"
        assert set(store.read()[0]["completed_tasks"]) == {"task-x", "task-web", "task-instagram"}
        assert sum(e["type"] == "run.completed" for e in events) == 1

    asyncio.run(run())


@pytest.mark.parametrize("entity", ["Tiendas D1", "ARA", "Marca independiente"])
def test_open_conversation_expands_enabled_sources_without_customer_special_cases(entity):
    sources = ("x", "instagram", "web", "facebook", "youtube", "market_data", "world_bank")
    _, model, collector, _ = market_fixture(sources)
    ask = model.ask
    async def select(name, schema, payload):
        result = await ask(name, schema, payload)
        if name == "market_understand":
            result.update(entity=entity, source_selection="open_conversation", sources=["x"])
        if name == "market_plan":
            result["tasks"] = [task for task in result["tasks"] if task["source"] in payload["brief"]["sources"]]
        return result
    model.ask = select
    from tests.runtime.test_market_intelligence_graph import market_run
    store, _, _, _ = market_fixture(sources)
    graph = MarketWorkflow(store, model, collector, enabled_sources=sources).build(InMemorySaver())
    _, state = market_run(graph)
    assert state["response"]["status"] == "complete"
    assert {call["task"]["source"] for call in collector.calls} == {"x", "instagram", "web", "facebook", "youtube"}


def test_targeted_conversation_keeps_the_users_single_source():
    store, model, collector, _ = market_fixture(("instagram",))
    graph = MarketWorkflow(store, model, collector,
                           enabled_sources=("x", "instagram", "web", "facebook", "youtube")).build(InMemorySaver())
    from tests.runtime.test_market_intelligence_graph import market_run
    _, state = market_run(graph)
    assert state["response"]["status"] == "complete"
    assert [call["task"]["source"] for call in collector.calls] == ["instagram"]


@pytest.mark.parametrize("slow,fast", [("x", "instagram"), ("instagram", "x")])
def test_fast_source_publishes_verified_capsule_before_slow_capture_finishes(slow, fast):
    store, model, collector, graph = market_fixture((slow, fast))
    collect = collector.collect
    ask = model.ask

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        fast_capsule = asyncio.Event()
        slow_finished = False
        verified = set()
        events = []

        async def capture(**arguments):
            nonlocal slow_finished
            if arguments["task"]["source"] == slow:
                entered.set()
                try:
                    await release.wait()
                finally:
                    slow_finished = True
            return await collect(**arguments)

        async def verify(name, schema, payload):
            result = await ask(name, schema, payload)
            if name == "market_verify" and result["accepted"]:
                verified.update(row["source"] for row in payload["evidence"])
            return result

        def observed(event):
            if _draft_for(event, fast):
                assert fast in verified
                assert not release.is_set() and not slow_finished
                fast_capsule.set()

        collector.collect = capture
        model.ask = verify
        consumer = asyncio.create_task(_consume(graph, "source-order", events, observed))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(fast_capsule.wait(), 2)
            assert not consumer.done()
            release.set()
            state = await asyncio.wait_for(consumer, 2)
        finally:
            release.set()
            if not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
        assert state["response"]["status"] == "complete"
        assert {row["source"] for row in state["response"]["references"]} == {slow, fast}
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert len(collector.calls) == 2
        assert {row["source"] for row in store.read()[0]["evidence"].values()} == {slow, fast}

    asyncio.run(run())


def test_four_sources_share_two_capture_slots_and_preserve_every_result():
    sources = ("x", "instagram", "web", "youtube")
    store, _, collector, graph = market_fixture(sources)
    collect = collector.collect

    async def run():
        two_entered = asyncio.Event()
        release = asyncio.Event()
        active = set()
        peak = 0
        starts = []
        events = []

        async def capture(**arguments):
            nonlocal peak
            source = arguments["task"]["source"]
            active.add(source)
            starts.append(source)
            peak = max(peak, len(active))
            assert len(active) <= 2
            if len(active) == 2:
                two_entered.set()
            try:
                await release.wait()
                await asyncio.sleep(0)
                return await collect(**arguments)
            finally:
                active.remove(source)

        collector.collect = capture
        consumer = asyncio.create_task(_consume(graph, "capture-limit", events))
        try:
            await asyncio.wait_for(two_entered.wait(), 2)
            assert len(starts) == 2 and len(active) == 2
            release.set()
            state = await asyncio.wait_for(consumer, 2)
        finally:
            release.set()
            if not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
        assert peak == 2 and not active
        assert sorted(starts) == sorted(sources)
        assert state["response"]["status"] == "complete"
        evidence = store.read()[0]["evidence"]
        assert {row["source"] for row in evidence.values()} == set(sources)
        assert {row["evidence_id"] for row in state["response"]["references"]} == set(evidence)

    asyncio.run(run())


def test_group_admission_starts_web_before_second_social_and_does_not_wait_for_x():
    _, _, collector, graph = market_fixture(("x", "instagram", "web"))
    collect = collector.collect

    async def run():
        release_x = asyncio.Event()
        both_capsules = asyncio.Event()
        starts = []
        capsules = set()
        events = []

        async def capture(**arguments):
            source = arguments["task"]["source"]
            starts.append(source)
            if source == "x":
                await release_x.wait()
            return await collect(**arguments)

        def observed(event):
            if event["type"] == "draft.upsert":
                capsules.update(event["source_keys"])
                if {"web", "instagram"} <= capsules:
                    both_capsules.set()

        collector.collect = capture
        consumer = asyncio.create_task(_consume(graph, "group-admission", events, observed))
        try:
            await asyncio.wait_for(both_capsules.wait(), 2)
            assert starts[:2] == ["x", "web"]
            assert not release_x.is_set()
            release_x.set()
            state = await asyncio.wait_for(consumer, 2)
        finally:
            release_x.set()
            if not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
        assert state["response"]["status"] == "complete"

    asyncio.run(run())


def test_document_priority_is_preserved_before_social_capture_admission():
    _, _, collector, graph = market_fixture(("x", "instagram", "web", "financial_document"))
    collect = collector.collect

    async def run():
        release = asyncio.Event()
        first_pair = asyncio.Event()
        starts = []
        events = []

        async def capture(**arguments):
            starts.append(arguments["task"]["source"])
            if len(starts) == 2:
                first_pair.set()
            await release.wait()
            return await collect(**arguments)

        collector.collect = capture
        consumer = asyncio.create_task(_consume(graph, "document-priority", events))
        try:
            await asyncio.wait_for(first_pair.wait(), 2)
            assert len(starts) == 2 and starts[0] == "financial_document"
            release.set()
            await asyncio.wait_for(consumer, 2)
        finally:
            release.set()
            if not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)

    asyncio.run(run())


def test_pending_capture_drains_admitted_source_then_replay_keeps_completed_capture():
    store, model, collector, graph = market_fixture(("x", "facebook", "instagram"))
    collect = collector.collect
    x_calls = []
    starts = []

    async def run():
        facebook_entered = asyncio.Event()
        pending_returned = asyncio.Event()
        release_facebook = asyncio.Event()
        first_events = []

        async def capture(**arguments):
            source = arguments["task"]["source"]
            starts.append(source)
            if source == "x":
                x_calls.append(copy.deepcopy(arguments))
                if len(x_calls) == 1:
                    await facebook_entered.wait()
                    pending_returned.set()
                    return {"status": "pending", "evidence": [], "limitations": [], "operation_id": "pending-x"}
            if source == "facebook":
                facebook_entered.set()
                await release_facebook.wait()
            return await collect(**arguments)

        collector.collect = capture
        consumer = asyncio.create_task(_consume(graph, "pending-replay", first_events))
        try:
            await asyncio.wait_for(pending_returned.wait(), 2)
            release_facebook.set()
            first_state = await asyncio.wait_for(consumer, 2)
        finally:
            release_facebook.set()
            if not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
        assert first_state["response"]["status"] == "pending"
        assert starts == ["x", "facebook"]
        assert any(_draft_for(event, "facebook") for event in first_events)
        assert {row["source"] for row in store.read()[0]["evidence"].values()} == {"facebook"}
        assert model.calls.count("market_analyze") == 1
        second_events = []
        second_state = await asyncio.wait_for(_consume(graph, "pending-replay", second_events), 2)
        assert second_state["response"]["status"] == "complete"
        assert starts.count("facebook") == 1 and starts.count("x") == 2 and starts.count("instagram") == 1
        assert x_calls[1]["previous_operation_id"] == "pending-x"
        assert x_calls[0]["operation_key"] == x_calls[1]["operation_key"]
        assert model.calls.count("market_analyze") == 3
        assert {row["source"] for row in second_state["response"]["references"]} == {"x", "facebook", "instagram"}

    asyncio.run(run())


def test_unavailable_source_preserves_other_verified_results_and_reports_partial():
    store, _, collector, graph = market_fixture(("x", "instagram"))
    collect = collector.collect

    async def run():
        starts = []
        events = []

        async def capture(**arguments):
            source = arguments["task"]["source"]
            starts.append(source)
            if source == "instagram":
                return {
                    "status": "unavailable", "evidence": [],
                    "limitations": ["La fuente no estuvo disponible en esta consulta."],
                    "operation_id": "unavailable-instagram",
                }
            return await collect(**arguments)

        collector.collect = capture
        state = await asyncio.wait_for(_consume(graph, "unavailable-source", events), 2)
        assert state["response"]["status"] == "partial"
        assert starts.count("x") == starts.count("instagram") == 1
        assert {row["source"] for row in state["response"]["references"]} == {"x"}
        assert any(_draft_for(event, "x") for event in events)
        assert not any(_draft_for(event, "instagram") for event in events)
        assert {row["source"] for row in store.read()[0]["evidence"].values()} == {"x"}
        assert any("no estuvo disponible" in text for text in state["response"]["limitations"])

    asyncio.run(run())


def test_cancelling_the_consumer_drains_capture_tasks_before_stream_close_returns():
    _, _, collector, graph = market_fixture(("x", "instagram", "web"))

    async def run():
        two_entered = asyncio.Event()
        never_release = asyncio.Event()
        active = set()
        ended = set()
        starts = []
        events = []

        async def capture(**arguments):
            source = arguments["task"]["source"]
            starts.append(source)
            active.add(source)
            if len(active) == 2:
                two_entered.set()
            try:
                await never_release.wait()
                raise AssertionError("Blocked capture must be cancelled")
            finally:
                active.remove(source)
                ended.add(source)

        collector.collect = capture
        consumer = asyncio.create_task(_consume(graph, "consumer-close", events))
        try:
            await asyncio.wait_for(two_entered.wait(), 2)
            consumer.cancel()
            result = await asyncio.wait_for(asyncio.gather(consumer, return_exceptions=True), 2)
            assert isinstance(result[0], asyncio.CancelledError)
            assert not active and ended == set(starts) and len(starts) == 2
        finally:
            never_release.set()
            if not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)

    asyncio.run(run())


def test_unexpected_capture_error_drains_its_admitted_sibling_and_preserves_error():
    store, model, collector, _ = market_fixture(("x", "instagram"))
    workflow = MarketWorkflow(store, model, collector, enabled_sources=("x", "instagram"))
    graph = workflow.build(InMemorySaver())

    async def run():
        sibling_entered = asyncio.Event()
        sibling_closed = asyncio.Event()
        async def capture(**arguments):
            if arguments["task"]["source"] == "x":
                await sibling_entered.wait()
                raise RuntimeError("controlled provider failure")
            sibling_entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                sibling_closed.set()
        collector.collect = capture
        with pytest.raises(RuntimeError, match="controlled provider failure"):
            await _consume(graph, "unexpected-error", [])
        assert sibling_closed.is_set() and not workflow._capture_tasks
    asyncio.run(run())


def test_pending_after_refinement_reservation_preserves_query_owner_for_replay():
    store, model, collector, _ = market_fixture(("x", "instagram"))
    workflow = MarketWorkflow(store, model, collector, enabled_sources=("x", "instagram"))
    graph = workflow.build(InMemorySaver())
    collect = collector.collect
    progress_task = store.progress_task
    capture_collection = workflow.capture_collection
    model.refine = True

    async def run():
        refinement_saved = asyncio.Event()
        instagram_pending = asyncio.Event()
        calls = []
        def record_save(task, **updates):
            progress_task(task, **updates)
            if updates.get("query"):
                refinement_saved.set()
        async def capture(**arguments):
            calls.append(copy.deepcopy(arguments))
            if arguments["task"]["source"] == "instagram" and not instagram_pending.is_set():
                await refinement_saved.wait()
                instagram_pending.set()
                return {"status": "pending", "evidence": [], "limitations": [], "operation_id": "pending-instagram"}
            return await collect(**arguments)
        async def interleave(value, task, discovery_key, operation_key):
            if task["source"] == "x" and "contraste" in task["query"]:
                await instagram_pending.wait()
            return await capture_collection(value, task, discovery_key, operation_key)
        store.progress_task = record_save
        collector.collect = capture
        workflow.capture_collection = interleave
        first = await asyncio.wait_for(_consume(graph, "refinement-pending", []), 2)
        value = store.read()[0]
        assert first["response"]["status"] == "pending"
        assert value["task_progress"]["task-x"]["query"].endswith("contraste")
        assert sum("contraste" in c["task"]["query"] for c in calls) == 0
        model.refine = False
        replay = await asyncio.wait_for(_consume(graph, "refinement-pending", []), 2)
        assert replay["response"]["status"] == "complete"
        ig_calls = [c for c in calls if c["task"]["source"] == "instagram"]
        assert len(ig_calls) == 2
        assert ig_calls[0]["task"]["query"] == ig_calls[1]["task"]["query"] == "Fixture brand reclamaciones"
        assert ig_calls[0]["operation_key"] == ig_calls[1]["operation_key"]
        assert ig_calls[1]["previous_operation_id"] == "pending-instagram"
        assert sum("contraste" in c["task"]["query"] for c in calls) == 1
        assert not workflow._capture_tasks
    asyncio.run(run())


def test_background_document_selector_keeps_its_own_query_during_another_refinement():
    store, model, collector, _ = market_fixture(("x", "financial_document"))
    workflow = MarketWorkflow(store, model, collector, enabled_sources=("x", "financial_document"))
    task = {"task_id": "document", "source": "financial_document", "role": "financial_analysis",
            "query": "document own query", "urls": [], "purpose": "Read financial figures"}
    store.begin("selector-query", "Controlled question")
    store.save(plan={"tasks": [task]}, active_query="another source refinement")
    async def ask(name, schema, payload):
        assert name == "market_select_passages"
        assert payload["task"]["query"] == "document own query"
        return {"candidate_ids": ["passage-1"]}
    model.ask = ask
    async def run():
        token = workflow._capture_context.set(task)
        try:
            selected = await workflow.select_passages({"source": "financial_document"},
                [{"candidate_id": "passage-1", "preview": "A controlled financial passage"}])
            assert selected == ["passage-1"]
        finally:
            workflow._capture_context.reset(token)
    asyncio.run(run())
