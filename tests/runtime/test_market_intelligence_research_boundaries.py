"""Current source authority, exact versions, cancellation and schema-free native probe."""
import asyncio
import copy
import importlib.util
import json
import sys
from types import ModuleType

import pytest

from ai_market_intelligence.runtime.connectors import compile_call
from ai_market_intelligence.runtime.runtime_versions import RUNTIME_DISTRIBUTIONS, exact_runtime_comparison
from ai_market_intelligence.runtime.social import social_observations, social_url
from ai_market_intelligence.installation.agent_package import agent_package
from tests.runtime.test_market_intelligence_graph import market_fixture, market_run, market_evidence


Market_WINDOW={'start':'2026-10-26T00:00:00+00:00','end':'2026-11-02T23:59:59+00:00'}


def social_call(platform,urls=None):
    urls=urls or ['https://'+platform+'.com/fixturebrand']
    args={'query':'','urls':urls,'max_items':10,'sort':'Latest'}
    return compile_call(platform+'_posts',args,Market_WINDOW,None,approved_urls=urls,unlimited=True)


def test_social_compilers_use_reviewed_actors_and_keep_unlimited_spend_explicit():
    facebook,instagram=social_call('facebook'),social_call('instagram')
    assert facebook['actor']=='apify/facebook-posts-scraper' and facebook['callOptions']['build']=='0.0.408'
    assert instagram['actor']=='apify/instagram-post-scraper' and instagram['callOptions']['build']=='0.0.650'
    assert 'maxTotalChargeUsd' not in facebook['callOptions'] and 'maxTotalChargeUsd' not in instagram['callOptions']
    assert facebook['input']['captionText'] is False
    assert instagram['input']['dataDetailLevel']=='basicData' and instagram['input']['includeMusicData'] is False
    assert facebook['input']['onlyPostsNewerThan']=='2026-10-26T00:00:00Z'
    assert facebook['input']['onlyPostsOlderThan']=='2026-11-02T23:59:59Z'
    assert instagram['input']['onlyPostsNewerThan']=='2026-10-26T00:00:00Z'
    with pytest.raises(ValueError,match='approved provider reservation'):
        compile_call('facebook_posts',{'query':'','urls':['https://facebook.com/fixturebrand'],'max_items':10,'sort':'Latest'},Market_WINDOW,None)


def test_instagram_work_limit_and_parser_do_not_depend_on_a_billing_item_cap():
    targets=['https://instagram.com/brand'+str(i) for i in range(3)]
    call=social_call('instagram',targets)
    assert 'maxItems' not in call['callOptions'] and call['input']['resultsLimit']==3
    assert call['input']['resultsLimit']*len(call['input']['username'])<=10
    rows=[{'url':target+'/posts/1','inputUrl':target,'caption':'Public fixture',
           'timestamp':'2026-11-01T12:00:00Z'} for target in targets]
    assert len(social_observations(rows,{'runId':'fixture-receipt'},call))==3
    with pytest.raises(ValueError,match='total result limit'):
        social_observations(rows*4,{'runId':'fixture-receipt'},call)


def test_world_bank_work_limit_and_parser_do_not_depend_on_a_billing_item_cap():
    from tests.runtime.test_market_intelligence_finance import WORLD, SCOPE, world_row
    from ai_market_intelligence.runtime.provider import _financial_result
    call=compile_call('world_bank',{'query':'','urls':[],'max_items':3,'sort':'Latest','financial':WORLD},
        Market_WINDOW,None,unlimited=True,approved_finance=SCOPE)
    assert 'maxItems' not in call['callOptions'] and call['input']['maxResults']==3
    result=_financial_result([world_row()],{'runId':'fixture-receipt','spent_microusd':None},call)
    assert len(result.items)==1 and result.cost is None
    with pytest.raises(ValueError,match='row limit'):
        _financial_result([world_row()]*4,{'runId':'fixture-receipt','spent_microusd':None},call)


def test_facebook_permalink_identity_is_not_lost_or_interchangeable():
    first='https://facebook.com/permalink.php?story_fbid=123&id=456'
    second='https://facebook.com/permalink.php?story_fbid=789&id=456'
    assert social_url(first,'facebook')!=social_url(second,'facebook')
    with pytest.raises(ValueError,match='explicit authority'):
        compile_call('facebook_posts',{'query':'','urls':[second],'max_items':10,'sort':'Latest'},Market_WINDOW,50000,approved_urls=[first])
    with pytest.raises(ValueError,match='unreviewed query'):
        social_url('https://facebook.com/permalink.php?story_fbid=123&id=456&id=999','facebook')


