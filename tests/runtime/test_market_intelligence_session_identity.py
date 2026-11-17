"""A new chat turn never inherits a pending request's wire identity."""

import copy
import json

import pytest

from tests.runtime.test_market_intelligence_research_runtime import (
    market_native_fixture,
    market_native_turn,
)


@pytest.mark.parametrize("second_question", ["Pregunta de mercado", "Otra pregunta"])
def test_new_turn_reports_pending_under_its_own_identity_without_effects(
    tmp_path, monkeypatch, second_question
):
    runtime, storage, model, provider, _ = market_native_fixture(tmp_path, monkeypatch)
    provider.pending = True
    first = market_native_turn(runtime, question="Pregunta de mercado", request_id="original")
    assert first[-1]["response"]["status"] == "pending"
    assert first[-1]["response"]["continuation_safe"] is True
    before = copy.deepcopy(storage.objects)
    calls = (list(model.calls), list(provider.starts))

    second = market_native_turn(runtime, question=second_question, request_id="new-turn")

    assert len(second) == 1
    assert second[0]["kind"] == "response"
    assert second[0]["request_id"] == "new-turn"
    assert second[0]["response"]["status"] == "pending"
    assert second[0]["response"]["pending_request_id"] == "original"
    assert second[0]["response"]["resumable"] is True
    assert second[0]["response"]["continuation_safe"] is False
    assert storage.objects == before
    assert (model.calls, provider.starts) == calls


def test_original_identity_resumes_and_replays_without_duplicate_research(
    tmp_path, monkeypatch
):
    runtime, storage, model, provider, _ = market_native_fixture(tmp_path, monkeypatch)
    provider.pending = True
    first = market_native_turn(runtime, question="Pregunta de mercado", request_id="original")
    assert first[-1]["response"]["status"] == "pending"

    provider.pending = False
    resumed = market_native_turn(runtime, question="Pregunta de mercado", request_id="original")
    assert resumed[-1]["request_id"] == "original"
    assert resumed[-1]["response"]["status"] in {"complete", "partial"}
    session = next(
        json.loads(raw)
        for key, (raw, _) in storage.objects.items()
        if "/sessions/" in key
    )
    assert session["active"] is None and session["requests"]["original"]["finished"]
    calls = (list(model.calls), list(provider.starts))
    replay = market_native_turn(runtime, command={"action": "replay", "request_id": "original"})
    assert replay[-1]["request_id"] == "original"
    assert replay[-1]["response"] == resumed[-1]["response"]
    assert (model.calls, provider.starts) == calls


def test_same_question_after_completion_can_start_a_new_explicit_request(tmp_path, monkeypatch):
    runtime, _, _, _, _ = market_native_fixture(tmp_path, monkeypatch)
    first = market_native_turn(runtime, question="Pregunta de mercado", request_id="original")
    assert first[-1]["response"]["status"] in {"complete", "partial"}
    second = market_native_turn(runtime, question="Pregunta de mercado", request_id="new-turn")
    assert second[-1]["request_id"] == "new-turn"
    assert second[-1]["response"]["status"] in {"complete", "partial"}


@pytest.mark.parametrize("operation,safe", [
    ({"kind": "apify", "state": "running", "receipt": {"runId": "known", "status": "RUNNING"}}, True),
    ({"kind": "apify", "state": "running", "receipt": {"runId": "known", "status": "SUCCEEDED"},
      "result": {"diagnostic": {"retryable": True}}}, True),
    ({"kind": "apify", "state": "uncertain", "receipt": None}, False),
    ({"kind": "model", "state": "uncertain", "receipt": None}, False),
    ({"kind": "model", "state": "running", "receipt": None}, False),
    ({"kind": "public_document", "state": "running", "receipt": None}, False),
    ({"kind": "apify", "state": "running", "receipt": {"runId": "known", "status": "RUNNING"},
      "result": {"diagnostic": {"retryable": False}}}, False),
    ({"kind": "apify", "state": "running", "receipt": {"status": "RUNNING"}}, False),
    ({"kind": "apify", "state": "running", "receipt": {"runId": "known", "status": "UNKNOWN"}}, False),
    ({"kind": "apify", "state": "completed", "receipt": {"runId": "known", "status": "SUCCEEDED"}}, False),
])
def test_auto_continuation_requires_only_known_retryable_captures(operation, safe):
    from types import SimpleNamespace
    from ai_market_intelligence.runtime.native import MarketAgentRuntime

    effects = SimpleNamespace(read=lambda: ({"operations": {"capture": operation}}, None))
    assert MarketAgentRuntime.capture_continuation_safe(effects) is safe


def test_one_uncertain_effect_prevents_auto_continuation_despite_a_known_capture():
    from types import SimpleNamespace
    from ai_market_intelligence.runtime.native import MarketAgentRuntime

    operations = {
        "capture": {"kind": "apify", "state": "running", "receipt": {"runId": "known", "status": "RUNNING"}},
        "model": {"kind": "model", "state": "uncertain", "receipt": None},
    }
    effects = SimpleNamespace(read=lambda: ({"operations": operations}, None))
    assert MarketAgentRuntime.capture_continuation_safe(effects) is False
    effects.read = lambda: (None, None)
    assert MarketAgentRuntime.capture_continuation_safe(effects) is False
