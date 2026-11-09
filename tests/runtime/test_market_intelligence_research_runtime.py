"""The native adapter runs the portable graph with scoped storage and content-carried events."""
import asyncio
import copy
import hashlib
import importlib.util
import io
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ai_market_intelligence.installation.agent_package import agent_package
from ai_market_intelligence.runtime.wire import MarketWireDecoder, market_stream_graph, market_wire_line
from ai_market_intelligence.runtime.services import build_market_services
from ai_market_intelligence.runtime.runtime_core import Scope
from tests.runtime.test_market_intelligence_graph import MarketFixtureModel, market_evidence, market_fixture
from tests.runtime.test_market_intelligence_research_collector import MarketProviderFixture


class MarketStorageFixture:
    def __init__(self):
        self.objects={}
        self.revision=0

    def get_object(self,namespace,bucket,key):
        if key not in self.objects:
            error=RuntimeError('Not found')
            error.status=404
            raise error
        raw,etag=self.objects[key]
        data=SimpleNamespace(raw=io.BytesIO(raw),close=lambda:None)
        return SimpleNamespace(data=data,headers={'etag':etag})

    def put_object(self,namespace,bucket,key,raw,**options):
        previous=self.objects.get(key)
        if (options.get('if_none_match')=='*' and previous is not None
                or options.get('if_match') and (previous is None or previous[1]!=options['if_match'])):
            error=RuntimeError('Precondition failed')
            error.status=412
            raise error
        self.revision+=1
        self.objects[key]=(bytes(raw),str(self.revision))


class MarketNativeProviderFixture(MarketProviderFixture):
    async def observations(self,receipt,call,**kwargs):
        platform='instagram' if 'instagram' in json.dumps(call) else 'facebook'
        if 'scraperlink/' in call['actor']:
            row={'url':f'https://{platform}.com/fixturebrand','text':'Fixture brand public page',
                 'provenance':{'provider_run_id':receipt['runId'],'content_basis':'search_title_and_snippet'}}
        else:
            row={**market_evidence(platform),'url':f'https://{platform}.com/fixturebrand/posts/1',
                 'provenance':{'provider_run_id':receipt['runId'],'content_basis':'post_text'}}
        return SimpleNamespace(items=[row],coverage=None,cost=receipt['spent_microusd'])


def market_active_config():
    return {'schema_version':1,'research_enabled':True,'services':{
        'credential_name':'fixture-reference','identity_sha256':'a'*64,'candidate_revision':'b'*64,
        'region':'us-chicago-1','model_endpoint':'https://inference.example.com/',
        'model_id':'fixture-model','compartment_id':'fixture-compartment','guardrails_config':{},
        'tenant_id':'fixture-tenant','generation_id':'d27966a4-6b46-4734-a672-b648a3e7e375',
        'namespace':'fixture-namespace','bucket':'fixture-artifacts','enabled_sources':['facebook','instagram'],
        'search_context':{'country_code':'CO','language_code':'es'},'spending_mode':'unlimited',
        'runtime_profile':{'python':'3.11.13','packages':{name:'1.0.0' for name in
            ('langchain-core','langgraph','langgraph-checkpoint','langgraph-prebuilt','oci','pydantic','mcp','httpx','pypdf')}}}}


def market_native_fixture(tmp_path,monkeypatch,*,config=None):
    from tests.runtime.packaged import load_candidate
    entry=load_candidate(tmp_path,monkeypatch,market_active_config() if config is None else config)
    module=sys.modules['core.native']
    calls=[]
    helper=ModuleType('aidputils.agents.toolkit.agent_helper')
    def pre_invoke_setup(**kwargs):
        calls.append('pre_invoke_setup')
        return {'configurable':{'thread_id':kwargs.get('thread_id'),'checkpoint_ns':'default'}}
    def pre_tool_setup(**kwargs):
        calls.append(('pre_tool_setup',kwargs.get('thread_id')))
        return 'fixture-context-token'
    def post_tool_setup(token,**kwargs):
        calls.append(('post_tool_setup',token))
    helper.pre_invoke_setup=pre_invoke_setup
    helper.pre_tool_setup=pre_tool_setup
    helper.post_tool_setup=post_tool_setup
    monkeypatch.setitem(sys.modules,'aidputils.agents.toolkit.agent_helper',helper)
    checkpoints=InMemorySaver()
    monkeypatch.setattr(module,'native_memory',lambda generation,*args,**kwargs:
        sys.modules['core.memory'].GenerationMemory(checkpoints,generation,request_id=kwargs.get('request_id')))
    collector=module.MarketCollector
    monkeypatch.setattr(module,'MarketCollector',lambda *args,**kwargs:collector(*args,**kwargs,poll_attempts=1,poll_seconds=0))
    adapter=entry.MarketIntelligenceAgent()
    adapter.setup()
    runtime=adapter.runtime
    storage=MarketStorageFixture()
    model=MarketFixtureModel(('facebook','instagram'))
    provider=MarketNativeProviderFixture()
    runtime.services={'storage':storage,'model':model,'provider':provider,'mcp':None}
    return runtime,storage,model,provider,calls