@pytest.mark.parametrize('platform',['facebook','instagram'])
def test_social_topic_query_and_unapproved_profile_are_rejected(platform):
    target='https://'+platform+'.com/fixturebrand'
    with pytest.raises(ValueError,match='global keyword search'):
        compile_call(platform+'_posts',{'query':'brand complaints','urls':[target],'max_items':10,'sort':'Latest'},Market_WINDOW,50000,approved_urls=[target])
    with pytest.raises(ValueError,match='explicit authority'):
        compile_call(platform+'_posts',{'query':'','urls':[target],'max_items':10,'sort':'Latest'},Market_WINDOW,50000,approved_urls=[])


@pytest.mark.parametrize('platform',['facebook','instagram'])
def test_social_posts_preserve_text_dates_unknown_metrics_and_receipt_identity(platform):
    call=social_call(platform)
    row={'url':'https://'+platform+'.com/fixturebrand/posts/1','inputUrl':'https://'+platform+'.com/fixturebrand',
         'time':'2026-11-01T12:00:00Z','timestamp':'2026-11-01T12:00:00Z',
         'text':'Una opiniÃ³n atribuida, fixture.','caption':'Una opiniÃ³n atribuida, fixture.','likesCount':None,'commentsCount':2}
    output=social_observations([row],{'runId':'fixture-receipt'},call)
    assert output[0]['text']==row['text'] and output[0]['metrics']['likes'] is None
    assert output[0]['provenance']['provider_run_id']=='fixture-receipt'
    assert output[0]['provenance']['requested_url']=='https://'+platform+'.com/fixturebrand'
    assert output[0]['provenance']['comments_retrieved'] is False
    assert output[0]['provenance']['global_topic_search'] is False
    with pytest.raises(ValueError,match='provider error'):
        social_observations([{**row,'error':'private_profile'}],{'runId':'fixture-receipt'},call)
    with pytest.raises(ValueError,match='admitted public destination'):
        social_observations([{**row,'inputUrl':'https://'+platform+'.com/anotherbrand'}],{'runId':'fixture-receipt'},call)


def test_instagram_collaboration_requires_a_recorded_coauthor_matching_the_requested_account():
    call=social_call('instagram')
    row={'url':'https://instagram.com/p/shared-post','ownerUsername':'anotherpublisher',
         'caption':'A collaborative public post.','timestamp':'2026-11-01T12:00:00Z',
         'coauthorProducers':[{'username':'fixturebrand'}]}
    parsed=social_observations([row],{'runId':'receipt'},call)[0]
    assert parsed['provenance']['requested_url']=='https://instagram.com/fixturebrand'
    assert parsed['provenance']['author_url']=='https://instagram.com/anotherpublisher'
    assert parsed['provenance']['source_relation']=='collaboration'
    for coauthors in ([],[{'username':'unrelated'}],[{'username':'fixturebrand/../unrelated'}]):
        with pytest.raises(ValueError,match='admitted public destination'):
            social_observations([{**row,'coauthorProducers':coauthors}],{'runId':'receipt'},call)


def test_instagram_known_unavailable_profile_is_not_invalid_dataset_and_preserves_other_posts():
    from ai_market_intelligence.runtime.provider import _social_result
    call=social_call('instagram',['https://instagram.com/fixturebrand','https://instagram.com/otherbrand'])
    error={'url':'https://instagram.com/fixturebrand','username':'fixturebrand',
           'error':'not_found','errorDescription':'Profile does not exist'}
    empty=_social_result([error],{'runId':'receipt','spent_microusd':500},call)
    assert empty.items==[] and empty.cost==500
    assert empty.coverage['provider_result_error']=='instagram_destination_unavailable'
    row={'url':'https://instagram.com/p/valid-post','inputUrl':'https://instagram.com/otherbrand',
         'caption':'A public caption.','timestamp':'2026-11-01T12:00:00Z'}
    mixed=_social_result([error,row],{'runId':'receipt','spent_microusd':500},call)
    assert len(mixed.items)==1 and mixed.items[0]['text']==row['caption']
    assert 'provider_result_error' not in mixed.coverage
    assert mixed.coverage['unavailable_social_destinations']==[error['url']]
    for invalid in ({**error,'url':'https://instagram.com/foreign'},
                    {**error,'username':'foreign'}, {**error,'error':'unknown'},
                    {key:value for key,value in error.items() if key!='username'}):
        with pytest.raises(ValueError):_social_result([invalid],{'runId':'receipt'},call)
    with pytest.raises(ValueError):_social_result([error]*11,{'runId':'receipt'},call)


