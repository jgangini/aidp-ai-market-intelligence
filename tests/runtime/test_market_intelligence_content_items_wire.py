"""Optional public content collections preserve canonical wire and replay boundaries."""

import copy
import json
from pathlib import Path

import pytest

from ai_market_intelligence.runtime.state import MarketRunStore
from ai_market_intelligence.runtime.wire import (
    MarketWireDecoder,
    market_event_packet,
    market_replay_packet,
    market_wire_line,
)
from tests.runtime.test_market_intelligence_graph import MarketSnapshot


@pytest.fixture
def content_fixture():
    return json.loads((Path(__file__).parents[1] / "fixtures/content-items-v1.json").read_text(encoding="utf-8"))


def fixture_store(fixture, *, content=True):
    store = MarketRunStore(MarketSnapshot(), "fixture-revision", event_limit=1)
    store.begin("fixture-request", "Pregunta de mercado")
    evidence = {
        row["evidence_id"]: {**row, "text": "Comentario original de su autor." if row["content_basis"] == "comment_text"
                             else "Raw observation is not a public summary.", "receipt_id": "private-receipt"}
        for row in fixture["references"]
    }
    evidence["unrelated"] = {**evidence["post-evidence"], "evidence_id": "unrelated", "url": "https://example.org/unrelated"}
    store.save(evidence=evidence)
    if content:
        store.transact(lambda value: value.update(content_items=copy.deepcopy(fixture["collection"])))
    return store


def test_event_adds_the_full_collection_and_only_its_public_references(content_fixture):
    store = fixture_store(content_fixture)
    event = store.append("source.coverage", text="Captura recibida.")
    packet = json.loads(market_event_packet(store, event))
    assert packet["content_items"] == content_fixture["collection"]
    assert {row["evidence_id"] for row in packet["references"]} == {"post-evidence", "comment-evidence"}
    assert packet["event"]["event_id"] == event["event_id"] and packet["event"]["sequence"] == 1
    assert "content_items" not in packet["event"]
    assert "private-receipt" not in json.dumps(packet) and "Raw observation" not in json.dumps(packet)
    comment = next(row for row in packet["references"] if row["content_basis"] == "comment_text")
    assert comment["text"] == "Comentario original de su autor."
    assert comment["requested_url"] == content_fixture["references"][1]["requested_url"]


def test_expired_replay_preserves_items_without_duplicating_nested_response(content_fixture):
    store = fixture_store(content_fixture)
    store.append("source.coverage", text="Primera actualización.")
    store.append("source.coverage", text="Segunda actualización.")
    response = {**content_fixture["legacy_response"]["response"], "content_items": content_fixture["collection"]}
    store.save(response=response)
    packet = json.loads(market_replay_packet(store, "fixture-request", 0))
    assert packet["replay"]["snapshot"] is not None and packet["replay"]["cursor"] == 2
    assert packet["content_items"] == content_fixture["collection"]
    assert "content_items" not in packet["response"]
    assert "content_items" not in packet["replay"]["snapshot"]["response"]
    assert store.read()[0]["response"]["content_items"] == content_fixture["collection"]
    assert {row["evidence_id"] for row in packet["references"]} == {"post-evidence", "comment-evidence"}
    assert next(row for row in packet["references"] if row["content_basis"] == "comment_text")["text"] == "Comentario original de su autor."


def test_response_lifts_items_without_state_access_or_mutating_its_input(content_fixture):
    response = {**content_fixture["legacy_response"]["response"], "content_items": content_fixture["collection"]}
    original = copy.deepcopy(response)
    wire = market_wire_line("response", request_id="fixture-request", response=response)
    packet = MarketWireDecoder().feed(wire)[0]
    assert packet["content_items"] == content_fixture["collection"]
    assert "content_items" not in packet["response"] and response == original
    assert packet["references"] == content_fixture["references"]


def test_response_authoritative_empty_collection_does_not_republish_old_items(content_fixture):
    store = fixture_store(content_fixture)
    store.save(response={"status": "partial", "markdown": "Sin hallazgos admitidos.",
                         "references": [], "artifacts": [],
                         "content_items": {"schema_version": "1.0", "revision": 2, "items": []}})
    packet = json.loads(market_replay_packet(store, "fixture-request", 0))
    assert packet["content_items"]["revision"] == 2 and packet["content_items"]["items"] == []
    assert packet["references"] == []


def test_legacy_packets_stay_without_the_optional_collection(content_fixture):
    store = fixture_store(content_fixture, content=False)
    event = store.append("source.coverage", text="Legado compatible.")
    packets = [json.loads(market_event_packet(store, event)), json.loads(market_replay_packet(store, "fixture-request", 0))]
    legacy = content_fixture["legacy_response"]
    packets.append(json.loads(market_wire_line("response", request_id=legacy["request_id"], response=legacy["response"])))
    assert all("content_items" not in packet for packet in packets)


def test_known_item_schema_requires_its_comment_reference_but_unknown_schema_is_ignored(content_fixture):
    response = {**content_fixture["legacy_response"]["response"], "content_items": content_fixture["collection"]}
    response["references"] = [content_fixture["references"][0]]
    with pytest.raises(ValueError, match="exist"):
        market_wire_line("response", request_id="fixture-request", response=response)
    response["content_items"] = {"schema_version": "future", "opaque": "Unsupported public extension"}
    packet = MarketWireDecoder().feed(market_wire_line("response", request_id="fixture-request", response=response))[0]
    assert packet["response"]["status"] == "complete" and packet["content_items"]["schema_version"] == "future"