def market_native_turn(runtime,question='Â¿QuÃ© reclamaciones aparecen en la muestra?',request_id='request-1',subject='fixture-user',session='fixture-session',command=None):
    async def run():
        messages=await runtime.invoke(json.dumps(command if command is not None else {'request_id':request_id,'question':question}),
            subject_id=subject,thread_id=session,trace_id='fixture-trace',stream=True)
        decoder=MarketWireDecoder()
        packets=[]
        async for message in messages:
            for character in message.content:
                packets.extend(decoder.feed(character))
        decoder.finish()
        return packets
    return asyncio.run(run())


def test_native_replay_restores_public_reference_manifests_without_model_or_capture_calls(tmp_path,monkeypatch):
    runtime,_,model,provider,_=market_native_fixture(tmp_path,monkeypatch)
    first=market_native_turn(runtime)
    before=(len(model.calls),len(provider.starts))
    replay=market_native_turn(runtime,command={'action':'replay','request_id':'request-1','after_sequence':0})
    packet=replay[-1]
    assert packet['kind']=='snapshot' and packet['references']
    assert (len(model.calls),len(provider.starts))==before
    expected={row['evidence_id']:row['url'] for row in first[-1]['response']['references']}
    assert all(expected[row['evidence_id']]==row['url'] for row in packet['references'])
    assert all('source' in row and not {'text','owner_id','scope','receipt_id'}&row.keys() for row in packet['references'])


def test_native_market_full_graph_preserves_source_role_artifacts_and_cached_delivery(tmp_path,monkeypatch):
    runtime,storage,model,provider,calls=market_native_fixture(tmp_path,monkeypatch)
    packets=market_native_turn(runtime)
    drafts=[packet['event'] for packet in packets if packet['kind']=='event' and packet['event']['type']=='draft.upsert']
    assert [event['source_key'] for event in drafts]==['facebook','instagram']
    assert all(event['role_key']=='market_analysis' for event in drafts)
    assert packets[-1]['response']['status']=='complete'

    from ai_market_intelligence.installation.stream import MarketGatewayStream
    from tests.runtime.test_market_intelligence_research_gateway import frame
    gateway=MarketGatewayStream()
    forwarded=[]
    for packet in packets:
        raw=frame(json.dumps(packet,ensure_ascii=False,separators=(',',':'))+'\n')
        arrived=[]
        for offset in range(0,len(raw),13):
            arrived.extend(gateway.feed(raw[offset:offset+13]))
        assert arrived==[packet]
        forwarded.extend(arrived)
    gateway.feed(b'data: [DONE]\n\n')
    gateway.finish()
    assert forwarded==packets

    assert packets[-1]['response']['artifacts'][0]['kind']=='bar'
    assert len(provider.starts)==3 and calls[0]=='pre_invoke_setup'
    assert [call['actor'] for call in provider.starts]==['apify/facebook-search-scraper','scraperlink/google-search-results-serp-scraper','apify/instagram-post-scraper']
    assert calls[-1]==('post_tool_setup','fixture-context-token')
    previous_calls=list(model.calls)
    repeated=market_native_turn(runtime)
    assert len(provider.starts)==3 and model.calls==previous_calls
    assert repeated[-1]['response']==packets[-1]['response']
    binding = market_active_config()['services']
    owner = hashlib.sha256(b'fixture-user').hexdigest()
    prefix = Scope(binding['tenant_id'], owner, binding['generation_id']).prefix
    assert all(key.startswith(prefix) and key.removeprefix(prefix).startswith(('research/', 'current/sessions/'))
               for key in storage.objects)
    assert any(key.startswith(prefix + 'research/') for key in storage.objects)
    assert any(key.startswith(prefix + 'current/sessions/') for key in storage.objects)


