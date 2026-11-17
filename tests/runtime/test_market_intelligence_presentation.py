"""Presentation semantics travel through existing events without changing text packets."""

import asyncio
import copy
import json
from pathlib import Path

import pytest

from ai_market_intelligence.runtime.collector import _merge_social_captures
from ai_market_intelligence.runtime.research_contracts import MarketCollection, MarketEvent
from ai_market_intelligence.runtime.runtime_core import ProviderQueryRejected
from ai_market_intelligence.runtime.effects import MarketEffectPending
from ai_market_intelligence.runtime.state import MarketCancelled
from ai_market_intelligence.runtime.workflow import MarketWorkflow
from ai_market_intelligence.runtime.wire import MarketWireDecoder, market_replay_packet, validate_wire_packet
from tests.runtime.test_market_intelligence_graph import market_fixture, market_evidence
from tests.runtime.test_market_intelligence_research_collector import market_collection_fixture


WINDOW = {'start': '2026-10-27T12:00:00+00:00', 'end': '2026-11-02T12:00:00+00:00'}


def progress_fixture(monkeypatch, *, event_limit=1):
    store, model, collector, _ = market_fixture(('facebook',), event_limit=event_limit)
    store.begin('fixture-request', 'Una consulta temática')
    store.save(brief=WINDOW)
    workflow = MarketWorkflow(store, model, collector, enabled_sources=('facebook',))
    emitted = []
    monkeypatch.setattr('langgraph.config.get_stream_writer', lambda: emitted.append)
    task = {'task_id': 'task-1', 'source': 'facebook', 'role': 'market_analysis'}
    return store, collector, workflow, task, emitted


@pytest.mark.parametrize('rows,coverage,outcome', [
    ([], {}, 'empty'),
    ([], {'omitted_textless_items': 1}, 'insufficient'),
    ([], {'failed_pages': [{'reason': 'access_challenge'}]}, 'insufficient'),
    ([], {'unavailable_search_routes': ['keyword']}, 'insufficient'),
    ([], {'provider_omitted_records': 2}, 'insufficient'),
    ([{'published_at': '2020-01-01T00:00:00Z'}], {}, 'insufficient'),
])
def test_capture_distinguishes_empty_from_filtered_or_incomplete(rows, coverage, outcome):
    _, _, collector = market_collection_fixture()
    captured = collector.collection('facebook', 'facebook', 'operation',
        {'items': rows, 'coverage': coverage}, {'runId': 'receipt'}, WINDOW)
    assert captured['result_outcome'] == outcome
    assert not captured['evidence']


def test_aggregate_empty_requires_every_capture_to_confirm_it():
    empty = {'status': 'partial', 'evidence': [], 'limitations': [],
             'operation_id': 'capture', 'result_outcome': 'empty'}
    assert _merge_social_captures('group', [empty, empty])['result_outcome'] == 'empty'
    unknown = {key: value for key, value in empty.items() if key != 'result_outcome'}
    assert _merge_social_captures('group', [empty, unknown])['result_outcome'] == 'insufficient'


@pytest.mark.parametrize('status,outcome,limits', [
    ('partial', 'empty', []), ('partial', 'insufficient', ['Texto excluido.']),
    ('unavailable', None, ['Conector no disponible.']), ('pending', None, []),
])
def test_progress_declares_outcome_without_rewriting_the_message(monkeypatch, status, outcome, limits):
    store, collector, workflow, task, emitted = progress_fixture(monkeypatch)

    async def collect(**kwargs):
        return {'status': status, 'evidence': [], 'limitations': limits,
                'operation_id': 'operation', 'result_outcome': outcome}

    collector.collect = collect
    asyncio.run(workflow.retrieve_collection(workflow.current(), task, 'operation', 'operation'))
    activity = emitted[-1]['coverage']['presentation']['activity']
    assert activity['outcome'] == (outcome or status)
    assert activity['state'] == ('pending' if status == 'pending' else 'completed')
    assert activity['activity_id'] == emitted[0]['coverage']['presentation']['activity']['activity_id']
    assert emitted[-1]['limitations'] == limits
    packet = json.loads(market_replay_packet(store, 'fixture-request', 0))
    assert packet['replay']['snapshot']['activities'][activity['activity_id']] == emitted[-1]
    decoder = MarketWireDecoder()
    assert decoder.feed(json.dumps(packet) + '\n') == [packet]
    decoder.finish()


