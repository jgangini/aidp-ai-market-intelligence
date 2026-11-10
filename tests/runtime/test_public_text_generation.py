"""Transient model prose never becomes durable evidence before review."""
import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from ai_market_intelligence.runtime.model import PUBLIC_TEXT_DELTA
from ai_market_intelligence.runtime.workflow import _PublicTextGeneration
from tests.runtime.test_market_intelligence_graph import market_fixture, market_run


def test_public_fragments_precede_review_and_canonical_publication_without_snapshot_growth():
    store,model,_,graph=market_fixture(('web',))
    original=model.ask
    async def ask(name,schema,payload):
        result=await original(name,schema,payload)
        sink=PUBLIC_TEXT_DELTA.get()
        if name=='market_draft':
            assert sink is not None
            for piece in ('La muestra ', 'contiene reclamaciones ', 'atribuidas.'):
                sink(piece)
                await asyncio.sleep(0)
            assert not store.read()[0]['blocks']
        elif name not in {'market_consolidate','market_summarize'}:
            assert sink is None
        return result
    model.ask=ask
    events,state=market_run(graph)
    packets=[event['_public_text'] for event in events if '_public_text' in event]
    assert [p['phase'] for p in packets]==['start','delta','delta','end']
    assert ''.join(p['text'] for p in packets if p['phase']=='delta')=='La muestra contiene reclamaciones atribuidas.'
    assert [p['delta_index'] for p in packets]==list(range(len(packets)))
    assert {p['block_id'] for p in packets}=={'source-web'}
    assert next(i for i,e in enumerate(events) if e.get('type')=='draft.upsert')>max(i for i,e in enumerate(events) if '_public_text' in e)
    saved=store.read()[0]
    assert all('_public_text' not in e for e in saved['events'])
    assert state['response']['references'] and saved['blocks']['source-web']['block_revision']==1


def test_rejected_provisional_text_is_discarded_after_generation_before_repair():
    _,model,_,graph=market_fixture(('web',))
    model.reject=True
    original=model.ask
    async def ask(name,schema,payload):
        result=await original(name,schema,payload)
        if name=='market_draft':
            PUBLIC_TEXT_DELTA.get()('Texto público todavía provisional.')
        return result
    model.ask=ask
    events,state=market_run(graph)
    packets=[e['_public_text'] for e in events if '_public_text' in e]
    assert [p['phase'] for p in packets]==['start','delta','end','discard']
    assert [p['delta_index'] for p in packets]==[0,1,2,3]
    assert not state['response']['references']


def test_model_failure_discards_partial_public_text():
    _,model,_,graph=market_fixture(('web',))
    original=model.ask
    async def ask(name,schema,payload):
        if name=='market_draft':
            PUBLIC_TEXT_DELTA.get()('Texto que no llegó a completarse.')
            raise TimeoutError('Controlled incomplete generation')
        return await original(name,schema,payload)
    model.ask=ask
    events,state=market_run(graph)
    assert [e['_public_text']['phase'] for e in events if '_public_text' in e]==['start','delta','discard']
    assert state['response']['status']=='failed'
    assert next(i for i, e in enumerate(events) if e.get('_public_text', {}).get('phase') == 'discard') < next(
        i for i, e in enumerate(events) if e.get('type') == 'run.failed')


@pytest.mark.parametrize('reject', [False, True])
def test_followup_commits_before_retiring_text_but_rejected_summary_withdraws_immediately(reject):
    from tests.runtime.test_market_intelligence_research_followup import followup_graph, saved_fixture

    graph, store, model, collector = followup_graph(saved_fixture(), reject=reject)
    original = model.ask

    async def ask(name, schema, payload):
        result = await original(name, schema, payload)
        if name == 'market_summarize':
            sink = PUBLIC_TEXT_DELTA.get()
            sink(result['text'][:20])
            await asyncio.sleep(0)
            sink(result['text'][20:])
        return result

    model.ask = ask

    async def run():
        return [event async for event in graph.astream(
            {'request_id': 'followup', 'question': 'Resume el informe anterior.'},
            {'configurable': {'thread_id': 'followup'}}, stream_mode='custom')]

    events = asyncio.run(run())
    packets = [e['_public_text'] for e in events if '_public_text' in e]
    assert {p['block_id'] for p in packets} == {'consolidated'}
    assert ''.join(p['text'] for p in packets if p['phase'] == 'delta')
    discarded = next((i for i, e in enumerate(events) if e.get('_public_text', {}).get('phase') == 'discard'), None)
    completed = next(i for i, e in enumerate(events) if e.get('type') == 'run.completed')
    if reject:
        rejected = next(i for i, e in enumerate(events) if e.get('type') == 'source.coverage'
                        and e.get('block_id') == 'report-followup')
        assert discarded is not None and discarded < rejected < completed
        assert store.read()[0]['response']['coverage']['transformation_verified'] is False
    else:
        assert discarded is None
        assert events[completed]['text'] == ''.join(p['text'] for p in packets if p['phase'] == 'delta')
        assert store.read()[0]['response']['coverage']['transformation_verified'] is True
    assert collector.calls == []
    assert all('_public_text' not in e for e in store.read()[0]['events'])


