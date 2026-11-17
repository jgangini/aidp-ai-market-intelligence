"""LLM secret ownership and uncertain-create recovery without external services."""
import copy
import hashlib
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from pydantic import SecretStr
from ai_market_intelligence.installation.credentials import ensure_llm_credential, llm_credential_reference

OP = '11111111-1111-4111-8111-111111111111'
KEY = 'fixture_api_key_' + 'a' * 32


@pytest.fixture
def target():
    state = {'operation_id': OP, 'resources': {}}
    rows, saved = [], []
    def request(method, path, **kwargs):
        assert method == 'POST' and path == '/credentials'
        assert saved[-1]['resources']['llm_create_requested'] is True
        payload = kwargs['payload']
        rows.append({'key': 'llm-key', 'displayName': payload['displayName'],
                     'credentialDescription': payload['credentialDescription'],
                     'type': 'SECRET_TOKEN', 'lifecycleState': 'ACTIVE'})
    client = NS(_list=lambda *a, **kw: copy.deepcopy(rows),
                _save=lambda state: saved.append(copy.deepcopy(state)), _request=Mock(side_effect=request))
    return client, state, rows, saved


def test_llm_secret_is_created_after_intent_and_reused_without_rotation(target):
    client, state, rows, saved = target
    result = ensure_llm_credential(client, state, SecretStr(KEY))
    assert result == {'credential_key': 'llm-key', 'credential_name': 'AiMarketIntelligenceOpenAI', 'secret_key': 'api_key'}
    assert KEY not in json.dumps(saved)
    assert client._request.call_args.kwargs['payload']['credentialDetails']['secretTokenPair'] == [
        {'secretKey': 'api_key', 'secretValue': KEY}]
    assert ensure_llm_credential(client, state) == result
    assert ensure_llm_credential(client, state, SecretStr(KEY)) == result
    assert client._request.call_count == 1


def test_llm_uncertain_create_is_not_repeated(target):
    client, state, rows, saved = target
    client._request.side_effect = TimeoutError('private provider error')
    with pytest.raises(TimeoutError):
        ensure_llm_credential(client, state, SecretStr(KEY))
    with pytest.raises(RuntimeError, match='uncertain'):
        ensure_llm_credential(client, state, SecretStr(KEY))
    assert client._request.call_count == 1
    assert state['resources'] == {'llm_create_requested': True,
        'llm_secret_sha256': hashlib.sha256(KEY.encode()).hexdigest()}


def test_llm_lost_reply_can_recover_only_its_exact_owned_create(target):
    client, state, rows, saved = target
    ensure_llm_credential(client, state, SecretStr(KEY))
    state['resources'].pop('llm_credential_key')
    assert ensure_llm_credential(client, state)['credential_key'] == 'llm-key'
    assert client._request.call_count == 1
    rows[0]['key'] = 'foreign'
    with pytest.raises(RuntimeError, match='identity'):
        llm_credential_reference(client, state)


def test_llm_unowned_name_collision_is_never_adopted(target):
    client, state, rows, saved = target
    rows.append({'displayName': 'AiMarketIntelligenceOpenAI', 'key': 'foreign',
                 'credentialDescription': 'another installation'})
    with pytest.raises(RuntimeError, match='unrelated'):
        ensure_llm_credential(client, state, SecretStr(KEY))
    assert not saved
    client._request.assert_not_called()


@pytest.mark.parametrize('pending', [False, True])
def test_changed_key_cannot_select_a_model_while_reusing_another_credential(target, pending):
    client, state, rows, saved = target
    ensure_llm_credential(client, state, SecretStr(KEY))
    if pending:
        state['resources'].pop('llm_credential_key')
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match='does not match'):
        ensure_llm_credential(client, state, SecretStr('different-fixture-key-000000000'))
    assert state == before and client._request.call_count == 1


def test_record_without_fingerprint_does_not_claim_a_new_key_matches(target):
    client, state, rows, saved = target
    ensure_llm_credential(client, state, SecretStr(KEY))
    state['resources'].pop('llm_secret_sha256')
    with pytest.raises(ValueError, match='does not match'):
        ensure_llm_credential(client, state, SecretStr(KEY))
    assert ensure_llm_credential(client, state)['credential_key'] == 'llm-key'
    assert client._request.call_count == 1