@pytest.mark.parametrize('error,outcome,phase', [
    (ProviderQueryRejected('not supported'), 'query_rejected', 'retrieved'),
    (ValueError('private provider detail'), 'failed', 'failed'),
])
def test_rejected_and_failed_retrieval_close_the_activity_without_retry(monkeypatch, error, outcome, phase):
    store, collector, workflow, task, emitted = progress_fixture(monkeypatch)
    calls = []

    async def collect(**kwargs):
        calls.append(kwargs)
        raise error

    collector.collect = collect
    run = workflow.retrieve_collection(workflow.current(), task, 'operation', 'operation')
    if outcome == 'failed':
        with pytest.raises(ValueError, match='private provider detail'):
            asyncio.run(run)
    else:
        asyncio.run(run)
    assert len(calls) == 1
    assert [e['coverage']['phase'] for e in emitted] == ['retrieving', phase]
    assert emitted[-1]['coverage']['presentation']['activity']['outcome'] == outcome
    assert 'private provider detail' not in json.dumps(emitted)
    assert len(store.replay()['snapshot']['activities']) == 1


@pytest.mark.parametrize('error', [MarketEffectPending('uncertain'), MarketCancelled('cancelled')])
def test_pending_and_cancelled_retrieval_are_not_reported_as_failed(monkeypatch, error):
    _, collector, workflow, task, emitted = progress_fixture(monkeypatch)

    async def collect(**kwargs):
        raise error

    collector.collect = collect
    with pytest.raises(type(error)):
        asyncio.run(workflow.retrieve_collection(workflow.current(), task, 'operation', 'operation'))
    assert not any(e['coverage']['presentation']['activity']['state'] == 'failed' for e in emitted)
    if isinstance(error, MarketEffectPending):
        assert emitted[-1]['coverage']['presentation']['activity']['state'] == 'pending'
    else:
        assert len(emitted) == 1  # Cancellation is closed by the turn's terminal event.


def test_two_concurrent_tasks_of_one_source_survive_replay_and_reuse(monkeypatch):
    store, collector, workflow, first, emitted = progress_fixture(monkeypatch)
    second = {**first, 'task_id': 'task-2'}
    entered = set()
    both_entered = asyncio.Event()

    async def collect(**kwargs):
        entered.add(kwargs['operation_key'])
        if len(entered) == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=2)
        return {'status': 'partial', 'evidence': [], 'limitations': [],
                'operation_id': kwargs['operation_key'], 'result_outcome': 'empty'}

    collector.collect = collect

    async def run():
        await asyncio.gather(*[
            workflow.retrieve_collection(workflow.current(), task, key, key)
            for task, key in ((first, 'operation-1'), (second, 'operation-2'))])
        await workflow.retrieve_collection(workflow.current(), first, 'operation-1', 'operation-1')

    asyncio.run(run())
    assert len(entered) == 2
    snapshot = store.replay()['snapshot']
    assert len(snapshot['activities']) == 2
    assert len(snapshot['activity']) == 1  # Existing per-source view remains compatible.
    assert {e['coverage']['presentation']['activity']['state'] for e in snapshot['activities'].values()} == {'completed'}
    assert emitted[-1]['coverage']['phase'] == 'reused'
    first_id = emitted[0]['coverage']['presentation']['activity']['activity_id']
    assert emitted[-1]['coverage']['presentation']['activity']['activity_id'] == first_id
    value = workflow.current()
    workflow.collection_progress(value, first, 'operation-1', 'reused', value['collections']['operation-1'])
    assert emitted[-1]['event_id'] == emitted[-2]['event_id']


