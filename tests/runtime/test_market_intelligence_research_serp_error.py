"""The observed bound SERP error settles its paid run without inventing evidence."""
import asyncio
import copy

import pytest


def test_serp_calendar_bounds_survive_actor_ignoring_tbs_and_do_not_double_on_receipt_validation():
    from ai_market_intelligence.runtime.serp import compile_serp_input, serp_observations, serp_receipt_matches
    from ai_market_intelligence.runtime.runtime_core import digest
    localized={'query':'Fixture Colombia noticias','maxResults':10,'countryCode':'CO',
               'languageCode':'es','dateFrom':'2026-12-31','dateTo':'2027-01-01'}
    inputs=compile_serp_input(localized)
    assert inputs['keyword']=='Fixture Colombia noticias after:2026-12-30 before:2027-01-02'
    call={'actor':'scraperlink/google-search-results-serp-scraper','input':inputs,
          'callOptions':{'maxItems':1,'build':'0.0.92'}}
    page={'page_number':1,'search_term':inputs['keyword'],'results':[
        {'position':1,'url':'https://example.com/story','title':'Published mention','description':'Text',
         'date':'hace 2 días'}]}
    item,=serp_observations([page],'known-run',call)
    assert item['provenance']['query']==localized['query']
    assert item['provenance']['effective_query']==inputs['keyword']
    assert item['published_at'] is None
    tool={'source_key':'apify','target_id':'serp','provider_run_id':'known-run',
          'request_hash':digest({'source_key':'apify','target_id':'serp','arguments':call})}
    requested={k:v for k,v in localized.items() if k not in {'query','maxResults'}}
    assert serp_receipt_matches(item['provenance'],tool,{'search_call':call},requested,
                                {'actor':call['actor'],'build':'0.0.92'})
    navigation=compile_serp_input({k:v for k,v in localized.items() if k not in {'dateFrom','dateTo'}})
    assert navigation['keyword']==localized['query'] and 'tbs' not in navigation


def test_provider_appended_before_is_bound_to_requested_tbs_and_preserved_as_provenance():
    call=search_call()
    page={'page_number':1,'search_term':call['input']['keyword']+' before:2026-11-02','results':[
        {'position':1,'url':'https://example.com/news','title':'A mention','description':'Text'}]}
    item,=serp_observations([page],'known-run',call)
    assert item['provenance']['provider_date_suffix']==' before:2026-11-02'
    assert item['provenance']['filter_verification']=='not_reported_by_provider'
    assert item['published_at'] is None
    for suffix in (' before:2026-11-03',' before:2026-11-01',' after:2026-10-26'):
        with pytest.raises(ValueError):
            serp_observations([{**page,'search_term':call['input']['keyword']+suffix}],'known-run',call)


def test_spelling_suggestion_cannot_rewrite_controller_date_operators():
    from ai_market_intelligence.runtime.serp import serp_query
    call=search_call();requested=call['input']['keyword'];tbs=call['input']['tbs']
    for changed in (requested.replace('before:2026-11-03','before:2026-11-04'),
                    requested.replace('after:2026-10-26','after:2026-10-25')):
        assert changed!=requested
        with pytest.raises(ValueError):
            serp_query({'search_term':changed,'related_keywords':{'spelling_suggestion':changed}},requested,tbs)
    corrected=requested.replace('reclamaciones','reclamacione')
    assert serp_query({'search_term':corrected,'related_keywords':{'spelling_suggestion':corrected}},requested,tbs)==corrected

from ai_market_intelligence.runtime.runtime_core import ConnectorUnavailable
from ai_market_intelligence.runtime.serp import serp_observations
from tests.runtime.test_market_intelligence_payload import provider_for
from tests.runtime.test_market_intelligence_research_collector import market_collection_fixture, market_collect


def error_page(call):
    return {'error':'Unexpected error: This operation was aborted','page_number':1,
            'keyword':call['input']['keyword']}


def search_call():
    _,_,collector=market_collection_fixture()
    return collector.call('web_search_serp',{'start':'2026-10-27T12:00:00Z','end':'2026-11-02T12:00:00Z'},
                          query='Fixture brand reclamaciones')