def test_exact_runtime_comparison_rejects_python_patch_drift_and_unknown_packages():
    observed={'python':'3.11.13','packages':{name:'1.0.0' for name in RUNTIME_DISTRIBUTIONS}}
    assert exact_runtime_comparison(observed,copy.deepcopy(observed))['exact_match']
    native=copy.deepcopy(observed)
    native['python']='3.11.11'
    assert not exact_runtime_comparison(observed,native)['exact_match']
    native=copy.deepcopy(observed)
    native['packages']['oci']=None
    assert exact_runtime_comparison(observed,native)['differences']=={'oci':'unknown'}


@pytest.mark.parametrize('during',['collect','model'])
def test_cancellation_during_external_wait_keeps_receipts_and_prevents_new_public_content(during):
    store,model,collector,graph=market_fixture()
    if during=='collect':
        original=collector.collect
        async def cancel_after_capture(**arguments):
            result=await original(**arguments)
            store.save(cancelled=True)
            return result
        collector.collect=cancel_after_capture
    else:
        original=model.ask
        async def cancel_during_model(name,schema,payload):
            result=await original(name,schema,payload)
            if name=='market_draft':
                store.save(cancelled=True)
            return result
        model.ask=cancel_during_model
    events,state=market_run(graph)
    assert state['response']['status']=='cancelled'
    assert not any(event['type'].startswith('draft.') or event['type']=='run.completed' for event in events)
    assert events[-1]['type']=='run.cancelled'
    assert store.read()[0]['collections'] and len(collector.calls)==1


def test_native_market_probe_has_no_business_schema_or_sql_and_reports_actual_versions(tmp_path,monkeypatch):
    from tests.runtime.packaged import load_candidate
    module=load_candidate(tmp_path,monkeypatch,{'schema_version':1,'research_enabled':False})
    assert not any(hasattr(module,name) for name in ('Records','Journal','SCHEMA_DEFINITION','SQLTool'))
    assert sys.modules['core.workflow'].MarketWorkflow.__module__ == 'core.workflow'
    helper=ModuleType('aidputils.agents.toolkit.agent_helper')
    calls=[]
    def pre_invoke_setup(**kwargs):
        calls.append('pre_invoke_setup')
        return {'configurable':{'thread_id':'fixture-session'}}
    helper.pre_invoke_setup=pre_invoke_setup
    monkeypatch.setitem(sys.modules,'aidputils.agents.toolkit.agent_helper',helper)
    runtime=module.MarketIntelligenceAgent()
    runtime.setup()
    async def probe_stream():
        return [message async for message in await runtime.invoke('inspect_runtime',subject_id='fixture-user')]
    result=json.loads(asyncio.run(probe_stream())[0].content)
    assert calls==['pre_invoke_setup'] and result['model_calls']==result['apify_starts']==0
    assert result['business_tables_required'] is False and result['versions']['python']
    assert set(result['versions']['packages'])==set(RUNTIME_DISTRIBUTIONS)
    with pytest.raises(ValueError,match='authenticated AIDP subject'):
        asyncio.run(runtime.invoke('inspect_runtime'))
    with pytest.raises(ValueError,match='explicit authenticated service bindings'):
        agent_package({'schema_version':1,'research_enabled':True})