@pytest.mark.parametrize('event_type,purpose,status', [
    ('source.coverage', 'progress', 'provisional'), ('draft.upsert', 'result', 'provisional'),
    ('draft.corrected', 'result', 'provisional'), ('draft.compacted', 'result', 'final'),
    ('clarification.required', 'clarification', 'pending'), ('artifact.upsert', 'artifact', 'final'),
    ('run.completed', 'answer', 'partial'), ('run.failed', 'terminal', 'failed'),
    ('run.cancelled', 'terminal', 'cancelled'),
])
def test_purpose_is_typed_and_does_not_convert_partial_to_complete(event_type, purpose, status):
    store, _, _, _ = market_fixture()
    store.begin('fixture-request', 'Pregunta')
    event = store.append(event_type, text='Texto original.', status=status)
    assert event['coverage']['presentation'] == {'schema_version': '1.0', 'purpose': purpose}
    assert event['status'] == status and event['text'] == 'Texto original.'
    historical = copy.deepcopy(event)
    del historical['coverage']['presentation']
    MarketEvent.model_validate(historical)  # Historical events do not acquire new meaning.
    invalid = copy.deepcopy(event)
    invalid['coverage']['presentation']['purpose'] = 'answer' if purpose != 'answer' else 'result'
    with pytest.raises(ValueError):
        MarketEvent.model_validate(invalid)


@pytest.mark.parametrize('change', ['foreign_turn', 'unknown_field', 'false_completion', 'wrong_inventory_key'])
def test_presentation_rejects_contradictions_and_foreign_replay(monkeypatch, change):
    store, _, workflow, task, _ = progress_fixture(monkeypatch)
    workflow.collection_progress(workflow.current(), task, 'operation', 'retrieving')
    workflow.collection_progress(workflow.current(), task, 'operation', 'retrieving')
    store.append('source.coverage', text='Otro estado.')  # Expire the first event.
    packet = json.loads(market_replay_packet(store, 'fixture-request', 0))
    activities = packet['replay']['snapshot']['activities']
    key, event = next(iter(activities.items()))
    if change == 'foreign_turn':
        event['turn_id'] = 'another-request'
    elif change == 'unknown_field':
        event['coverage']['presentation']['activity']['new_field'] = 'unexpected'
    elif change == 'false_completion':
        event['coverage']['presentation']['activity']['state'] = 'completed'
    else:
        activities['wrong-key'] = activities.pop(key)
    with pytest.raises(ValueError):
        validate_wire_packet(packet)


@pytest.mark.parametrize('container', ['blocks', 'activity'])
@pytest.mark.parametrize('change', ['purpose', 'foreign_turn'])
def test_presentation_is_checked_in_every_snapshot_container(monkeypatch, container, change):
    store, _, workflow, task, _ = progress_fixture(monkeypatch)
    workflow.collection_progress(workflow.current(), task, 'operation', 'retrieving')
    store.append('draft.upsert', block_id='source-facebook', text='Avance.')
    packet = json.loads(market_replay_packet(store, 'fixture-request', 0))
    snapshot = packet['replay']['snapshot']
    snapshot['activities'] = {}
    event = next(iter(snapshot[container].values()))
    if change == 'purpose':
        event['coverage']['presentation']['purpose'] = 'answer'
    else:
        event['turn_id'] = 'another-request'
    with pytest.raises(ValueError):
        validate_wire_packet(packet)


def test_shared_ndjson_fixture_validates_without_changing_text_keys():
    path = Path('tests/fixtures/presentation-v1.ndjson')
    decoder = MarketWireDecoder()
    packets = decoder.feed(path.read_text(encoding='utf-8'))
    decoder.finish()
    assert {'event', 'snapshot', 'response', 'text'} <= {p['kind'] for p in packets}
    text = [p for p in packets if p['kind'] == 'text']
    assert [p['phase'] for p in text] == ['start', 'delta', 'end', 'discard']
    assert all('presentation' not in p and 'coverage' not in p for p in text)
    snapshot = next(p for p in packets if p['kind'] == 'snapshot')['replay']['snapshot']
    assert len(snapshot['activities']) == 2
    terminal = next(p for p in packets if p['kind'] == 'event' and p['event']['type'] == 'run.completed')
    assert terminal['event']['status'] == 'partial'


@pytest.mark.parametrize('changes', [
    {'status': 'pending', 'result_outcome': 'empty'},
    {'status': 'unavailable', 'result_outcome': 'empty'},
    {'evidence': [market_evidence()], 'result_outcome': 'empty'},
    {'limitations': ['Parte de la captura falló.'], 'result_outcome': 'empty'},
])
def test_collection_does_not_claim_empty_from_unavailable_or_partial_capture(changes):
    with pytest.raises(ValueError):
        MarketCollection.model_validate({'status': 'partial', 'evidence': [], 'limitations': [],
                                         'operation_id': 'operation', **changes})