def test_compacted_replay_preserves_source_activity_without_turning_it_into_findings():
    store,model,collector,graph=market_fixture(('facebook','instagram'),event_limit=2)
    from tests.runtime.test_market_intelligence_graph import market_run
    events,state=market_run(graph)
    from ai_market_intelligence.runtime.wire import market_replay_packet
    packet=json.loads(market_replay_packet(store,'request',0))
    activity=packet['replay']['snapshot']['activity']
    assert set(activity)=={'facebook','instagram'}
    for source,item in activity.items():
        assert item['source_keys']==[source] and item['coverage']['phase']=='retrieved'
        assert item['evidence_ids']==[] and item['block_id'] is None
    assert all(block['type']!='source.coverage' for block in state['response']['blocks'])


def test_invalid_activity_source_cannot_be_committed():
    store,model,collector,graph=market_fixture()
    store.begin('request','A fixture question')
    before=store.read()
    with pytest.raises(ValueError):
        store.append('source.coverage',coverage={'source':'untrusted-label','phase':'retrieving'})
    assert store.read()==before


def test_native_market_clarification_context_is_retained_without_any_capture(tmp_path,monkeypatch):
    runtime,storage,model,provider,calls=market_native_fixture(tmp_path,monkeypatch)
    model.clarification='Â¿En quÃ© fuentes quieres buscar?'
    first=market_native_turn(runtime,question='Â¿QuÃ© se dice de la marca?')
    assert first[-1]['response']['status']=='needs_clarification' and provider.starts==[]
    original=model.ask
    context=[]
    async def observe_context(name,schema,payload):
        if name=='market_understand':
            context.append(copy.deepcopy(payload['conversation_context']))
        return await original(name,schema,payload)
    model.ask=observe_context
    model.clarification=None
    result=market_native_turn(runtime,question='En ambas redes.',request_id='request-2')
    assert result[-1]['response']['status']=='complete'
    assert context[0]['question']=='Â¿QuÃ© se dice de la marca?'
    assert context[0]['brief']['clarification']=='Â¿En quÃ© fuentes quieres buscar?'


def test_native_market_two_authenticated_owners_never_share_snapshots(tmp_path,monkeypatch):
    runtime,storage,model,provider,calls=market_native_fixture(tmp_path,monkeypatch)
    first=market_native_turn(runtime,subject='first-user')
    second=market_native_turn(runtime,subject='second-user')
    assert len(provider.starts)==6
    assert first[-1]['response']['references']!=second[-1]['response']['references']
    sessions=[key for key in storage.objects if '/sessions/' in key]
    assert len(sessions)==2


def test_native_market_same_owner_and_explicit_request_id_are_isolated_by_native_session(tmp_path,monkeypatch):
    runtime,storage,model,provider,calls=market_native_fixture(tmp_path,monkeypatch)
    first=market_native_turn(runtime,session='first-session')
    second=market_native_turn(runtime,session='second-session')
    assert len(provider.starts)==6
    assert first[-1]['response']['references']!=second[-1]['response']['references']
    events=[packet['event'] for packet in first+second if packet['kind']=='event' and packet['event']['type']=='run.completed']
    assert len({event['run_id'] for event in events})==2


def test_market_wire_rejects_incomplete_or_foreign_protocol_messages():
    decoder=MarketWireDecoder()
    decoder.feed('{"protocol":')
    with pytest.raises(ValueError,match='ended inside'):
        decoder.finish()
    with pytest.raises(ValueError,match='Unexpected native wire'):
        MarketWireDecoder().feed('{"protocol":"other","version":"2.0","kind":"response"}\n')


def test_market_wire_accepts_grouped_packets_with_individual_size_bounds():
    line=market_wire_line('response',response={'markdown':'x'*1100000})
    decoder=MarketWireDecoder()
    assert len(decoder.feed(line+line))==2
    decoder.finish()


