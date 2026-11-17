"""Calendar selection uses the market's IANA zone, not the server's UTC date."""

import asyncio
from datetime import datetime, timezone

import pytest

from ai_market_intelligence.runtime.periods import relative_day_window
from ai_market_intelligence.runtime.research_contracts import MarketUnderstanding
from ai_market_intelligence.runtime.workflow import MarketWorkflow
from tests.runtime.test_market_intelligence_graph import market_fixture
from ai_market_intelligence.runtime.decisions.understand import normalize_period


def test_colombia_today_is_previous_utc_date_and_stops_at_the_request_clock():
    now = datetime.fromisoformat("2026-11-11T04:15:14+00:00")
    assert relative_day_window(now, "today", country_code="CO") == {
        "start": "2026-11-10T05:00:00+00:00",
        "end": "2026-11-11T04:15:14+00:00",
        "publication_timezone": "America/Bogota",
    }


def test_explicit_zone_yesterday_preserves_the_25_hour_dst_day():
    window = relative_day_window(datetime.fromisoformat("2026-11-02T12:00:00+00:00"),
                                 "yesterday", "America/New_York")
    assert window["start"] == "2026-11-01T04:00:00+00:00"
    assert window["end"] == "2026-11-02T04:59:59.999999+00:00"


def test_public_period_label_uses_the_resolved_calendar_day_without_changing_storage():
    window = relative_day_window(datetime.fromisoformat("2026-11-11T04:15:14+00:00"), "today",
                                 country_code="CO")
    label = MarketWorkflow.report_text({"brief": {**window, "retrieval_mode": "conversation"},
                                        "evidence": {}}, "Sin hallazgos suficientes.")
    assert "10 de noviembre de 2026, 00:00:00 -05" in label
    assert "10 de noviembre de 2026, 23:15:14 -05" in label
    assert window["start"] == "2026-11-10T05:00:00+00:00"


@pytest.mark.parametrize("country", ["US", None, "ZZ"])
def test_missing_or_ambiguous_market_zone_is_not_assumed_to_be_utc(country):
    assert relative_day_window(datetime.now(timezone.utc), "today", country_code=country) is None


def test_empty_explicit_timezone_does_not_silently_select_the_country_timezone():
    assert relative_day_window(datetime.now(timezone.utc), "today", "", "CO") is None


@pytest.mark.parametrize("zone,country,day", [(12, "CO", "today"), (None, [], "today"),
                                            (None, "CO", []), (None, "CO", "tomorrow")])
def test_invalid_calendar_selectors_are_rejected_before_any_tool(zone, country, day):
    with pytest.raises(ValueError):
        relative_day_window(datetime.now(timezone.utc), day, zone, country)


@pytest.mark.parametrize("country,expected", [("CO", "plan"), ("US", "deliver")])
def test_intake_resolves_local_today_or_requests_clarification_without_collection(country, expected):
    store, model, collector, _ = market_fixture(("facebook",))
    original = model.ask

    async def today(name, schema, payload):
        result = await original(name, schema, payload)
        if name == "market_understand":
            result.update(relative_day="today", period_is_explicit=True, start=None, end=None,
                          publication_timezone=None, search_country_code=country)
        return result

    model.ask = today
    graph = MarketWorkflow(store, model, collector, enabled_sources=("facebook",),
                           clock=lambda: datetime.fromisoformat("2026-11-11T04:15:14+00:00")).build()

    async def run():
        return await graph.ainvoke({"request_id": "today", "question": "Qué se dice hoy de una empresa?"},
                                  interrupt_before=["plan", "deliver"])

    result = asyncio.run(run())
    assert result["route"] == expected and not collector.calls
    brief = store.read()[0]["brief"]
    assert "relative_day" not in brief
    if country == "CO":
        assert brief["publication_timezone"] == "America/Bogota"
        assert brief["start"] == "2026-11-10T05:00:00+00:00"
        assert brief["end"] == "2026-11-11T04:15:14+00:00"
    else:
        assert brief["clarification"] and not brief["start"] and not brief["end"]


def test_explicit_nonrelative_dates_and_default_seven_day_contract_remain_available():
    from ai_market_intelligence.runtime.decisions.understand import normalize_period

    explicit = {"period_is_explicit": True, "start": "2026-10-01", "end": "2026-10-31"}
    before = dict(explicit)
    normalize_period(explicit, {"now": "2026-11-11T04:15:14+00:00"})
    assert explicit == before
    default = {"period_is_explicit": False, "start": None, "end": None}
    normalize_period(default, {"now": "2026-11-11T04:15:14+00:00"})
    assert default["start"] == "2026-11-04T04:15:14+00:00"
    assert default["end"] == "2026-11-11T04:15:14+00:00"


@pytest.mark.parametrize("mode", ["documentary", "financial", "conversation"])
@pytest.mark.parametrize("explicit", [True, False])
def test_relative_day_does_not_abort_requested_clarification(mode, explicit):
    store, model, collector, graph = market_fixture(("web",))
    original = model.ask
    clarification = "¿Te refieres al desempeño comercial o a lo que se comenta sobre la cadena?"

    async def ambiguous_today(name, schema, payload):
        result = await original(name, schema, payload)
        if name == "market_understand":
            result.update(relative_day="today", period_is_explicit=explicit,
                          retrieval_mode=mode, start=None, end=None,
                          clarification=clarification)
        return result

    model.ask = ambiguous_today

    async def run():
        return [event async for event in graph.astream(
            {"request_id": "ambiguous-today", "question": "Cómo le fue a D1 en el mercado el día de hoy"},
            config={"configurable": {"thread_id": "ambiguous-today"}}, stream_mode="custom")]

    events = asyncio.run(run())
    saved = store.read()[0]
    assert saved["status"] == "needs_clarification"
    assert saved["response"]["markdown"] == clarification
    assert saved["response"]["references"] == []
    assert saved["plan"] is None and not collector.calls
    assert model.calls == ["market_understand"]
    assert any(event["type"] == "clarification.required" and event["text"] == clarification
               for event in events)
    assert not any(event["type"] == "run.failed" for event in events)


@pytest.mark.parametrize("mode,explicit", [("documentary", True), ("financial", True),
                                          ("conversation", False)])
def test_relative_day_still_requires_explicit_conversation_when_not_clarifying(mode, explicit):
    result = {"intent": "research", "retrieval_mode": mode, "relative_day": "today",
              "period_is_explicit": explicit, "clarification": None}
    with pytest.raises(ValueError, match="requires explicit conversation research"):
        normalize_period(result, {"now": "2026-11-12T21:34:18+00:00"})
