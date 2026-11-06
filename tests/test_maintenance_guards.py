import asyncio
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from ai_market_intelligence.client import MarketAidpClient, public_status
from ai_market_intelligence.errors import AidpProvisionConflict
from tests.installer.helpers import bare_client, FakeResponse
from tests.installer.test_market_installer import state, OP


def test_public_maintenance_summary_never_exposes_backup_or_diagnostics():
    saved = state('ready')
    saved['maintenance'] = {'operation_id': OP, 'action': 'update', 'phase': 'target_wait', 'status': 'running',
        'before_state': {'private': 'not-public'}, 'diagnostic': 'not-public'}
    result = public_status(saved)
    assert result['status'] == 'updating' and result['phase'] == 'target_wait'
    assert result['maintenance'] == {key: saved['maintenance'][key] for key in ('operation_id','action','phase','status')}
    assert 'not-public' not in str(result)


def test_install_cannot_overwrite_incomplete_maintenance():
    client = MarketAidpClient.__new__(MarketAidpClient)
    saved = state('ready')
    saved['maintenance'] = {'status': 'running'}
    client._workspace = lambda: {'key': 'workspace'}
    client._read_installation = lambda key: saved
    client._save = Mock()
    with pytest.raises(AidpProvisionConflict, match='maintenance'):
        client._advance('owner', OP, True, None, None)
    client._save.assert_not_called()


def test_endpoint_is_not_certified_during_unaccepted_update():
    client = MarketAidpClient.__new__(MarketAidpClient)
    saved = state('ready')
    saved['maintenance'] = {'status': 'running'}
    client._workspace = lambda: {'key': 'workspace'}
    client._read_installation = lambda key: saved
    with pytest.raises(AidpProvisionConflict, match='not yet verified'):
        asyncio.run(client.agent_endpoint())


def test_raw_workspace_backup_preserves_json_bytes_without_decoding():
    client = bare_client()
    original = b'{ "spacing": 2 }\r\n'
    response = FakeResponse(content=original)
    response.json = Mock(side_effect=AssertionError('Do not reserialize backup bytes'))
    client.session = NS(request=Mock(return_value=response))
    actual = client._workspace_bytes('workspace', '/Workspace/.artifact/ai_market_intelligence/agent-manifest.json')
    assert actual == original
    response.json.assert_not_called()
    assert client.session.request.call_args.kwargs['allow_redirects'] is False