def test_native_stream_can_close_at_response_without_leaving_a_reserved_session(tmp_path,monkeypatch):
    runtime,storage,model,provider,calls=market_native_fixture(tmp_path,monkeypatch)
    async def close_at_response():
        messages=await runtime.invoke(json.dumps({'request_id':'request-1','question':'First question'}),
            subject_id='fixture-user',thread_id='fixture-session',stream=True)
        async for message in messages:
            if json.loads(message.content)['kind']=='response':
                await messages.aclose()
                return
    asyncio.run(close_at_response())
    result=market_native_turn(runtime,question='Another question',request_id='request-2')
    assert result[-1]['response']['status']=='complete' and len(provider.starts)==6


def test_native_cancel_closes_pending_request_and_releases_session_preserving_receipts(tmp_path,monkeypatch):
    runtime,storage,model,provider,calls=market_native_fixture(tmp_path,monkeypatch)
    provider.pending=True
    pending=market_native_turn(runtime)
    assert pending[-1]['response']['status']=='pending'
    async def cancel():
        messages=await runtime.invoke(json.dumps({'action':'cancel','request_id':'request-1'}),
            subject_id='fixture-user',thread_id='fixture-session',stream=True)
        return [json.loads(message.content) async for message in messages]
    cancelled=asyncio.run(cancel())
    assert cancelled[-1]['response']['status']=='cancelled'
    old_starts=len(provider.starts)
    provider.pending=False
    result=market_native_turn(runtime,question='Another question',request_id='request-2')
    assert result[-1]['response']['status']=='complete' and len(provider.starts)>old_starts
    states=[json.loads(raw) for raw,_ in storage.objects.values()]
    old=[state for state in states if state.get('request_id')=='request-1']
    assert old[0]['status']=='cancelled' and old[0]['collections']
    operations=[state for state in states if 'operations' in state]
    assert any(row['receipt'] for state in operations for row in state['operations'].values() if row['kind']=='apify')


def test_market_runtime_mismatch_fails_before_credentials_or_remote_services():
    config=market_active_config()['services']
    with pytest.raises(ValueError,match='native runtime changed'):
        build_market_services(config)


@pytest.mark.parametrize('name,version',[
    ('langgraph','0.6.7'),('langgraph','1.0.1'),('langgraph',None),
    ('langgraph-checkpoint','3.0.1'),('langgraph-prebuilt','1.0.13'),
])
def test_runtime_outside_selected_managed_profile_cannot_initialize_research(monkeypatch,name,version):
    from ai_market_intelligence.runtime import services
    config=market_active_config()['services']
    config['runtime_profile']['packages'].update(services.TARGET_GRAPH_PACKAGES)
    config['runtime_profile']['packages'][name]=version
    monkeypatch.setattr(services,'runtime_versions',lambda *args:config['runtime_profile'])
    with pytest.raises(ValueError,match='selected managed LangGraph profile'):
        build_market_services(config)


def test_market_wire_heartbeats_do_not_publish_private_model_analysis():
    store,model,collector,graph=market_fixture()
    original=collector.collect
    async def delayed(**kwargs):
        await asyncio.sleep(0.025)
        return await original(**kwargs)
    collector.collect=delayed
    async def run():
        stream=market_stream_graph(graph,store,{'request_id':'request','question':'A fixture question'},
                              {'configurable':{'thread_id':'request'}},heartbeat_seconds=0.005)
        return [json.loads(line) async for line in stream]
    packets=asyncio.run(run())
    assert any(packet['kind']=='heartbeat' for packet in packets)
    assert all(set(packet)=={'protocol','version','kind','request_id'}
               for packet in packets if packet['kind']=='heartbeat')
    assert packets[-1]['kind']=='response'


