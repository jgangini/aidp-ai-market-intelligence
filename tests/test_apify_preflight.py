"""Read-only preflight against a mock HTTP transport; no live provider access."""
import json

import httpx
import pytest
from pydantic import SecretStr

from ai_market_intelligence.apify_preflight import _read, check_apify
from ai_market_intelligence.runtime.actor_profiles import PROFILES

TOKEN = SecretStr('apify_api_sensitive_token_1234567890')
PROFILE = {'reviewed': {'actor_id': 'Actor123', 'build': '1.2.3'}}


def mock_client(handler):
    requests = []
    def transport(request):
        requests.append(request)
        assert request.method == 'GET'
        assert request.url.scheme == 'https' and request.url.host == 'api.apify.com'
        assert request.url.path == '/v2/users/me' or request.url.path == '/v2/actors/Actor123' or any(
            request.url.path in {'/v2/actors/' + profile['actor_id'], '/v2/actors/' + profile['actor_id'] + '/builds'}
            for profile in PROFILES.values()) or request.url.path == '/v2/actors/Actor123/builds'
        assert not any(part in request.url.path for part in ('runs', 'run-sync', 'tasks'))
        assert request.headers['Authorization'] == 'Bearer ' + TOKEN.get_secret_value()
        assert 'token' not in request.url.params
        return handler(request)
    return httpx.Client(transport=httpx.MockTransport(transport)), requests


def success(request):
    path = request.url.path
    if path.endswith('/users/me'):
        return httpx.Response(200, json={'data': {'id': 'account', 'email': 'private@example.org',
            'proxy': {'password': TOKEN.get_secret_value()}}})
    if path.endswith('/builds'):
        actor = path.split('/')[-2]
        build = next((p['build'] for p in PROFILES.values() if p['actor_id'] == actor), '1.2.3')
        return httpx.Response(200, json={'data': {'items': [{'buildNumber': build, 'status': 'SUCCEEDED'}]}})
    return httpx.Response(200, json={'data': {'id': path.split('/')[-1]}})


def check(handler=success, **options):
    with mock_client(handler)[0] as client:
        return check_apify(TOKEN, client=client, profiles=PROFILE, **options)


def test_all_canonical_profiles_use_exact_actor_and_build_without_starting_runs():
    client, requests = mock_client(success)
    with client:
        result = check_apify(TOKEN, client=client)
    expected = ['/v2/users/me']
    for profile in PROFILES.values():
        if not profile.get('unavailable_reason'):
            expected += ['/v2/actors/' + profile['actor_id'], '/v2/actors/' + profile['actor_id'] + '/builds']
    assert [request.url.path for request in requests] == expected
    assert result['account'] == {'status': 'authenticated'}
    assert result['reads_verified'] is True and result['apify_starts'] == 0
    assert result['execution_permission'] == 'not_verified'
    assert len(result['actors']) == len(PROFILES) == 13
    for row in result['actors']:
        profile = PROFILES[row['profile']]
        assert row['actor_id'] == profile['actor_id'] and row['build'] == profile['build']
        assert row['execution_permission'] == row['entitlement'] == 'not_verified'
        assert row['status'] == ('suspended' if profile.get('unavailable_reason') else 'visible')
        assert row['required'] is not bool(profile.get('unavailable_reason'))
    public = json.dumps(result)
    assert all(private not in public for private in (TOKEN.get_secret_value(), 'private@example.org', 'password'))


def test_suspended_profile_is_reported_without_actor_or_build_reads():
    profile = next(p for p in PROFILES.values() if p.get('unavailable_reason'))
    client, requests = mock_client(success)
    with client:
        result = check_apify(TOKEN, client=client, profiles={'suspended': profile})
    assert [r.url.path for r in requests] == ['/v2/users/me']
    assert result['actors'][0]['status'] == 'suspended'
    assert result['actors'][0]['entitlement'] == 'not_verified'


@pytest.mark.parametrize('stage', ['account', 'actor', 'build'])
@pytest.mark.parametrize('code,expected', [(401,'unauthenticated'), (403,'access_denied'),
    (404,'absent_or_invisible'), (429,'indeterminate'), (500,'indeterminate')])
def test_http_errors_are_actionable_redacted_and_never_trigger_start(stage, code, expected):
    def handler(request):
        match = request.url.path.endswith({'account':'/users/me', 'actor':'/Actor123', 'build':'/builds'}[stage])
        if match:
            return httpx.Response(code, json={'error': {'message': 'Rental/subscription restriction ' + TOKEN.get_secret_value()}})
        return success(request)
    result = check(handler)
    error = result['account'] if stage == 'account' else result['actors'][0]
    assert error['status'] == expected and error['http_status'] == code and error['message']
    assert result['reads_verified'] is False and result['apify_starts'] == 0
    assert TOKEN.get_secret_value() not in json.dumps(result)
    if code == 403:
        assert 'restriction' in error['message'] and 'scope' in error['message']
    if stage == 'account':
        assert result['actors'] == []


@pytest.mark.parametrize('payload', [[], None, {'data': []}, {'data': None}, {'data': {}},
    {'error': {'message': TOKEN.get_secret_value()}}])
def test_malformed_account_never_reports_authenticated(payload):
    result = check(lambda request: httpx.Response(200, json=payload))
    assert result['account']['status'] == 'indeterminate'
    assert result['reads_verified'] is False and result['actors'] == []
    assert TOKEN.get_secret_value() not in json.dumps(result)


