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
    return NS(_oci_config={'tenancy': 'tenancy'}, signer=object(),
              settings=NS(aidp_region='us-chicago-1', identity_domain_id='domain'))


def test_workbench_link_uses_native_endpoint_and_encoded_identity(client):
    result = access.workbench_url(client, NS(web_socket_endpoint='wss://nativeord.datalake.oci.oraclecloud.com/socket'))
    assert result == 'https://nativeord.datalake.oci.oraclecloud.com#?tenant=Test+tenant&domain=Default+%26+admin'


def test_workbench_alias_uses_sdk_region_key(client):
    assert access.workbench_url(client, NS(alias_key='native')).startswith('https://nativeord.datalake.oci.oraclecloud.com#?')


@pytest.mark.parametrize('endpoint', [
    'wss://example.com/socket', 'wss://test.datalake.oci.oraclecloud.com.evil.com',
    'wss://user:password@test.datalake.oci.oraclecloud.com',
    'wss://test.datalake.oci.oraclecloud.com:1234', 'http://test.datalake.oci.oraclecloud.com',
])
def test_workbench_rejects_non_oracle_or_credential_bearing_endpoints(client, endpoint):
    with pytest.raises(AidpProvisionConflict):
        access.workbench_url(client, NS(web_socket_endpoint=endpoint))


def test_missing_endpoint_does_not_invent_a_chat_route(client):
    assert access.workbench_url(client, NS()) == ''
