from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from ai_market_intelligence import access
from ai_market_intelligence.errors import AidpProvisionConflict


@pytest.fixture
def client(monkeypatch):
    identity = Mock()
    identity.get_tenancy.return_value.data = NS(id='tenancy', name='Test tenant')
    identity.get_domain.return_value.data = NS(id='domain', display_name='Default & admin')
    monkeypatch.setattr(access.oci.identity, 'IdentityClient', lambda *args, **kwargs: identity)
    return NS(_oci_config={'tenancy': 'tenancy'}, signer=object(), identity=identity,
              settings=NS(aidp_region='us-chicago-1', identity_domain_id='domain'))


EXPECTED_URL = 'https://nativeordagenthub.datalake.oci.oraclecloud.com/static/ah/#?tenant=Test+tenant&domain=Default+%26+admin'


@pytest.mark.parametrize('endpoint', [
    'wss://nativeord.datalake.oci.oraclecloud.com/socket',
    'nativeord.datalake.oci.oraclecloud.com',
    'wss://NATIVEORD.datalake.oci.oraclecloud.com:443/socket',
    'https://nativeordagenthub.datalake.oci.oraclecloud.com/static/ah/',
])
def test_agent_hub_link_uses_verified_prefix_once_and_encoded_identity(client, endpoint):
    assert access.agent_hub_url(client, NS(web_socket_endpoint=endpoint)) == EXPECTED_URL
    client.identity.get_tenancy.assert_called_once_with('tenancy')
    client.identity.get_domain.assert_called_once_with('domain')


@pytest.mark.parametrize('alias', ['native', 'nativeord', 'nativeordagenthub'])
def test_agent_hub_alias_uses_sdk_region_key_without_duplicating_suffix(client, alias):
    assert access.agent_hub_url(client, NS(alias_key=alias)) == EXPECTED_URL


@pytest.mark.parametrize('endpoint', [
    'wss://example.com/socket', 'wss://test.datalake.oci.oraclecloud.com.evil.com',
    'wss://user:password@test.datalake.oci.oraclecloud.com',
    'wss://@test.datalake.oci.oraclecloud.com',
    'wss://test.datalake.oci.oraclecloud.com:1234', 'http://test.datalake.oci.oraclecloud.com',
    'https://test.datalake.oci.oraclecloud.com:',
    'https://test.datalake.oci.oraclecloud.com:0443',
    'wss://nativeordagenthubagenthub.datalake.oci.oraclecloud.com',
    'wss://-nativeord.datalake.oci.oraclecloud.com',
    'wss://',
])
def test_agent_hub_rejects_invalid_endpoints_even_with_a_valid_alias(client, endpoint):
    with pytest.raises(AidpProvisionConflict):
        access.agent_hub_url(client, NS(web_socket_endpoint=endpoint, alias_key='native'))
    client.identity.get_tenancy.assert_not_called()


@pytest.mark.parametrize('alias', [
    'native.example.com', 'native/path', 'native:443', '-native', 'native-',
    'nativeordagenthubagenthub', 'a' * 57,
])
def test_agent_hub_rejects_invalid_or_oversized_derived_host(client, alias):
    with pytest.raises(AidpProvisionConflict):
        access.agent_hub_url(client, NS(alias_key=alias))


def test_missing_endpoint_does_not_invent_a_chat_route(client):
    assert access.agent_hub_url(client, NS()) == ''
    client.identity.get_tenancy.assert_not_called()


def test_alias_requires_a_known_sdk_region(client):
    client.settings.aidp_region = 'unknown-region-1'
    assert access.agent_hub_url(client, NS(alias_key='native')) == ''


@pytest.mark.parametrize('resource', ['tenancy', 'domain'])
def test_agent_hub_requires_verified_sign_in_identity(client, resource):
    getattr(client.identity, 'get_' + resource).return_value.data.id = 'unrelated'
    with pytest.raises(AidpProvisionConflict, match='identity could not be verified'):
        access.agent_hub_url(client, NS(alias_key='native'))