def test_invalid_json_is_indeterminate():
    result = check(lambda request: httpx.Response(200, content=b'not-json'))
    assert result['account']['status'] == 'indeterminate'


@pytest.mark.parametrize('payload', [None, [], {'items': 'not-a-list'}, {'items': [None]},
    {'items': [{'buildNumber':'1.2.3','status':'SUCCEEDED'}]*2}])
def test_malformed_or_ambiguous_builds_do_not_verify_reads(payload):
    def handler(request):
        return httpx.Response(200,json={'data':payload}) if request.url.path.endswith('/builds') else success(request)
    result = check(handler)
    assert result['actors'][0]['status'] == 'indeterminate'
    assert result['reads_verified'] is False


@pytest.mark.parametrize('status', ['RUNNING','FAILED','ABORTED',None])
def test_pinned_build_requires_success(status):
    def handler(request):
        return httpx.Response(200,json={'data':{'items':[{'buildNumber':'1.2.3','status':status}]}}) if request.url.path.endswith('/builds') else success(request)
    result=check(handler)
    assert result['actors'][0]['status']=='build_unavailable' and result['reads_verified'] is False


def test_actor_identity_mismatch_never_requests_builds():
    def handler(request):
        if request.url.path.endswith('/Actor123'):
            return httpx.Response(200,json={'data':{'id':'foreign'}})
        return success(request)
    client, requests = mock_client(handler)
    with client:
        result = check_apify(TOKEN,client=client,profiles=PROFILE)
    assert result['actors'][0]['status']=='identity_mismatch'
    assert len(requests)==2


@pytest.mark.parametrize('found_page', [None, 1])
def test_build_pagination_has_an_exact_bound_and_does_not_use_latest(found_page):
    def handler(request):
        if not request.url.path.endswith('/builds'):
            return success(request)
        page=int(request.url.params['offset'])//1000
        assert request.url.params['limit']=='1000' and request.url.params['desc']=='true'
        items=[{'buildNumber':'latest','status':'SUCCEEDED'}]*1000
        if page==found_page:
            items[0]={'buildNumber':'1.2.3','status':'SUCCEEDED'}
        return httpx.Response(200,json={'data':{'items':items}})
    client, requests=mock_client(handler)
    with client:
        result=check_apify(TOKEN,client=client,profiles=PROFILE,max_build_pages=2)
    assert len(requests)==4
    assert result['actors'][0]['status']==('not_verified' if found_page is None else 'visible')
    assert result['execution_permission']=='not_verified'


def test_short_build_page_ends_search_and_reports_absent_or_invisible():
    def handler(request):
        return httpx.Response(200,json={'data':{'items':[]}}) if request.url.path.endswith('/builds') else success(request)
    client,requests=mock_client(handler)
    with client:
        result=check_apify(TOKEN,client=client,profiles=PROFILE,max_build_pages=5)
    assert len(requests)==3 and result['actors'][0]['status']=='build_absent_or_invisible'


def test_redirect_never_follows_or_forwards_bearer_to_another_host():
    client,requests=mock_client(lambda request:httpx.Response(302,headers={'location':'https://untrusted.example/runs'}))
    with client:
        result=check_apify(TOKEN,client=client,profiles=PROFILE)
    assert len(requests)==1 and result['account']['status']=='indeterminate'


def test_timeout_does_not_echo_request_token_or_retry():
    def timeout(request):
        raise httpx.ReadTimeout(TOKEN.get_secret_value(),request=request)
    client,requests=mock_client(timeout)
    with client:
        result=check_apify(TOKEN,client=client,profiles=PROFILE)
    assert len(requests)==1 and result['account']['status']=='indeterminate'
    assert TOKEN.get_secret_value() not in json.dumps(result)


@pytest.mark.parametrize('path',['/actors/Actor123/runs','/acts/Actor123/run-sync','/actor-tasks/Task123/run-sync',
    '/actors/Actor123?token=secret','https://other.example/users/me','/actors/../users/me'])
def test_internal_read_allowlist_rejects_every_start_or_external_surface(path):
    client,requests=mock_client(success)
    with client, pytest.raises(ValueError,match='Unsupported'):
        _read(client,path,TOKEN)
    assert not requests


@pytest.mark.parametrize('token',[TOKEN.get_secret_value(),SecretStr('short'),SecretStr('x'*513),SecretStr('a'*20+'\n')])
def test_invalid_token_is_rejected_before_network(token):
    client,requests=mock_client(success)
    with client,pytest.raises(ValueError,match='Invalid Apify credential'):
        check_apify(token,client=client)
    assert not requests


@pytest.mark.parametrize('pages',[0,6,True,1.5,'2'])
def test_page_bound_requires_an_integer_before_network(pages):
    client,requests=mock_client(success)
    with client,pytest.raises(ValueError,match='bounded'):
        check_apify(TOKEN,client=client,profiles=PROFILE,max_build_pages=pages)
    assert not requests


@pytest.mark.parametrize('payload',[None, [], {'id': None}, {'id': 123}, {'error': 'subscription required'}])
def test_malformed_actor_metadata_cannot_verify_actor_or_execution_access(payload):
    def handler(request):
        return httpx.Response(200,json={'data':payload}) if request.url.path.endswith('/Actor123') else success(request)
    client,requests=mock_client(handler)
    with client:
        result=check_apify(TOKEN,client=client,profiles=PROFILE)
    row=result['actors'][0]
    assert row['status'] in {'indeterminate','identity_mismatch'}
    assert row['execution_permission']==row['entitlement']=='not_verified'
    assert result['reads_verified'] is False and len(requests)==2
