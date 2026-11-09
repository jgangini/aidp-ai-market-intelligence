"""Final revalidation must retain the scope of each published source block."""
import asyncio
import copy

import pytest

from ai_market_intelligence.runtime.workflow import MarketWorkflow
from tests.runtime.test_market_intelligence_graph import market_fixture, market_run


def test_repair_does_not_reintroduce_unverified_analysis_as_generation_context():
    from ai_market_intelligence.runtime.decisions.report import repair_units

    payload = {
        'findings': [{'claim': 'Unverified synthesis that caused the rejection'}],
        'rejected_block': {'text': 'A report needing a bounded correction'},
        'review_issues': [{'reason': 'A detail belongs to a different source'}],
        'evidence': [{'evidence_id': 'e1', 'text': 'Observed source text'}],
        'brief': {'objective': 'Explain the observed market discussion'},
        'financial_facts': [],
        'computed_artifacts': [],
    }
    original = copy.deepcopy(payload)
    calls = []

    async def ask(name, schema, context, **kwargs):
        calls.append(name)
        assert 'findings' not in context
        assert context == {key: value for key, value in original.items() if key != 'findings'}
        assert kwargs['allowed_evidence'] == ['e1']
        return {'units': []}

    assert asyncio.run(repair_units(ask, payload, allowed_evidence=['e1'])) == {'units': []}
    assert calls == ['market_repair_units']
    assert payload == original


@pytest.mark.parametrize('previous_units', [False, True])
def test_final_source_review_and_repair_keep_inventory_isolated(previous_units):
    store, model, collector, graph = market_fixture(('x', 'youtube'))
    market_run(graph)
    workflow = MarketWorkflow(store, model, collector, enabled_sources=('x', 'youtube'))
    blocks = workflow.published_blocks(workflow.current())
    assert len(blocks) == 2
    if previous_units:
        value = workflow.current()
        for block in blocks:
            value['domain_outcomes'][block['coverage']['source']]['_verified_units'] = [
                {'unit_id': 'u1', **{k: copy.deepcopy(block[k]) for k in ('text', 'evidence_ids', 'limitations')}}
            ]
        store.save(domain_outcomes=value['domain_outcomes'])
    calls = []

    async def review(name, schema, payload):
        calls.append(name)
        source = workflow._worker_source.get()
        assert source in {'x', 'youtube'}
        assert {row['source'] for row in payload['evidence']} == {source}
        assert payload['retrieved_inventory']['by_source'] == {source: 1}
        row = payload['evidence'][0]
        if name == 'market_verify':
            return {'accepted': False, 'issues': [{'claim': 'Controlled rejected detail',
                'evidence_id': row['evidence_id'], 'quoted_text': row['text'], 'reason': 'Remove unsupported detail'}]}
        if name == 'market_repair_units':
            return {'units': [{'unit_id': 'u1', 'text': row['text'],
                               'evidence_ids': [row['evidence_id']], 'limitations': []}]}
        assert name == 'market_verify_repair'
        return {'reviews': [{'unit_id': unit['unit_id'], 'accepted': True, 'issues': []}
                            for unit in payload['units']]}

    model.ask = review
    workflow.emit = lambda *args, **kwargs: None
    async def run():
        await workflow.review_preserved_blocks(blocks, {'text': 'Rejected combined report'}, [])
        assert workflow._worker_source.get() is None
        combined = {'text': 'Combined report', 'evidence_ids': sorted(workflow.current()['evidence']), 'limitations': []}
        payload, omitted = workflow.review_context(combined)
        assert not omitted and set(payload['retrieved_inventory']['by_source']) == {'x', 'youtube'}

    asyncio.run(run())
    assert calls.count('market_verify_repair') == 2
    assert calls.count('market_repair_units') == (0 if previous_units else 2)


@pytest.mark.parametrize('error_type', [RuntimeError, asyncio.CancelledError])
def test_final_source_review_restores_context_after_exception(error_type):
    store, model, collector, graph = market_fixture(('x', 'youtube'))
    market_run(graph)
    workflow = MarketWorkflow(store, model, collector, enabled_sources=('x', 'youtube'))

    async def fail(name, schema, payload):
        assert workflow._worker_source.get() in {'x', 'youtube'}
        raise error_type('controlled review failure')

    model.ask = fail
    async def run():
        with pytest.raises(error_type, match='controlled review failure'):
            await workflow.review_preserved_blocks(workflow.published_blocks(workflow.current()), {}, [])
        assert workflow._worker_source.get() is None

    asyncio.run(run())


def test_late_repair_still_rejects_a_reference_from_another_source():
    store, model, collector, graph = market_fixture(('x', 'youtube'))
    market_run(graph)
    workflow = MarketWorkflow(store, model, collector, enabled_sources=('x', 'youtube'))
    called = []

    async def invent_reference(name, schema, payload):
        called.append(name)
        row = payload['evidence'][0]
        if name == 'market_verify':
            return {'accepted': False, 'issues': [{'claim': 'Unsupported detail',
                'evidence_id': row['evidence_id'], 'quoted_text': row['text'], 'reason': 'Repair required'}]}
        assert name == 'market_repair_units'
        foreign = next(r for r in workflow.current()['evidence'].values() if r['source'] != row['source'])
        return {'units': [{'unit_id': 'u1', 'text': foreign['text'],
                           'evidence_ids': [foreign['evidence_id']], 'limitations': []}]}

    model.ask = invent_reference
    async def run():
        with pytest.raises(ValueError, match='Public prose must cite admitted evidence'):
            await workflow.review_preserved_blocks(workflow.published_blocks(workflow.current()), {}, [])
        assert workflow._worker_source.get() is None

    asyncio.run(run())
    assert called == ['market_verify', 'market_repair_units']
