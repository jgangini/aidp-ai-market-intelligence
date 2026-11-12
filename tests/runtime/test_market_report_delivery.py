"""Local native-adapter integration of task report delivery; no remote services."""

import copy
import asyncio
import json
from types import SimpleNamespace

import pytest

from ai_market_intelligence.runtime.native import market_chat_input
from ai_market_intelligence.runtime.reporting import validate_delivery
from tests.runtime.test_market_intelligence_research_runtime import market_native_fixture, market_native_turn
from tests.runtime.test_market_intelligence_research_followup import followup_model


DELIVERY = {"mode": "report", "task_id": "task-1", "task_run_id": "task-run-1"}


def command(request="request-1", delivery=None, question="Qué se dice de la empresa en Colombia"):
    return {"request_id": request, "question": question,
            "delivery": copy.deepcopy(DELIVERY if delivery is None else delivery)}


def report_model(model, *, reject=False):
    previous = model.ask

    async def ask(name, schema, payload):
        if name == "market_report_sentiment":
            model.calls.append(name)
            return {"annotations": [{"evidence_id": row["evidence_id"], "target": payload["target"],
                "aspect": None, "label": "positive" if reject else "unclassified", "supporting_text": row["text"],
                "limitations": [] if reject else ["La ficha contiene recuentos, no una opinión inequívoca hacia el objetivo."]}
                for row in payload["evidence"]]}
        if name == "market_verify_report_sentiment":
            model.calls.append(name)
            evidence = {row["evidence_id"]: row for row in payload["evidence"]}
            return {"reviews": [{"unit_id": unit["unit_id"], "accepted": not reject,
                "issues": [{"claim": "La polaridad no está respaldada.",
                            "evidence_id": unit["annotation"]["evidence_id"],
                            "quoted_text": evidence[unit["annotation"]["evidence_id"]]["text"],
                            "reason": "Rechazo controlado de la etiqueta."}] if reject else []}
                for unit in payload["units"]]}
        return await previous(name, schema, payload)

    model.ask = ask


def test_native_report_is_canonical_persisted_and_replayed_without_effects(tmp_path, monkeypatch):
    runtime, _, model, provider, _ = market_native_fixture(tmp_path, monkeypatch)
    report_model(model)
    packets = market_native_turn(runtime, command=command())
    response = packets[-1]["response"]
    assert response["status"] == "complete"
    report = response["report"]
    assert (report["task_id"], report["task_run_id"], report["schema_version"]) == ("task-1", "task-run-1", "1.0")
    assert report["period"]["bounds"] == "[start,end]"
    assert report["period"]["end"] == "2026-11-02T23:59:59+00:00"
    assert "**Estado:**" not in response["markdown"]
    assert report["extensions"]["market.sentiment"]["method"]["calibration_status"] == "not_calibrated"
    references = {row["evidence_id"] for row in response["references"]}
    assert all(set(section["evidence_ids"]) <= references for section in report["sections"])
    assert "findings" in {section["section_id"] for section in report["sections"]}
    before = len(model.calls), len(provider.starts)
    replay = market_native_turn(runtime, command={"action": "replay", "request_id": "request-1"})[-1]
    assert replay["kind"] == "snapshot" and replay["response"] == response
    assert (len(model.calls), len(provider.starts)) == before
    again = market_native_turn(runtime, command=command())[-1]
    assert again["response"] == response and (len(model.calls), len(provider.starts)) == before


def test_chat_without_delivery_keeps_existing_contract_and_has_no_sentiment_calls(tmp_path, monkeypatch):
    runtime, _, model, _, _ = market_native_fixture(tmp_path, monkeypatch)
    response = market_native_turn(runtime)[-1]["response"]
    assert "report" not in response
    assert not any("sentiment" in call for call in model.calls)


def test_report_rejects_changed_delivery_or_duplicate_task_run_before_effects(tmp_path, monkeypatch):
    runtime, _, model, provider, _ = market_native_fixture(tmp_path, monkeypatch)
    report_model(model)
    market_native_turn(runtime, command=command())
    before = len(model.calls), len(provider.starts)
    changed = {**DELIVERY, "task_run_id": "different"}
    with pytest.raises(ValueError, match="request ID"):
        market_native_turn(runtime, command=command(delivery=changed))
    with pytest.raises(ValueError, match="task execution"):
        market_native_turn(runtime, command=command("other-request"))
    assert (len(model.calls), len(provider.starts)) == before