@pytest.mark.parametrize('error_type', [ValueError, TimeoutError])
def test_successful_followup_has_no_fallible_post_commit_discard(monkeypatch, error_type):
    from tests.runtime.test_market_intelligence_research_followup import followup_graph, saved_fixture

    graph, store, model, _ = followup_graph(saved_fixture())
    original_ask = model.ask
    original_packet = _PublicTextGeneration.packet

    def packet(generation, phase, text=''):
        if phase == 'discard':
            raise error_type('Controlled failed discard transport')
        return original_packet(generation, phase, text)

    monkeypatch.setattr(_PublicTextGeneration, 'packet', packet)

    async def ask(name, schema, payload):
        result = await original_ask(name, schema, payload)
        if name == 'market_summarize':
            PUBLIC_TEXT_DELTA.get()(result['text'])
        return result

    model.ask = ask

    async def run():
        return [event async for event in graph.astream(
            {'request_id': 'followup', 'question': 'Resume el informe anterior.'},
            {'configurable': {'thread_id': 'followup'}}, stream_mode='custom')]

    events = asyncio.run(run())
    value = store.read()[0]
    assert value['status'] == 'completed' and value['response']['coverage']['transformation_verified'] is True
    assert any(e.get('type') == 'run.completed' for e in events)
    assert not any(e.get('type') == 'run.failed' for e in events)


def test_source_and_consolidated_blocks_replace_their_stream_without_a_preceding_discard():
    _, model, _, graph = market_fixture(('web',))
    original = model.ask

    async def ask(name, schema, payload):
        result = await original(name, schema, payload)
        if name in {'market_draft', 'market_consolidate'}:
            PUBLIC_TEXT_DELTA.get()(result['text'])
        return result

    model.ask = ask
    events, state = market_run(graph)
    for block_id, event_type in [('source-web', 'draft.upsert'), ('consolidated', 'draft.compacted')]:
        committed = next(i for i, e in enumerate(events) if e.get('type') == event_type
                         and e.get('block_id') == block_id)
        deltas = [i for i, e in enumerate(events) if e.get('_public_text', {}).get('block_id') == block_id
                  and e['_public_text']['phase'] == 'delta']
        assert deltas and max(deltas) < committed
        assert not any(e.get('_public_text', {}).get('block_id') == block_id
                       and e['_public_text']['phase'] == 'discard' for e in events)
    assert state['response']['references']


@pytest.mark.parametrize('event_type', ['run.failed', 'run.cancelled'])
@pytest.mark.parametrize('block_id', ['source-web', 'consolidated'])
def test_failure_and_cancellation_still_withdraw_before_the_terminal_marker(event_type, block_id):
    from ai_market_intelligence.runtime.workflow import MarketWorkflow

    async def run():
        store, model, collector, _ = market_fixture(('web',))
        store.begin('fixture-request', 'A bounded question')
        workflow = MarketWorkflow(store, model, collector, enabled_sources=('web',))
        events = []
        token = workflow._worker_writer.set(events.append)
        try:
            generation = _PublicTextGeneration(workflow, block_id,
                'web' if block_id == 'source-web' else None, ['web'], events.append)
            workflow._public_texts[block_id] = generation
            generation.feed('Still provisional.')
            generation.end()
            workflow.emit(event_type, status='failed', text='Stopped safely.')
            discarded = next(i for i, e in enumerate(events) if e.get('_public_text', {}).get('phase') == 'discard')
            stopped = next(i for i, e in enumerate(events) if e.get('type') == event_type)
            assert discarded < stopped and not workflow._public_texts
        finally:
            workflow._worker_writer.reset(token)

    asyncio.run(run())


def test_failed_canonical_commit_does_not_withdraw_before_failure_cleanup(monkeypatch):
    from ai_market_intelligence.runtime.workflow import MarketWorkflow

    async def run():
        store, model, collector, _ = market_fixture(('web',))
        store.begin('fixture-request', 'A bounded question')
        workflow = MarketWorkflow(store, model, collector, enabled_sources=('web',))
        events = []
        token = workflow._worker_writer.set(events.append)
        try:
            generation = _PublicTextGeneration(workflow, 'consolidated', None, ['web'], events.append)
            workflow._public_texts['consolidated'] = generation
            generation.feed('A verified candidate pending commit.')
            generation.end()
            append = store.append

            def fail(*args, **kwargs):
                raise ValueError('Controlled failed commit')

            monkeypatch.setattr(store, 'append', fail)
            with pytest.raises(ValueError, match='Controlled failed commit'):
                workflow.emit('run.completed', text='A verified candidate pending commit.')
            assert not generation.closed
            assert not any(e.get('_public_text', {}).get('phase') == 'discard' for e in events)
            monkeypatch.setattr(store, 'append', append)
            workflow.emit('run.failed', status='failed', text='Stopped safely.')
            assert generation.closed and not workflow._public_texts
        finally:
            workflow._worker_writer.reset(token)

    asyncio.run(run())