def test_market_recovers_after_the_last_domain_without_starting_another_capture():
    store,model,collector,graph=market_fixture()
    original=store.progress_task
    def interrupt_after_last_task(task, **updates):
        original(task, **updates)
        if updates.get('completed'):
            raise RuntimeError('Simulated interruption before consolidation')
    store.progress_task=interrupt_after_last_task
    with pytest.raises(RuntimeError,match='before consolidation'):
        market_run(graph)
    store.progress_task=original
    from ai_market_intelligence.runtime.workflow import MarketWorkflow
    from langgraph.checkpoint.memory import InMemorySaver
    fresh=MarketWorkflow(store,model,collector,enabled_sources=('web',)).build(InMemorySaver())
    events,state=market_run(fresh)
    assert len(collector.calls)==1 and state['response']['status']=='complete'
    assert events[-1]['type']=='run.completed'


def test_terminal_event_and_response_commit_together_or_neither_commits():
    store,model,collector,graph=market_fixture()
    original=store.snapshot.write
    def fail_terminal_commit(value,etag):
        if value['events'] and value['events'][-1]['type']=='run.completed':
            raise RuntimeError('Simulated terminal storage failure')
        original(value,etag)
    store.snapshot.write=fail_terminal_commit
    with pytest.raises(RuntimeError,match='terminal storage failure'):
        market_run(graph)
    assert store.read()[0]['response'] is None
    assert not any(event['type']=='run.completed' for event in store.replay()['events'])
    store.snapshot.write=original
    from ai_market_intelligence.runtime.workflow import MarketWorkflow
    from langgraph.checkpoint.memory import InMemorySaver
    fresh=MarketWorkflow(store,model,collector,enabled_sources=('web',)).build(InMemorySaver())
    events,state=market_run(fresh)
    assert len(collector.calls)==1 and state['response']['status']=='complete'
    assert sum(event['type']=='run.completed' for event in store.replay()['events'])==1


def test_source_count_chart_keeps_all_references_across_valid_collections():
    from ai_market_intelligence.runtime.artifacts import artifacts
    rows=[market_evidence(source,number) for source in ('web','x') for number in range(26)]
    chart=artifacts(rows)[0]
    assert len(chart['evidence_ids'])==52 and sum(row['value'] for row in chart['rows'])==52
    assert set(chart['evidence_ids'])=={row['evidence_id'] for row in rows}


def test_resume_after_publication_keeps_identical_block_revision():
    store,model,collector,graph=market_fixture()
    original=store.progress_task
    def interrupt_before_advance(task, **updates):
        if updates.get('completed'):
            raise RuntimeError('Interrupted after publication')
        original(task, **updates)
    store.progress_task=interrupt_before_advance
    with pytest.raises(RuntimeError,match='after publication'):
        market_run(graph)
    previous=store.read()[0]['blocks']['source-web']
    store.progress_task=original
    from ai_market_intelligence.runtime.workflow import MarketWorkflow
    from langgraph.checkpoint.memory import InMemorySaver
    fresh=MarketWorkflow(store,model,collector,enabled_sources=('web',)).build(InMemorySaver())
    events,state=market_run(fresh)
    assert store.read()[0]['blocks']['source-web']==previous
    assert not any(event['type'] in {'draft.upsert','draft.corrected'} for event in events)
    assert [event['type'] for event in events].count('draft.compacted')==1
    assert len(collector.calls)==1 and state['delivered']


def test_rejected_long_synthesis_closes_with_full_verified_blocks_and_references():
    store,model,collector,graph=market_fixture(('web','x'))
    original=model.ask
    async def long_blocks(name,schema,payload):
        result=await original(name,schema,payload)
        if name=='market_draft':
            result['text']='ObservaciÃ³n atribuida. '*500
        if name=='market_verify' and len(payload['cited_evidence_ids'])==2:
            row=payload['evidence'][0]
            result={'accepted':False,'issues':[{'claim':payload['block']['text'],'evidence_id':row['evidence_id'],
                     'quoted_text':row['text'],'reason':'La sÃ­ntesis aÃ±ade una inferencia sin apoyo.'}]}
        return result
    model.ask=long_blocks
    events,state=market_run(graph)
    response=state['response']
    assert len(response['markdown'])<=20000 and len(response['blocks'])==2
    assert all(len(block['text'])>10000 for block in response['blocks'])
    assert len(response['references'])==2 and events[-1]['type']=='run.completed'
    assert store.replay()['events'][-1]['text']==response['markdown']


