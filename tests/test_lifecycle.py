from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from ai_market_intelligence import lifecycle
from ai_market_intelligence.errors import AidpProvisionConflict, AidpProvisionPending


OP = '11111111-1111-4111-8111-111111111111'
ADMIN = 'ocid1.user.oc1..selected'
OPERATOR = 'ocid1.user.oc1..operator'


@pytest.fixture
def verified_lifecycle(monkeypatch):
    events = []
    @contextmanager
    def lease(client, operation_id):
        events.append(('lock', operation_id))
        yield
        events.append(('unlock', operation_id))
    monkeypatch.setattr(lifecycle, 'installation_lock', lease)
    verified = Mock(return_value={'administrator_verified': True, 'roles_verified': True})
    monkeypatch.setattr(lifecycle, 'verify_administrator', verified)
    return events, verified


def test_new_platform_assigns_selected_admin_and_preserves_operator_signer(verified_lifecycle):
    _, verified = verified_lifecycle
    client = NS(settings=NS(admin_user_ocid=ADMIN), signer=NS(user=OPERATOR),
                _role_principal_ocids=Mock(return_value=(set(), set())),
                _role=Mock(return_value={'key': 'native-admin-role'}), _request=Mock())
    lifecycle.bind_new_administrator(client)
    request = client._request.call_args
    assert request.args == ('POST', '/roles/native-admin-role/actions/addMember')
    assert request.kwargs['payload'] == {'assignees': [{'type': 'USER', 'target': ADMIN}]}
    assert client.signer.user == OPERATOR
    assert verified.call_args_list[0].kwargs == {'roles_required': False}
    assert verified.call_args_list[-1].kwargs == {}


def test_bootstrap_does_not_duplicate_current_direct_assignment(verified_lifecycle):
    client = NS(settings=NS(admin_user_ocid=ADMIN), _role_principal_ocids=lambda name: ({ADMIN}, set()),
                _request=Mock())
    lifecycle.bind_new_administrator(client)
    client._request.assert_not_called()


def test_bootstrap_assignment_without_effective_role_cannot_advance_installation(verified_lifecycle):
    _, verified = verified_lifecycle
    verified.side_effect = [None, AidpProvisionConflict('role not effective')]
    client = NS(settings=NS(admin_user_ocid=ADMIN),
                _role_principal_ocids=Mock(return_value=(set(), set())),
                _role=Mock(return_value={'key': 'native-admin-role'}), _request=Mock(), operate=Mock())
    client._workspace = lambda: {'key': 'workspace'}
    client._read_installation = lambda key: None
    with pytest.raises(AidpProvisionConflict, match='not effective'):
        lifecycle.install(client, OP, new_platform=True, max_steps=1, sleep=lambda _: None)
    assert client._request.call_count == 1
    client.operate.assert_not_called()


def test_new_platform_resume_does_not_restore_revoked_admin_role(verified_lifecycle):
    _, verified = verified_lifecycle
    verified.side_effect = AidpProvisionConflict('administrator revoked')
    client = NS(settings=NS(admin_user_ocid=ADMIN),
        _workspace=lambda: {'key': 'workspace'}, _read_installation=lambda key: {'operation_id': OP},
        _request=Mock(), operate=Mock())
    with pytest.raises(AidpProvisionConflict, match='revoked'):
        lifecycle.install(client, OP, new_platform=True, max_steps=1, sleep=lambda _: None)
    client._request.assert_not_called()
    client.operate.assert_not_called()


def test_resume_keeps_operation_owner_session_and_credential_selection(verified_lifecycle):
    events, verified = verified_lifecycle
    calls = []
    saved = {'operation_id': OP, 'installed': False, 'status': 'installing'}
    async def operate(user, operation, **kw):
        calls.append((user, operation, kw))
        assert operation == saved['operation_id']
        return dict(saved)
    client = NS(settings=NS(admin_user_ocid=ADMIN), operate=operate)
    with pytest.raises(AidpProvisionPending, match='same operation'):
        lifecycle.install(client, OP, reuse_existing=False, test_session_id='existing-session', max_steps=1, sleep=lambda _: None)
    saved.update(installed=True, status='active', enabled=False, deployment_surface='TEST')
    result = lifecycle.install(client, OP, reuse_existing=False, test_session_id='existing-session', max_steps=1, sleep=lambda _: None)
    assert result['installed'] and result['enabled'] is False and result['deployment_surface'] == 'TEST'
    assert len(calls) == 2 and all(item == calls[0] for item in calls)
    assert calls[0] == (ADMIN, OP, {'reuse_existing': False, 'token': None, 'test_session_id': 'existing-session'})
    assert verified.call_count == 2
    assert events == [('lock', OP), ('unlock', OP), ('lock', OP), ('unlock', OP)]


def test_revocation_between_advances_stops_before_second_mutation(verified_lifecycle):
    _, verified = verified_lifecycle
    verified.side_effect = [None, AidpProvisionConflict('revoked')]
    operations = []
    async def operate(*args, **kw):
        operations.append(args)
        return {'status': 'installing', 'installed': False}
    client = NS(settings=NS(admin_user_ocid=ADMIN), operate=operate)
    with pytest.raises(AidpProvisionConflict, match='revoked'):
        lifecycle.install(client, OP, max_steps=3, sleep=lambda _: None)
    assert len(operations) == 1


@pytest.mark.parametrize('status', ['error', 'awaiting_action'])
def test_action_required_never_restarts_or_returns_success(verified_lifecycle, status):
    calls = []
    async def operate(*args, **kw):
        calls.append(args)
        return {'status': status, 'message': 'preserve candidate'}
    client = NS(settings=NS(admin_user_ocid=ADMIN), operate=operate)
    with pytest.raises(AidpProvisionConflict, match='preserve candidate'):
        lifecycle.install(client, OP, max_steps=3, sleep=lambda _: None)
    assert len(calls) == 1