def test_report_transformation_keeps_original_period_and_starts_no_new_capture(tmp_path, monkeypatch):
    runtime, _, model, provider, _ = market_native_fixture(tmp_path, monkeypatch)
    report_model(model)
    first = market_native_turn(runtime, command=command())[-1]["response"]
    starts = len(provider.starts)
    original_sentiment = model.calls.count("market_report_sentiment")
    followup_model(model)
    delivery = {**DELIVERY, "task_run_id": "summary-run", "template": first["report"]["template"]}
    response = market_native_turn(runtime, command=command("summary", delivery, "Resume el informe anterior."))[-1]["response"]
    assert len(provider.starts) == starts
    assert model.calls.count("market_report_sentiment") == original_sentiment
    assert response["report"]["source_request_id"] == "request-1"
    assert response["report"]["period"] == first["report"]["period"]
    assert response["report"]["template"] == first["report"]["template"]
    assert response["coverage"]["no_new_capture"] is True
    assert "Investigación de origen" not in response["markdown"]
    assert "Se reutiliza el informe con sus fechas originales; no se realizaron nuevas capturas." in response["limitations"]


def test_rejected_sentiment_is_unclassified_and_never_published_as_positive(tmp_path, monkeypatch):
    runtime, _, model, _, _ = market_native_fixture(tmp_path, monkeypatch)
    report_model(model, reject=True)
    response = market_native_turn(runtime, command=command())[-1]["response"]
    sentiment = response["report"]["extensions"]["market.sentiment"]
    assert sentiment["status"] == "insufficient"
    assert all(group["classified_count"] == 0 and group["counts"]["positive"] == 0 for group in sentiment["groups"])
    assert all(annotation["label"] == "unclassified" for group in sentiment["groups"] for annotation in group["annotations"])


def test_empty_first_execution_keeps_template_for_later_evidence(tmp_path, monkeypatch):
    runtime, _, model, provider, _ = market_native_fixture(tmp_path, monkeypatch)
    report_model(model)
    observations = provider.observations

    async def empty(receipt, call, **kwargs):
        return SimpleNamespace(items=[], coverage=None, cost=receipt["spent_microusd"])

    provider.observations = empty
    first = market_native_turn(runtime, command=command())[-1]["response"]
    assert first["status"] == "partial" and first["references"] == []
    assert first["report"]["extensions"]["market.sentiment"]["status"] == "insufficient"
    assert not any("sentiment" in name for name in model.calls)
    provider.observations = observations
    delivery = {**DELIVERY, "task_run_id": "later-run", "template": first["report"]["template"]}
    later = market_native_turn(runtime, command=command("later", delivery))[-1]["response"]
    assert later["status"] == "complete" and later["references"]
    assert later["report"]["template"] == first["report"]["template"]
    assert "findings" in {section["section_id"] for section in later["report"]["sections"]}


def test_foreign_session_cannot_transform_the_saved_report(tmp_path, monkeypatch):
    runtime, _, model, provider, _ = market_native_fixture(tmp_path, monkeypatch)
    report_model(model)
    market_native_turn(runtime, command=command())
    before = len(provider.starts)
    followup_model(model)
    response = market_native_turn(runtime, session="foreign-session", command=command("summary", {**DELIVERY, "task_run_id": "summary"}, "Resume el informe anterior."))[-1]["response"]
    assert response["status"] == "needs_clarification" and "report" not in response
    assert len(provider.starts) == before


def test_delivery_input_is_exact_bounded_and_does_not_require_a_model():
    value = command()
    assert market_chat_input(json.dumps(value), "session", "trace") == value
    for invalid in ({**value, "extra": True}, command(delivery={**DELIVERY, "mode": "chat"}),
                    command(delivery={**DELIVERY, "task_id": " "})):
        with pytest.raises(ValueError):
            market_chat_input(json.dumps(invalid), "session", "trace")
    with pytest.raises(ValueError):
        market_chat_input(json.dumps(command(question="a" * 65536)), "session", "trace")
    with pytest.raises(ValueError):
        validate_delivery({**DELIVERY, "schema_version": "1.0"})


def test_explicit_template_is_not_limited_to_legacy_chat_size():
    from ai_market_intelligence.runtime.reporting import build_report_response
    from tests.runtime.test_market_report_builder import inputs

    value, response, delivery = inputs()
    template = build_report_response(value, response, delivery)["report"]["template"]
    for section in template["sections"]:
        section["title"] = "界" * 500
    request = command(delivery={**DELIVERY, "template": template}, question="a" * 500)
    raw = json.dumps(request, ensure_ascii=False)
    assert len(raw) > 4096 and len(raw.encode("utf-8")) < 65536
    assert market_chat_input(raw, "session", "trace")["delivery"]["template"] == template