def later_rejection_fixture(repair_accepted):
    store,model,collector,graph=market_fixture()
    original=model.ask
    claim='Los otros fabricantes no producen esta marca.'
    useful='El artÃ­culo atribuye la fabricaciÃ³n a un proveedor; no confirma exclusividad.'
    reviews=[]
    async def previously_accepted(name,schema,payload):
        result=await original(name,schema,payload)
        if name in {'market_draft','market_consolidate'}:
            result['text']=useful+' '+claim
        if name=='market_verify':
            reviews.append(payload)
            if len(reviews)>1:
                row=payload['evidence'][0]
                return {'accepted':False,'issues':[{'claim':claim,'evidence_id':row['evidence_id'],
                    'quoted_text':row['text'],'reason':'La fuente no excluye a otros fabricantes.'}]}
        if name=='market_repair_units':
            assert payload['review_issues'] and payload['rejected_block']['text'].endswith(claim)
            return {'units':[{'unit_id':'u1','text':useful,'evidence_ids':[payload['evidence'][0]['evidence_id']],
                             'limitations':['Atribución documental parcial.']}]}
        if name=='market_verify_repair':
            assert payload['review_issues'] and payload['question'] and payload['rejected_block']['text'].endswith(claim)
            row=payload['evidence'][0]
            return {'reviews':[{'unit_id':'u1','accepted':repair_accepted,'issues':[] if repair_accepted else
                [{'claim':useful,'evidence_id':row['evidence_id'],'quoted_text':row['text'],'reason':'No se pudo verificar la reparaciÃ³n.'}]}]}
        return result
    model.ask=previously_accepted
    return store,model,collector,graph,claim,useful


@pytest.mark.parametrize('repair_accepted',[True,False])
def test_later_rejection_cannot_restore_the_same_unsupported_source_claim(repair_accepted):
    store,model,collector,graph,claim,useful=later_rejection_fixture(repair_accepted)
    events,state=market_run(graph)
    response=state['response']
    assert claim not in response['markdown'] and response['status']=='partial'
    block=store.read()[0]['blocks']['source-web']
    assert claim not in block['text'] and any(e['type']=='draft.corrected' for e in events)
    assert model.calls.count('market_repair_units')==model.calls.count('market_verify_repair')==1
    assert len(collector.calls)==1 and len(store.read()[0]['draft_repairs'])==1
    import json
    from ai_market_intelligence.runtime.wire import market_replay_packet
    assert claim not in json.loads(market_replay_packet(store,'current-request',0))['response']['markdown']
    if repair_accepted:
        assert useful in response['markdown'] and response['references']
    else:
        assert block['status']=='failed' and response['references']==[] and response['artifacts']==[]
        assert not any('se conservan los hallazgos previamente verificados' in limit
                       for limit in response['limitations'])
    preserved_limit = 'Atribución documental parcial.'
    assert (preserved_limit in response['limitations']) is repair_accepted
    assert (preserved_limit in events[-1]['limitations']) is repair_accepted
    replay_response = json.loads(market_replay_packet(store, 'current-request', 0))['response']
    assert (preserved_limit in replay_response['limitations']) is repair_accepted