def test_rejected_repair_withdraws_its_provisional_text_before_other_sources_finish():
    store, model, collector, _ = market_fixture(('financial_document',))
    from ai_market_intelligence.runtime.workflow import MarketWorkflow
    workflow = MarketWorkflow(store, model, collector, enabled_sources=('financial_document',))

    async def run():
        packets = []
        store.begin('repair-rejected', 'Pregunta de mercado')
        generation = _PublicTextGeneration(workflow, 'source-financial_document',
                                           'financial_document', ['financial_document'], packets.append)
        workflow._public_texts['source-financial_document'] = generation
        generation.feed('Reparación todavía provisional.')
        generation.end()

        async def rejected(state):
            return {'text': 'Reparación rechazada', 'evidence_ids': [], 'limitations': []}, False

        workflow.reviewed_draft = rejected
        await workflow.verify({'task': {'source': 'financial_document'},
            'analysis': {'context_limitations': [], 'gap': None,
                         'evidence_coverage': {'total': 0}, 'financial_facts': []}})
        assert [p['_public_text']['phase'] for p in packets] == ['start', 'delta', 'end', 'discard']
        assert not workflow._public_texts and not store.read()[0]['blocks']

    asyncio.run(run())


def test_fragment_budget_reserves_terminators_and_stays_linear():
    async def run():
        packets=[]
        owner=SimpleNamespace(_public_text_packets=0,_public_texts={},
                              current=lambda:{'request_id':'request','blocks':{}})
        for _ in range(400):
            stream=_PublicTextGeneration(owner,'source-web','web',['web'],packets.append)
            owner._public_texts['source-web']=stream
            stream.feed('x'*2048)
            stream.end()
            stream.discard()
        assert len(packets)<=1024
        assert sum(len(p['_public_text']['text']) for p in packets)<=2048*1024
        assert all(len(p['_public_text']['text'])<=2048 for p in packets)
        assert all(p['_public_text']['phase']!='end' or p['_public_text']['text']=='' for p in packets)
    asyncio.run(run())


def test_real_http_model_fragments_cross_graph_before_source_and_final_close():
    from langchain_core.messages import HumanMessage, SystemMessage
    from ai_market_intelligence.runtime.model import OpenAIModel
    from ai_market_intelligence.runtime.wire import market_stream_graph
    from tests.runtime.test_market_intelligence_api_key_model import FAKE_KEY, binding
    from tests.runtime.test_market_intelligence_public_model_stream import chunk, event

    names = ('market_draft', 'market_consolidate')
    release = {name: Event() for name in names}
    finished = {name: Event() for name in names}
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(payload)
            case = json.loads(payload['messages'][1]['content'])
            name = case['name']
            body = json.dumps(case['result'], ensure_ascii=False, separators=(',', ':'))
            first = len('{"text":"') + 25
            second = first + 75
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            for value in (chunk(role='assistant'), chunk(body[:first]), chunk(body[first:second])):
                self.wfile.write(event(value))
                self.wfile.flush()
            if not release[name].wait(8):
                return
            for value in (chunk(body[second:]), chunk(finish='stop'),
                          chunk(usage={'total_tokens': 42}), b'[DONE]'):
                self.wfile.write(event(value))
                self.wfile.flush()
            finished[name].set()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    public = OpenAIModel(binding()['services'], FAKE_KEY)
    public.endpoint = f'http://127.0.0.1:{server.server_port}/chat/completions'
    store, model, _, graph = market_fixture(('web',))
    original = model.ask

    async def ask(name, schema, payload):
        result = await original(name, schema, payload)
        if name in names:
            result = {'text': 'La muestra recuperada contiene reclamaciones atribuidas a sus fuentes; '
                      'este avance conserva el alcance y se contrastará antes de publicarse.',
                      'evidence_ids': result['evidence_ids'], 'limitations': result['limitations']}
            return await public.with_structured_output(schema, method='json_schema').ainvoke([
                SystemMessage(content='Public prose fixture'),
                HumanMessage(content=json.dumps({'name': name, 'result': result}))])
        return result

    model.ask = ask

    async def consume():
        packets = []
        deltas = {}
        async for line in market_stream_graph(graph, store,
                {'request_id': 'http-public', 'question': 'Pregunta de mercado'},
                {'configurable': {'thread_id': 'http-public'}}):
            assert line.startswith('{"protocol"') and line.endswith('\n')
            packet = json.loads(line)
            packets.append(packet)
            if packet['kind'] == 'text' and packet['phase'] == 'delta':
                block = packet['block_id']
                deltas[block] = deltas.get(block, 0) + 1
                name = 'market_draft' if block == 'source-web' else 'market_consolidate'
                if deltas[block] == 2:
                    assert not finished[name].is_set()
                    release[name].set()
        assert deltas['source-web'] >= 2 and deltas['consolidated'] >= 2
        assert packets[-1]['kind'] == 'response' and packets[-1]['response']['references']
        assert all('_public_text' not in e for e in store.read()[0]['events'])

    try:
        asyncio.run(asyncio.wait_for(consume(), 15))
        assert len(received) == 2 and all(p['stream'] is True for p in received)
    finally:
        for signal in release.values():
            signal.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