def test_source_progress_streams_before_slow_collection_and_keeps_source_order():
    store,model,collector,graph=market_fixture(('facebook','instagram'))
    original=collector.collect

    async def run():
        entered=asyncio.Event()
        release=asyncio.Event()
        async def delayed(**kwargs):
            if kwargs['task']['source']=='facebook':
                entered.set()
                await release.wait()
            return await original(**kwargs)
        collector.collect=delayed
        stream=market_stream_graph(graph,store,{'request_id':'request','question':'A fixture question'},
                                  {'configurable':{'thread_id':'request'}},heartbeat_seconds=0.01)
        packets=[]
        try:
            async def first_event():
                async for line in stream:
                    packet=json.loads(line)
                    packets.append(packet)
                    if packet['kind']=='event':
                        return packet['event']
                raise AssertionError('No source activity was streamed')
            first=await asyncio.wait_for(first_event(),timeout=1)
            await asyncio.wait_for(entered.wait(),timeout=1)
            assert not release.is_set()
            assert not any(call['task']['source']=='facebook' for call in collector.calls)
            assert first['type']=='source.coverage'
            assert first['coverage']['phase']=='retrieving'
            assert first['source_key']=='facebook' and first['source_keys']==['facebook']
            assert first['role_key']=='market_analysis' and first['evidence_ids']==[]
            release.set()
            async for line in stream:
                packets.append(json.loads(line))
        finally:
            release.set()
            await stream.aclose()
        return packets

    packets=asyncio.run(run())
    events=[packet['event'] for packet in packets if packet['kind']=='event']
    assert [item['sequence'] for item in events]==list(range(1,len(events)+1))
    starts=[item['source_key'] for item in events
            if item['type']=='source.coverage' and item['coverage'].get('phase')=='retrieving']
    assert len(starts)==2 and set(starts)=={'facebook','instagram'}
    for source in starts:
        start=next(i for i,item in enumerate(events) if item['source_key']==source
                   and item['type']=='source.coverage' and item['coverage'].get('phase')=='retrieving')
        retrieved=next(i for i,item in enumerate(events) if item['source_key']==source
                       and item['type']=='source.coverage' and item['coverage'].get('phase')=='retrieved')
        draft=next(i for i,item in enumerate(events) if source in item['source_keys']
                   and item['type']=='draft.upsert')
        assert start < retrieved < draft
    assert packets[-1]['kind']=='response'
    assert packets[-1]['response']['status']=='complete'


def test_completed_invalid_decision_releases_the_session_for_a_new_request(tmp_path,monkeypatch):
    runtime,storage,model,provider,_=market_native_fixture(tmp_path,monkeypatch)
    original=model.ask
    async def invalid(name,schema,payload):
        result=await original(name,schema,payload)
        if name=='market_analyze':
            result['findings'][0]['supports']=['unowned-evidence']
        return result
    model.ask=invalid
    first=market_native_turn(runtime)
    assert first[-1]['response']['status']=='failed'
    model.ask=original
    second=market_native_turn(runtime,question='Otra investigaciÃ³n.',request_id='second')
    assert second[-1]['response']['status'] in {'complete','partial'}
    session=next(json.loads(raw) for key,(raw,_) in storage.objects.items() if '/sessions/' in key)
    assert session['requests']['request-1']['finished'] and session['active'] is None


def test_uncertain_model_failure_does_not_release_the_session_or_start_another_request(tmp_path,monkeypatch):
    runtime,storage,model,provider,_=market_native_fixture(tmp_path,monkeypatch)
    original=model.ask
    async def timeout(name,schema,payload):
        if name=='market_analyze':
            raise TimeoutError('Simulated uncertain submitted inference')
        return await original(name,schema,payload)
    model.ask=timeout
    market_native_turn(runtime)
    before=list(model.calls)
    second=market_native_turn(runtime,question='Otra investigaciÃ³n.',request_id='second')
    assert second[-1]['response']['status']=='pending' and model.calls==before
    session=next(json.loads(raw) for key,(raw,_) in storage.objects.items() if '/sessions/' in key)
    assert session['active']['request_id']=='request-1'