def test_bound_serp_page_error_is_unavailability_and_not_a_claim_of_no_results():
    call=search_call()
    with pytest.raises(ConnectorUnavailable,match='buscador') as captured:
        serp_observations([error_page(call)],'known-run',call)
    assert 'Unexpected error' not in str(captured.value)
    assert getattr(captured.value,'code',None)=='serp_page_error'


@pytest.mark.parametrize('mutation',[
    {'keyword':'Another query'},{'page_number':2},{'page_number':True},{'error':''},
    {'error':None},{'error':'x'*2001},{'results':[]},{'isError':True}])
def test_malformed_foreign_or_mixed_error_pages_keep_strict_validation(mutation):
    call=search_call();row={**error_page(call),**mutation}
    with pytest.raises(ValueError):serp_observations([row],'known-run',call)


def test_error_cannot_hide_an_additional_dataset_page():
    call=search_call()
    with pytest.raises(ValueError):serp_observations([error_page(call),error_page(call)],'known-run',call)


def test_valid_empty_serp_remains_distinct_from_a_provider_error():
    call=search_call()
    assert serp_observations([{'page_number':1,'search_term':call['input']['keyword'],'results':[]}],'known-run',call)==[]


def test_error_collection_and_cached_recovery_have_identical_status_receipt_and_cost():
    provider,effects,collector=market_collection_fixture()
    dataset_reads=[]
    async def normalize(receipt,call,**kwargs):
        normalizer,tool=provider_for({'datasetId':receipt['datasetId'],'items':[error_page(call)]})
        try:return await normalizer.observations(receipt,call,**kwargs)
        finally:dataset_reads.extend(tool.calls)
    async def selector(*args):raise AssertionError('A provider error cannot authorize a destination')
    provider.observations=normalize;collector.source_selector=selector
    first=market_collect(collector,source='web');saved=copy.deepcopy(effects.snapshot.read()[0])
    assert first['status']=='unavailable' and first['evidence']==[]
    assert 'buscador' in first['limitations'][0]
    assert market_collect(collector,source='web')==first and effects.snapshot.read()[0]==saved
    assert len(provider.starts)==len(provider.reads)==len(dataset_reads)==1
    assert effects.reserved_read_urls()==[]
    operation=next(iter(saved['operations'].values()))
    assert operation['state']=='completed' and operation['receipt']['status']=='SUCCEEDED'
    assert operation['receipt']['runId']=='receipt-1' and operation['receipt']['spent_microusd']==500
    assert operation['result']['items']==[] and operation['result']['coverage']['provider_result_error']=='serp_page_error'


@pytest.mark.parametrize('row',[{'page_number':1,'results':[]},
    {'page_number':1,'search_term':'Foreign query','results':[]}])
def test_invalid_terminal_dataset_closes_receipt_without_accepting_or_restarting_it(row):
    provider,effects,collector=market_collection_fixture()
    dataset_reads=[]
    async def normalize(receipt,call,**kwargs):
        normalizer,tool=provider_for({'datasetId':receipt['datasetId'],'items':[row]})
        try:return await normalizer.observations(receipt,call,**kwargs)
        finally:dataset_reads.extend(tool.calls)
    provider.observations=normalize
    first=market_collect(collector,source='web');saved=copy.deepcopy(effects.snapshot.read()[0])
    assert first['status']=='unavailable' and first['evidence']==[]
    assert 'incompatibles' in first['limitations'][0]
    operation=next(iter(saved['operations'].values()))
    assert operation['state']=='completed' and operation['receipt']['status']=='SUCCEEDED'
    assert operation['receipt']['spent_microusd']==500
    assert operation['result']['coverage']['provider_result_error']=='invalid_dataset'
    assert effects.reserved_read_urls()==[]
    from ai_market_intelligence.runtime.collector import MarketCollector
    restored=MarketCollector(provider,effects,search_context={'country_code':'CO','language_code':'es'},poll_attempts=1,poll_seconds=0)
    assert market_collect(restored,source='web')==first and effects.snapshot.read()[0]==saved
    assert len(provider.starts)==len(provider.reads)==len(dataset_reads)==1