@pytest.mark.parametrize('retained', [('u1',), ('u2',), (), ('u1', 'u2')])
@pytest.mark.parametrize('mode', ['conversation', 'documentary', 'financial'])
def test_late_review_of_repaired_units_retains_only_newly_accepted_units(retained, mode):
    store, model, collector, graph = market_fixture()
    original = model.ask
    useful = ('La publicación describe un proveedor de esta marca.' if mode == 'conversation'
              else 'El documento reporta ingresos del ejercicio 2020.')
    disputed = 'La publicación excluye a todos los otros proveedores.'
    verification_count = 0
    unit_reviews = 0
    collect = collector.collect

    async def paired_evidence(**arguments):
        result = await collect(**arguments)
        result['evidence'][0]['text'] = useful
        result['evidence'].append(market_evidence('web', 2))
        return result

    collector.collect = paired_evidence

    async def review_units(name, schema, payload):
        nonlocal verification_count, unit_reviews
        result = await original(name, schema, payload)
        if name == 'market_understand':
            result['retrieval_mode'] = mode
            if mode != 'conversation':
                result['objective'] = 'Consultar los ingresos del ejercicio 2020.'
        row = payload.get('evidence', [{}])[0]
        if name in {'market_draft', 'market_consolidate'}:
            result['text'] = useful + ' ' + disputed
        if name == 'market_verify':
            verification_count += 1
            return {'accepted': False, 'issues': [{'claim': disputed,
                'evidence_id': row['evidence_id'], 'quoted_text': row['text'],
                'reason': 'El soporte no excluye a otros proveedores.'}]}
        if name == 'market_repair_units':
            return {'units': [
                {'unit_id': 'u1', 'text': useful, 'evidence_ids': [row['evidence_id']],
                 'limitations': ['Alcance del núcleo.']},
                {'unit_id': 'u2', 'text': disputed, 'evidence_ids': [payload['evidence'][1]['evidence_id']],
                 'limitations': ['Alcance del detalle.']},
            ]}
        if name == 'market_verify_repair':
            unit_reviews += 1
            assert payload['review_issues'] and payload['rejected_block']
            assert {unit['unit_id'] for unit in payload['units']} == {'u1', 'u2'}
            return {'reviews': [
                {'unit_id': unit['unit_id'],
                 'accepted': unit_reviews == 1 or unit['unit_id'] in retained,
                 'issues': [] if unit_reviews == 1 or unit['unit_id'] in retained else [
                     {'claim': unit['text'], 'evidence_id': unit['evidence_ids'][0],
                      'quoted_text': next(item['text'] for item in payload['evidence']
                                          if item['evidence_id'] == unit['evidence_ids'][0]),
                      'reason': 'La revisión actual no admite esta afirmación.'}]}
                for unit in payload['units']
            ]}
        return result

    model.ask = review_units
    events, state = market_run(graph)
    response = state['response']
    assert verification_count == 2 and unit_reviews == 2
    assert model.calls.count('market_repair_units') == 1
    assert len(store.read()[0]['draft_repairs']) == 1 and len(collector.calls) == 1
    assert (useful in response['markdown']) is ('u1' in retained)
    assert (disputed in response['markdown']) is ('u2' in retained)
    assert bool(response['references']) is bool(retained)
    assert {ref['evidence_id'] for ref in response['references']} == {
        'evidence-web-' + unit_id[-1] for unit_id in retained}
    assert ('Alcance del detalle.' in response['limitations']) is ('u2' in retained)
    assert ('Alcance del núcleo.' in response['limitations']) is ('u1' in retained)
    assert response['status'] == 'partial'
    assert any(event['type'] == 'draft.corrected' for event in events)
    brief = store.read()[0]['brief']
    label = ('Periodo de publicaciones consultado' if mode == 'conversation'
             else 'Ventana operativa de búsqueda')
    assert f"{label}: {brief['start']} a {brief['end']}" in response['markdown']
    if mode != 'conversation':
        assert 'Periodo de publicaciones consultado' not in response['markdown']
        assert '2020' in brief['objective']
    assert 'Fuentes con evidencia: web' in response['markdown']
    assert 'Evidencias recuperadas: 2' in response['markdown']
    assert events[-1]['text'] == response['markdown']
    assert '_verified_units' not in json.dumps(response)
    from ai_market_intelligence.runtime.wire import market_replay_packet
    replay = json.loads(market_replay_packet(store, 'current-request', 0))
    assert replay['response']['markdown'] == response['markdown']
    assert '_verified_units' not in json.dumps(replay)


def test_final_partial_preserves_provider_and_evidence_limitations_without_model_repetition():
    store,model,collector,graph=market_fixture()
    collect=collector.collect
    ask=model.ask
    async def partial(**arguments):
        result=await collect(**arguments)
        result.update(status='partial',limitations=['El proveedor no devolviÃ³ la pÃ¡gina completa.'])
        result['evidence'][0]['limitations']=['Solo se recuperÃ³ el extracto publicado.']
        return result
    async def no_limits(name,schema,payload):
        result=await ask(name,schema,payload)
        if 'limitations' in result:
            result['limitations']=[]
        return result
    collector.collect,model.ask=partial,no_limits
    events,state=market_run(graph)
    assert state['response']['status']=='partial'
    assert set(state['response']['limitations'])=={'El proveedor no devolviÃ³ la pÃ¡gina completa.',
                                                  'Solo se recuperÃ³ el extracto publicado.'}
    assert events[-1]['limitations']==state['response']['limitations']
