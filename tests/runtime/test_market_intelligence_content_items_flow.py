"""Individual summaries are reviewed in batches before canonical publication."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from ai_market_intelligence.runtime.content_items import build_content_units
from ai_market_intelligence.runtime.model import content_units_context, content_items_context, context_size
from ai_market_intelligence.runtime.workflow import MarketWorkflow
from ai_market_intelligence.runtime.wire import market_stream_graph, market_replay_packet
from tests.runtime.test_market_intelligence_graph import market_fixture, market_evidence


def test_shared_fixture_preserves_spanish_and_identical_collection_across_envelopes():
    fixture = json.loads(Path('tests/fixtures/content-items-v1.json').read_text(encoding='utf-8'))
    assert fixture['legacy_response']['response']['markdown'] == 'Respuesta sin colección opcional.'
    assert fixture['collection']['items'][0]['text'] == 'El autor anunció una oferta de temporada.'
    for name in ('event', 'response', 'snapshot'):
        assert fixture[name]['content_items'] == fixture['collection']


def test_source_replacement_uses_captured_comments_when_analysis_context_omits_their_unit():
    store, _, _, _ = market_fixture(('facebook',))
    store.begin('fixture-request', 'Comentarios sobre la publicación')
    parent = {**market_evidence('facebook'), 'evidence_id': 'parent',
              'url': 'https://facebook.com/example/posts/1', 'content_basis': 'post_text', 'text': 'Anuncio.'}
    comment = {**parent, 'evidence_id': 'comment', 'content_basis': 'comment_text',
               'url': parent['url'] + '?comment_id=2', 'requested_url': parent['url'], 'text': 'Crítica individual.'}
    store.save(evidence={'parent': parent, 'comment': comment})
    units = build_content_units([parent, comment])
    drafts = [{'unit_id': unit['unit_id'], 'text': 'Opinión atribuida.' if unit['is_comments'] else 'Anuncio.',
               'evidence_ids': unit['evidence_ids'], 'limitations': []} for unit in units]
    first = store.replace_content_items('facebook', units, drafts)
    original = copy.deepcopy(first)
    parent_unit = next(unit for unit in units if not unit['is_comments'])
    parent_draft = next(draft for draft in drafts if draft['unit_id'] == parent_unit['unit_id'])
    second = store.replace_content_items('facebook', [parent_unit], [parent_draft])
    assert first == original and first['items'][0]['comments_analysis']['analyzed_count'] == 1
    assert second['revision'] == second['items'][0]['item_revision'] == 2
    assert second['items'][0]['comments_analysis']['status'] == 'unavailable'
    assert second['items'][0]['comments_analysis']['text'] is None
    replay = json.loads(market_replay_packet(store, 'fixture-request', 0))
    assert replay['content_items'] == second


def item_flow(*, reject_comments=False, invalid_parent=False):
    store, model, collector, graph = market_fixture(('facebook',))
    parent = market_evidence('facebook')
    parent.update(evidence_id='parent', url='https://facebook.com/example/posts/1',
                  content_basis='post_text', text='El autor anunció una oferta de temporada.')
    comment = {**copy.deepcopy(parent), 'evidence_id': 'comment',
               'url': parent['url'] + '?comment_id=2', 'requested_url': parent['url'],
               'content_basis': 'comment_text', 'text': 'Una persona valoró la utilidad de la oferta.'}

    async def collect(**kwargs):
        collector.calls.append(copy.deepcopy(kwargs))
        return {'status': 'complete', 'evidence': [parent, comment],
                'limitations': [], 'operation_id': 'fixture-operation'}

    collector.collect = collect
    original = model.ask

    async def ask(name, schema, payload):
        if name == 'market_verify_items':
            model.calls.append(name)
            rows = {row['evidence_id']: row for row in payload['evidence']}
            return {'reviews': [
                {'unit_id': unit['unit_id'], 'accepted': not (reject_comments and unit['evidence_ids'] == ['comment']),
                 'issues': [{'claim': unit['text'], 'evidence_id': 'comment',
                             'quoted_text': rows['comment']['text'], 'reason': 'La inferencia no está respaldada.'}]
                           if reject_comments and unit['evidence_ids'] == ['comment'] else []}
                for unit in payload['units']]}
        result = await original(name, schema, payload)
        if name == 'market_analyze':
            result['items'] = [
                {'unit_id': unit['unit_id'], 'text': comment['text'] if unit['is_comments'] else parent['text'],
                 'evidence_ids': ['parent'] if invalid_parent and unit['is_comments'] else unit['evidence_ids'],
                 'limitations': ['Sólo la muestra recuperada.']}
                for unit in payload['content_units']]
        if name == 'market_consolidate':
            assert payload['content_items']['items']
            assert {row['evidence_id'] for row in payload['evidence']} >= {'parent'}
        return result

    model.ask = ask
    return store, model, collector, graph


@pytest.mark.parametrize('reject_comments', [False, True])
def test_items_and_comments_have_independent_verdicts_and_replay_without_new_calls(reject_comments):
    store, model, collector, graph = item_flow(reject_comments=reject_comments)

    async def run():
        return [json.loads(line) async for line in market_stream_graph(graph, store,
            {'request_id': 'fixture-request', 'question': 'Qué se dijo en la muestra'},
            {'configurable': {'thread_id': 'fixture-request'}})]

    packets = asyncio.run(run())
    final = packets[-1]
    assert final['kind'] == 'response'
    item = final['content_items']['items'][0]
    assert item['text'] == 'El autor anunció una oferta de temporada.'
    assert item['evidence_ids'] == ['parent']
    comments = item.get('comments_analysis')
    if reject_comments:
        assert comments is None or comments['text'] is None
    else:
        assert comments['parent_item_id'] == item['item_id']
        assert comments['evidence_ids'] == ['comment'] and comments['analyzed_count'] == 1
    assert model.calls.count('market_verify_items') == 1
    assert model.calls.index('market_analyze') < model.calls.index('market_verify_items') < model.calls.index('market_draft')
    assert any(p.get('content_items') for p in packets if p['kind'] == 'event')
    before = list(model.calls), copy.deepcopy(collector.calls)
    replay = json.loads(market_replay_packet(store, 'fixture-request', 0))
    assert replay['content_items'] == final['content_items']
    assert (model.calls, collector.calls) == before
    assert 'content_items' not in final['response']


def test_cross_parent_proposal_fails_before_draft_or_review():
    store, model, _, graph = item_flow(invalid_parent=True)

    async def run():
        return await graph.ainvoke({'request_id': 'fixture-request', 'question': 'Qué se dijo en la muestra'},
            {'configurable': {'thread_id': 'fixture-request'}})

    result = asyncio.run(run())
    assert result['response']['status'] == 'failed'
    assert 'market_draft' not in model.calls and 'market_verify_items' not in model.calls
    assert not store.read()[0].get('content_items')


def test_state_retains_revision_floor_after_withdrawal_and_cancellation_is_explicit():
    store, _, _, _ = market_fixture(('facebook',))
    row = market_evidence('facebook')
    row.update(content_basis='post_text', url='https://facebook.com/example/posts/1')
    units = build_content_units([row])
    draft = {'unit_id': units[0]['unit_id'], 'text': 'Hallazgo de la publicación.',
             'evidence_ids': [row['evidence_id']], 'limitations': []}
    store.begin('fixture-request', 'Pregunta')
    store.save(evidence={row['evidence_id']: row})
    first = store.replace_content_items('facebook', units, [draft])
    withdrawn = store.replace_content_items('facebook', [], [])
    changed = store.replace_content_items('facebook', units, [{**draft, 'text': 'Hallazgo revisado de la publicación.'}])
    assert withdrawn['items'] == [] and withdrawn['revision'] > first['revision']
    assert changed['items'][0]['item_id'] == first['items'][0]['item_id']
    assert changed['items'][0]['item_revision'] > first['items'][0]['item_revision']
    response, _ = store.cancel()
    assert response['content_items']['items'] == []
    assert response['content_items']['revision'] > changed['revision']


def test_optional_units_keep_parent_closure_and_do_not_overflow_analysis_context():
    parent = {**market_evidence('facebook'), 'evidence_id': 'parent',
              'content_basis': 'post_text', 'url': 'https://facebook.com/example/posts/1'}
    comment = {**parent, 'evidence_id': 'comment', 'content_basis': 'comment_text',
               'requested_url': parent['url'], 'url': parent['url'] + '?comment_id=2'}
    units = build_content_units([parent, comment])
    context = content_units_context({}, [comment], units)
    assert context['content_units'] == []
    assert context['content_unit_coverage']['omitted'] == 2
    rows = [{**parent, 'evidence_id': f'row-{number}',
             'url': f'https://facebook.com/example/posts/{number}',
             'limitations': ['L' * 500] * 12} for number in range(100)]
    context = content_units_context({}, rows, build_content_units(rows))
    assert context_size(context) <= 90000
    assert context['evidence'] and len(context['content_units']) <= 50
    assert all(set(unit['evidence_ids']).issubset({r['evidence_id'] for r in context['evidence']})
               for unit in context['content_units'])


def completed_item_workflow():
    store, model, collector, graph = item_flow()
    asyncio.run(graph.ainvoke({'request_id': 'fixture-request', 'question': 'Qué se dijo en la muestra'},
                {'configurable': {'thread_id': 'fixture-request'}}))
    return store, model, MarketWorkflow(store, model, collector, enabled_sources=('facebook',))


def test_source_draft_receives_item_originals_even_when_findings_cite_only_parent():
    store, _, workflow = completed_item_workflow()
    context = workflow.draft_context({'task': {'source': 'facebook'}, 'analysis': {
        'findings': [{'supports': ['parent'], 'refutes': []}], 'financial_facts': []}}, ['parent'])
    assert context['content_items']['items'] == store.read()[0]['content_items']['items']
    assert {row['evidence_id'] for row in context['evidence']} == {'parent', 'comment'}
    # Optional metadata must fit together with the originals, never replace them.
    enlarged = copy.deepcopy(context['content_items']['items'][0])
    enlarged['text'] = 'A' * 3000
    bounded = content_items_context({}, [{**row, 'text': 'B' * 44000} for row in context['evidence']], [enlarged])
    assert context_size(bounded) <= 90000
    assert bounded['content_item_coverage']['omitted'] == 1


def test_many_valid_source_blocks_are_admitted_with_originals_before_synthesis():
    rows = [market_evidence(source) for source in ('facebook', 'instagram', 'web', 'x',
                                                 'youtube', 'market_data', 'world_bank', 'financial_document')]
    blocks = [{'text': 'A' * 20000, 'evidence_ids': [row['evidence_id']]} for row in rows]
    context = content_items_context({'blocks': blocks}, rows, [])
    assert context_size(context) <= 90000
    assert context['evidence'] == rows
    assert 0 < len(context['blocks']) < len(blocks)
    assert context['block_coverage']['omitted'] == len(blocks) - len(context['blocks'])


def test_stream_sends_collection_only_on_revision_change_and_skips_identical_events():
    store, _, _ = completed_item_workflow()
    from ai_market_intelligence.runtime.wire import market_event_packet
    publication = {'events': {}, 'content_revision': None}
    events = store.read()[0]['events']
    first = json.loads(market_event_packet(store, events[0], publication=publication))
    second = json.loads(market_event_packet(store, events[1], publication=publication))
    assert 'content_items' in first and 'content_items' not in second
    assert market_event_packet(store, events[0], publication=publication) is None
    with pytest.raises(ValueError, match='immutable'):
        market_event_packet(store, {**events[0], 'text': 'Evento alterado.'}, publication=publication)
    store.replace_content_items('facebook', [], [])
    updated = json.loads(market_event_packet(store, events[2], publication=publication))
    assert updated['content_items']['revision'] > first['content_items']['revision']
    assert updated['content_items']['items'] == []


@pytest.mark.parametrize('status', ['pending', 'failed'])
def test_delivery_keeps_item_reference_manifest_when_other_work_is_unfinished(status):
    store, _, workflow = completed_item_workflow()
    store.save(response={'status': status, 'references': [], 'artifacts': [], 'markdown': 'Consulta incompleta.'})
    result = asyncio.run(workflow.deliver({}))
    from ai_market_intelligence.runtime.wire import market_wire_line
    packet = json.loads(market_wire_line('response', request_id='fixture-request', response=result['response']))
    assert packet['content_items']['items']
    assert {row['evidence_id'] for row in packet['references']} == {'parent', 'comment'}


def test_review_metadata_is_bounded_with_evidence_and_new_collection_gets_new_event(monkeypatch):
    store, model, workflow = completed_item_workflow()
    from ai_market_intelligence.runtime import workflow as module
    original = module.evidence_context
    observed = []
    def bounded(payload, evidence):
        assert payload['content_units']
        observed.append(copy.deepcopy(payload['content_units']))
        return original(payload, evidence)
    monkeypatch.setattr(module, 'evidence_context', bounded)
    units = build_content_units(list(store.read()[0]['evidence'].values()))
    drafts = [{'unit_id': unit['unit_id'], 'text': 'Resumen revisado.',
               'evidence_ids': unit['evidence_ids'], 'limitations': []} for unit in units]
    events = []
    token = workflow._worker_writer.set(events.append)
    try:
        async def run():
            for text in ('Resumen revisado.', 'Otro resumen revisado.'):
                await workflow.review_content_items({'task': {'source': 'facebook', 'role': 'market_analysis'},
                    'analysis': {'content_units': units, 'items': [{**d, 'text': text} for d in drafts]}})
        asyncio.run(run())
    finally:
        workflow._worker_writer.reset(token)
    assert observed and len(events) == 2
    assert events[0]['event_id'] != events[1]['event_id']
    assert events[0]['coverage']['content_revision'] < events[1]['coverage']['content_revision']
