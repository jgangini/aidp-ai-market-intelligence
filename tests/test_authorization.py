from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from ai_market_intelligence.authorization import resolve_domain, verify_administrator
from ai_market_intelligence.errors import AidpProvisionConflict
from ai_market_intelligence.transport import AidpClient


USER = 'ocid1.user.oc1..selected'
GROUP = 'ocid1.group.oc1..administrators'


def role_client(assignees):
    client = AidpClient.__new__(AidpClient)
    client.settings = NS(admin_user_ocid=USER, identity_domain_id='ocid1.domain.oc1..selected')
    client._role = Mock(return_value={'key': 'admin'})
    client._request = Mock(side_effect=lambda method, path, **kw: {'assignees': assignees[path.removeprefix('/roles/')]})
    return client


def domain_user(*, ocid=USER, active=True, memberships=()):
    return NS(ocid=ocid, active=active, groups=[NS(value=item) for item in memberships])


def domain_client(users, groups=()):
    return NS(list_users=Mock(return_value=NS(data=NS(resources=users))),
              list_groups=Mock(return_value=NS(data=NS(resources=list(groups)))))


def test_active_direct_administrator_is_verified_without_group_lookup():
    client = role_client({'admin': [{'type': 'USER', 'target': USER}]})
    domain = domain_client([domain_user()])
    assert verify_administrator(client, domain_client=domain) == {'administrator_verified': True, 'roles_verified': True}
    domain.list_groups.assert_not_called()


def test_inherited_group_role_requires_matching_scim_membership():
    client = role_client({'admin': [{'type': 'ROLE', 'target': 'inherited'}],
                          'inherited': [{'type': 'GROUP', 'target': GROUP}, {'type': 'ROLE', 'target': 'admin'}]})
    domain = domain_client([domain_user(memberships=('scim-group-id',))], [NS(id='scim-group-id', ocid=GROUP)])
    assert verify_administrator(client, domain_client=domain)['roles_verified'] is True
    assert {call.args[1] for call in client._request.call_args_list} == {'/roles/admin', '/roles/inherited'}
    assert 'ocid1.group.oc1..administrators' in domain.list_groups.call_args.kwargs['filter']


def test_group_ocid_does_not_substitute_for_scim_group_id():
    client = role_client({'admin': [{'type': 'GROUP', 'target': GROUP}]})
    domain = domain_client([domain_user(memberships=(GROUP,))], [NS(id='different-scim-id', ocid=GROUP)])
    with pytest.raises(AidpProvisionConflict, match='lacks effective'):
        verify_administrator(client, domain_client=domain)


@pytest.mark.parametrize('users', [[], [domain_user(active=False)], [domain_user(ocid='ocid1.user.oc1..other')],
                                   [domain_user(), domain_user()]])
def test_missing_inactive_mismatched_or_ambiguous_user_cannot_administer(users):
    client = role_client({'admin': [{'type': 'USER', 'target': USER}]})
    with pytest.raises(AidpProvisionConflict, match='active user'):
        verify_administrator(client, domain_client=domain_client(users))
    client._role.assert_not_called()


def test_new_platform_can_validate_identity_before_roles_exist_but_existing_cannot():
    client = role_client({'admin': []})
    domain = domain_client([domain_user()])
    assert verify_administrator(client, roles_required=False, domain_client=domain)['roles_verified'] is False
    client._role.assert_not_called()
    with pytest.raises(AidpProvisionConflict, match='lacks effective'):
        verify_administrator(client, domain_client=domain)


@pytest.mark.parametrize('domain_id,state,url', [
    ('ocid1.domain.oc1..other', 'ACTIVE', 'https://selected.identity.oraclecloud.com'),
    ('ocid1.domain.oc1..selected', 'INACTIVE', 'https://selected.identity.oraclecloud.com'),
    ('ocid1.domain.oc1..selected', 'ACTIVE', 'https://attacker.example'),
    ('ocid1.domain.oc1..selected', 'ACTIVE', 'https://user:password@selected.identity.oraclecloud.com'),
    ('ocid1.domain.oc1..selected', 'ACTIVE', 'https://selected.identity.oraclecloud.com?target=other'),
])
def test_identity_endpoint_must_belong_to_active_selected_domain(monkeypatch, domain_id, state, url):
    client = role_client({'admin': []})
    client._oci_config = {}
    client.signer = object()
    iam = NS(get_domain=Mock(return_value=NS(data=NS(id=domain_id, lifecycle_state=state, url=url))))
    monkeypatch.setattr('ai_market_intelligence.authorization.oci.identity.IdentityClient', lambda *a, **kw: iam)
    with pytest.raises(AidpProvisionConflict, match='Identity Domain'):
        resolve_domain(client)


@pytest.mark.parametrize('suffix', ['', '/', ':443', ':443/'])
def test_verified_endpoint_is_canonical_for_deploy_studio(monkeypatch, suffix):
    client = NS(_oci_config={}, signer=object(), settings=NS(identity_domain_id='ocid1.domain.oc1..selected'))
    domain = NS(id=client.settings.identity_domain_id, lifecycle_state='ACTIVE',
                url='https://selected.identity.oraclecloud.com' + suffix)
    iam = NS(get_domain=Mock(return_value=NS(data=domain)))
    monkeypatch.setattr('ai_market_intelligence.authorization.oci.identity.IdentityClient', lambda *a, **kw: iam)
    assert resolve_domain(client) == 'https://selected.identity.oraclecloud.com'
    iam.get_domain.assert_called_once_with(client.settings.identity_domain_id)


@pytest.mark.parametrize('url', [
    'https://selected.identity.oraclecloud.com/admin/v1',
    'https://selected.identity.oraclecloud.com//',
    'https://selected.identity.oraclecloud.com:8443',
    'https://selected.identity.oraclecloud.com:invalid',
    'https://selected.identity.oraclecloud.com#fragment',
    'http://selected.identity.oraclecloud.com',
    'https://selected.identity.oraclecloud.com.attacker.example',
    'https://[invalid',
])
def test_verified_endpoint_rejects_non_root_and_malformed_urls(monkeypatch, url):
    client = NS(_oci_config={}, signer=object(), settings=NS(identity_domain_id='ocid1.domain.oc1..selected'))
    domain = NS(id=client.settings.identity_domain_id, lifecycle_state='ACTIVE', url=url)
    monkeypatch.setattr('ai_market_intelligence.authorization.oci.identity.IdentityClient',
                        lambda *a, **kw: NS(get_domain=Mock(return_value=NS(data=domain))))
    with pytest.raises(AidpProvisionConflict, match='Identity Domain'):
        resolve_domain(client)