def test_account_scoped_research_does_not_require_inventing_a_geographic_market():
    from ai_market_intelligence.runtime.research_contracts import MarketBrief
    brief=MarketBrief.model_validate({'entity':'Humans of New York','market':None,
        'objective':'Summarise only the public posts of https://www.facebook.com/humansofnewyork/',
        'intent':'research','retrieval_mode':'conversation','start':'2026-10-27T04:02:47+00:00',
        'end':'2026-11-03T04:02:47+00:00','sources':['facebook'],'clarification':None,
        'assumptions':['Account sample only; no geographic population claims.']})
    assert brief.market is None and brief.sources==['facebook']


@pytest.mark.parametrize('proposed_start', [None, '2026-11-01T00:00:00+00:00'])
@pytest.mark.parametrize('clock_at', ['2026-11-08T03:15:31+00:00', '2026-11-07T22:15:31-05:00'])
def test_default_conversation_window_uses_controller_clock_without_rounding(proposed_start, clock_at):
    from ai_market_intelligence.runtime.workflow import MarketWorkflow
    store, model, collector, _ = market_fixture(('x',))
    store.begin('time-default', 'que se dice de d1 en colombia',
                started_at=clock_at)
    ask = model.ask
    async def default_period(name, schema, payload):
        result = await ask(name, schema, payload)
        if name == 'market_understand':
            result.update(period_is_explicit=False, start=proposed_start, end=None)
        return result
    model.ask = default_period
    workflow = MarketWorkflow(store, model, collector, enabled_sources=('x',))
    asyncio.run(workflow.understand({'question': 'que se dice de d1 en colombia'}))
    brief = store.read()[0]['brief']
    assert brief['start'] == '2026-11-01T03:15:31+00:00'
    assert brief['end'] == '2026-11-08T03:15:31+00:00'
    assert 'period_is_explicit' not in brief


def test_explicit_conversation_dates_are_not_replaced_by_default_window():
    from ai_market_intelligence.runtime.workflow import MarketWorkflow
    store, model, collector, _ = market_fixture(('x',))
    store.begin('time-explicit', 'Publicaciones de octubre', started_at='2026-11-08T03:15:31+00:00')
    ask = model.ask
    async def explicit_period(name, schema, payload):
        result = await ask(name, schema, payload)
        if name == 'market_understand':
            result.update(period_is_explicit=True, start='2026-10-01', end='2026-10-31')
        return result
    model.ask = explicit_period
    workflow = MarketWorkflow(store, model, collector, enabled_sources=('x',))
    asyncio.run(workflow.understand({'question': 'Publicaciones de octubre'}))
    brief = store.read()[0]['brief']
    assert brief['start'] == '2026-10-01T00:00:00+00:00'
    assert brief['end'] == '2026-10-31T23:59:59.999999+00:00'


@pytest.mark.parametrize('choice', ['missing', None, 'false', 0])
def test_intake_requires_a_boolean_period_choice_before_planning(choice):
    from ai_market_intelligence.runtime.workflow import MarketWorkflow
    store, model, collector, _ = market_fixture(('x',))
    store.begin('time-choice', 'Conversación reciente', started_at='2026-11-08T03:15:31+00:00')
    ask = model.ask
    async def invalid_choice(name, schema, payload):
        result = await ask(name, schema, payload)
        if name == 'market_understand':
            result.pop('period_is_explicit')
            if choice != 'missing':
                result['period_is_explicit'] = choice
        return result
    model.ask = invalid_choice
    workflow = MarketWorkflow(store, model, collector, enabled_sources=('x',))
    with pytest.raises(ValueError, match='period_is_explicit'):
        asyncio.run(workflow.understand({'question': 'Conversación reciente'}))
    assert store.read()[0]['brief'] is None and collector.calls == []


def test_instagram_post_reader_preserves_text_with_unavailable_like_count():
    call=social_call('instagram')
    row={'url':'https://instagram.com/p/fixture-post','inputUrl':'https://instagram.com/fixturebrand',
         'caption':'Comentario público recuperado.','timestamp':'2026-11-01T12:00:00Z',
         'likesCount':-1,'commentsCount':2}
    result=social_observations([row],{'runId':'fixture-receipt'},call)[0]
    assert result['text']==row['caption'] and result['metrics']['likes'] is None
    assert result['metrics']['comments']==2
    assert result['provenance']['comments_retrieved'] is False