def test_native_paused_model_config_is_read_without_constructing_paid_services(tmp_path,monkeypatch):
    config={'schema_version':1,'research_enabled':False,'services':{
        'model_id':'selected-model','region':'us-chicago-1','compartment_id':'compartment'}}
    runtime,storage,model,provider,calls=market_native_fixture(tmp_path,monkeypatch,config=config)
    module=sys.modules[type(runtime).__module__]
    def forbidden(*args,**kwargs):
        pytest.fail('Paused inspection must not initialize memory, model or Apify services')
    monkeypatch.setattr(module,'build_market_services',forbidden)
    monkeypatch.setattr(module,'native_memory',forbidden)
    assert runtime.config==config
    async def probe_stream():
        return [message async for message in await runtime.invoke('inspect_runtime',subject_id='fixture-user',thread_id='fixture-session')]
    probe=json.loads(asyncio.run(probe_stream())[0].content)
    assert probe['research_enabled'] is False and probe['model_calls']==probe['apify_starts']==0
    assert not provider.starts


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('structured', [False, True])
def test_paused_d1_query_returns_explanation_without_research_effects(tmp_path,monkeypatch,stream,structured):
    config={'schema_version':1,'research_enabled':False}
    runtime,storage,model,provider,calls=market_native_fixture(tmp_path,monkeypatch,config=config)
    module=sys.modules[type(runtime).__module__]
    def forbidden(*args,**kwargs):
        pytest.fail('Paused chat must not construct research services or memory')
    monkeypatch.setattr(module,'build_market_services',forbidden)
    monkeypatch.setattr(module,'native_memory',forbidden)
    runtime.services=None
    text='que se dice de d1 en colombia'
    question=json.dumps({'request_id':'d1-paused','question':text}) if structured else text
    async def query():
        result=await runtime.invoke(question,subject_id='fixture-user',thread_id='fixture-session',trace_id='fixture-trace',stream=stream)
        assert hasattr(result,'__aiter__')
        return [message async for message in result]
    messages=asyncio.run(query())
    if not structured:
        assert len(messages)==1
        text=messages[0].content
        assert 'pausada' in text and 'TEST' in text and 'No se consultaron fuentes' in text
        assert '"protocol"' not in text and '"markdown"' not in text
        assert runtime.services is None and storage.objects=={} and model.calls==[] and provider.starts==[]
        assert calls==['pre_invoke_setup']
        return
    decoder=MarketWireDecoder()
    packets=decoder.feed(messages[0].content)
    decoder.finish()
    assert len(messages)==len(packets)==1 and packets[0]['kind']=='response'
    assert packets[0]['request_id']==module.market_chat_input(question,'fixture-session','fixture-trace')['request_id']
    from ai_market_intelligence.installation.stream import MarketGatewayStream
    gateway=MarketGatewayStream()
    assert gateway.public_text(messages[0].content)==packets and gateway.has_response
    response=packets[0]['response']
    assert response['status']=='paused' and 'pausada' in response['markdown'] and 'TEST' in response['markdown']
    assert response['research_enabled'] is False and response['model_calls']==response['apify_starts']==0
    assert response['references']==response['artifacts']==[]
    assert runtime.services is None and storage.objects=={} and model.calls==[] and provider.starts==[]
    assert calls==['pre_invoke_setup']


def test_paused_chat_still_requires_authenticated_subject(tmp_path,monkeypatch):
    runtime,*_=market_native_fixture(tmp_path,monkeypatch,config={'schema_version':1,'research_enabled':False})
    with pytest.raises(ValueError,match='authenticated AIDP subject'):
        asyncio.run(runtime.invoke('que se dice de d1 en colombia',thread_id='fixture-session'))


@pytest.mark.parametrize('stream',[False,True])
def test_ordinary_chat_keeps_the_verified_final_answer_and_links_without_wire_packets(tmp_path,monkeypatch,stream):
    runtime,_,model,provider,_=market_native_fixture(tmp_path,monkeypatch)
    question='Que se dice de la marca'
    async def query():
        result=await runtime.invoke(question,subject_id='fixture-user',thread_id='fixture-session',
                                    trace_id='fixture-trace',stream=stream)
        assert hasattr(result,'__aiter__')
        return [message async for message in result]
    messages=asyncio.run(query())
    before=(len(model.calls),len(provider.starts))
    module=sys.modules[type(runtime).__module__]
    request=module.market_chat_input(question,'fixture-session','fixture-trace')
    saved=market_native_turn(runtime,command={'action':'replay','request_id':request['request_id'],'after_sequence':0})[-1]
    final=messages[-1].content
    assert saved['response']['markdown'] in final and saved['response']['references']
    assert '**Fuentes**' in final
    assert all(row['url'] in final for row in saved['response']['references'])
    assert not any('"protocol"' in message.content for message in messages)
    if stream:
        assert '**facebook**' in messages[0].content
        assert any('**instagram**' in message.content for message in messages[:-1])
    assert (len(model.calls),len(provider.starts))==before