def test_long_followup_report_does_not_overflow_completion_event(tmp_path, monkeypatch):
    runtime, _, model, _, _ = market_native_fixture(tmp_path, monkeypatch)
    report_model(model)
    first = market_native_turn(runtime, command=command())[-1]["response"]
    followup_model(model)
    previous = model.ask

    async def long_summary(name, schema, payload):
        result = await previous(name, schema, payload)
        if name == "market_summarize":
            result["text"] = "Hallazgo atribuido a la muestra. " * 590
            assert len(result["text"]) <= 20000
        return result

    model.ask = long_summary
    delivery = {**DELIVERY, "task_run_id": "long-summary", "template": first["report"]["template"]}
    packets = market_native_turn(runtime, command=command("summary", delivery, "Resume el informe anterior."))
    response = packets[-1]["response"]
    assert response["status"] == "complete" and len(response["markdown"]) > 20000
    completed = next(packet["event"] for packet in packets if packet["kind"] == "event" and packet["event"]["type"] == "run.completed")
    assert len(completed["text"]) <= 20000
    assert response["report"]["source_request_id"] == "request-1"


def test_runtime_declares_report_capability_without_model_or_actor_calls(tmp_path, monkeypatch):
    runtime, _, model, provider, _ = market_native_fixture(tmp_path, monkeypatch)

    async def inspect():
        messages = await runtime.invoke("inspect_runtime", subject_id="fixture-user", thread_id="fixture-session", stream=True)
        return [json.loads(message.content) async for message in messages]

    metadata = asyncio.run(inspect())[0]
    assert metadata["capabilities"]["report"]["schema_versions"] == ["1.0"]
    assert metadata["capabilities"]["report"]["delivery_modes"] == ["report"]
    assert metadata["model_calls"] == metadata["apify_starts"] == 0
    assert model.calls == [] and provider.starts == []


def review_fixture():
    from ai_market_intelligence.runtime.workflow import MarketWorkflow
    from ai_market_intelligence.runtime.report_analysis import annotation_key
    from tests.runtime.test_market_intelligence_graph import market_fixture, market_evidence

    store, model, collector, _ = market_fixture(("facebook",))
    store.begin("review", "Pregunta de informe")
    workflow = MarketWorkflow(store, model, collector, enabled_sources=("facebook",))
    units = [{**market_evidence("facebook", number), "content_basis": "post_text",
              "text": "Texto original controlado. " * 440} for number in range(1, 8)]
    proposals = []
    for unit in units:
        annotation = {"evidence_id": unit["evidence_id"], "target": "Empresa",
                      "aspect": "aspecto" * 60, "label": "unclassified",
                      "supporting_text": unit["text"][:1000], "limitations": [str(i) + "L" * 490 for i in range(12)]}
        proposals.append({"unit_id": annotation_key(annotation), "annotation": annotation})
    return workflow, model, units, proposals


def test_sentiment_review_batches_reserve_space_for_originals_and_proposals():
    workflow, model, units, proposals = review_fixture()
    observed = []

    async def ask(name, schema, payload):
        observed.append(payload)
        assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= 90000
        assert {row["annotation"]["evidence_id"] for row in payload["units"]} <= {row["evidence_id"] for row in payload["evidence"]}
        return {"reviews": [{"unit_id": row["unit_id"], "accepted": True, "issues": []} for row in payload["units"]]}

    model.ask = ask
    accepted = asyncio.run(workflow.review_report_sentiment({"target": "Empresa", "objective": "Investigar"}, units, proposals))
    assert len(observed) > 1
    assert set(accepted) == {row["unit_id"] for row in proposals}
    assert sum(len(payload["units"]) for payload in observed) == len(proposals)


def test_sentiment_reviewer_cannot_reject_an_annotation_with_another_original():
    workflow, model, units, proposals = review_fixture()

    async def ask(name, schema, payload):
        return {"reviews": [{"unit_id": row["unit_id"], "accepted": False,
                 "issues": [{"claim": "Etiqueta rechazada", "evidence_id": units[-1]["evidence_id"],
                             "quoted_text": units[-1]["text"][:100], "reason": "Original distinto."}]}
                for row in payload["units"]]}

    model.ask = ask
    with pytest.raises(ValueError, match="quote its original"):
        asyncio.run(workflow.review_report_sentiment_batch({"target": "Empresa", "objective": "Investigar"}, units, proposals[:2]))