def test_chat_projection_keeps_heartbeats_and_closes_the_underlying_stream(tmp_path,monkeypatch):
    runtime,*_=market_native_fixture(tmp_path,monkeypatch)
    from langchain_core.messages import AIMessage
    async def run():
        closed=[]
        async def source():
            try:
                yield AIMessage(content=market_wire_line('heartbeat',request_id='fixture'))
                await asyncio.Event().wait()
            finally:
                closed.append(True)
        stream=runtime.chat_messages(source())
        assert (await asyncio.wait_for(anext(stream),timeout=1)).content=='\n'
        await stream.aclose()
        assert closed==[True]
    asyncio.run(run())


def test_chat_projection_preserves_artifact_units_and_escapes_markdown_cells():
    from ai_market_intelligence.runtime.wire import market_chat_text
    text=market_chat_text({'kind':'response','response':{
        'markdown':'Informe verificado','references':[],
        'artifacts':[{'title':'Datos','x_label':'Periodo','y_label':'Ingresos','unit':'currency','currency':'USD',
                      'caption':'Muestra limitada','rows':[{'period':'2026','value':'10|20','note':'<script> [link](javascript:x)',
                                                           'evidence_ids':['internal-evidence']}]}]}})
    assert 'USD' in text and 'currency' in text and 'Muestra limitada' in text
    assert '10\\|20' in text and '&lt;script&gt;' in text and '\\[link\\]' in text
    assert 'internal-evidence' not in text and '<script>' not in text


@pytest.mark.parametrize('gate_actor,expected,started',[
    ('apify/facebook-search-scraper','**facebook**',0),
    ('scraperlink/google-search-results-serp-scraper','Avance de facebook',1),
])
def test_native_plain_chat_streams_before_the_blocked_tool_completes(tmp_path,monkeypatch,gate_actor,expected,started):
    runtime,_,model,provider,_=market_native_fixture(tmp_path,monkeypatch)
    original=provider.start
    async def run():
        entered=asyncio.Event()
        release=asyncio.Event()
        received=asyncio.Event()
        async def delayed(call):
            if call['actor']==gate_actor:
                entered.set()
                await release.wait()
            return await original(call)
        provider.start=delayed
        stream=await runtime.invoke('Que se dice de la marca',subject_id='fixture-user',
                                    thread_id='fixture-session',trace_id='fixture-stream',stream=False)
        messages=[]
        async def consume():
            async for message in stream:
                messages.append(message)
                if expected in message.content:
                    received.set()
        reader=asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(),timeout=2)
            await asyncio.wait_for(received.wait(),timeout=2)
            assert not release.is_set()
            assert all(call['actor']!=gate_actor for call in provider.starts)
            if started:
                assert any(call['actor']=='apify/facebook-search-scraper' for call in provider.starts)
                assert 'market_verify' in model.calls
                assert any('sujeto a revisión final' in message.content for message in messages)
            release.set()
            await asyncio.wait_for(reader,timeout=5)
        finally:
            release.set()
            if not reader.done(): reader.cancel()
            await asyncio.gather(reader,return_exceptions=True)
            await stream.aclose()
        return messages
    messages=asyncio.run(run())
    assert any('Avance de instagram' in message.content for message in messages[:-1])
    assert '**Fuentes**' in messages[-1].content
    assert all('"protocol"' not in message.content for message in messages)


@pytest.mark.parametrize('status,label',[('provisional','Corrección de'),('failed','Retirada de')])
def test_chat_correction_explicitly_supersedes_the_prior_source_capsule(status,label):
    from ai_market_intelligence.runtime.wire import market_chat_text
    text=market_chat_text({'kind':'event','event':{'type':'draft.corrected','source_key':'instagram',
        'status':status,'text':'Sólo queda el hallazgo contrastado.','limitations':[]},'references':[]})
    assert label+' instagram' in text
    assert 'anterior' in text and 'Sólo queda el hallazgo contrastado.' in text


def test_chat_reused_multisource_summary_preserves_attribution():
    from ai_market_intelligence.runtime.state import market_attribution
    from ai_market_intelligence.runtime.wire import market_chat_text
    attribution=market_attribution({
        'fb':{'source':'facebook','discovery_channel':'social_search'},
        'ig':{'source':'instagram','discovery_channel':'web_search'},
    },['fb','ig'])
    text=market_chat_text({'kind':'event','event':{
        'type':'draft.upsert',**attribution,'status':'provisional',
        'text':'Resumen de dos fuentes.','limitations':[]},'references':[]})
    assert 'Avance de facebook, instagram' in text
    assert 'None' not in text
