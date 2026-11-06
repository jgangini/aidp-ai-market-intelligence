"""Runner contracts and ownership failures without OCI or Apify connections."""
import json
import hashlib
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from ai_market_intelligence import hooks
from ai_market_intelligence.errors import AidpProvisionConflict

OP = '00000000-0000-4000-8000-000000000001'
DEPLOYMENT = 'dep_0123456789abcdef'
USER = 'ocid1.user.oc1..synthetic'
DOMAIN = 'ocid1.domain.oc1..synthetic'


def context(mode='new'):
    fields = {'aidp_mode': mode, 'identity_domain_id': DOMAIN, 'admin_user_ocid': USER,
        'agent_model_id': 'model', 'artifacts_bucket_mode': 'new', 'artifacts_bucket_name': 'artifacts',
        'autonomous_database_mode': 'existing', 'existing_autonomous_database_ocid': 'ocid1.autonomousdatabase.oc1..synthetic'}
    if mode == 'existing':
        fields.update(existing_ai_data_platform_ocid='ocid1.aidataplatform.oc1..synthetic', existing_workspace_key='exact-key')
    return {'schema_version': 1, 'project_id': 'aidp-ai-market-intelligence', 'deployment_id': DEPLOYMENT,
        'region': 'us-chicago-1', 'compartment': 'ocid1.compartment.oc1..synthetic',
        'compartment_mode': 'existing', 'inputs': fields}


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setattr(hooks, 'preflight_memory', Mock(return_value={'name': 'Agent Flow memory', 'status': 'passed', 'message': 'Fixture binding'}))
    monkeypatch.setattr(hooks.oci.config, 'from_file', lambda *a: {'tenancy': 'tenancy', 'user': 'operator',
        'fingerprint': 'fingerprint', 'key_file': '/not-a-real-key'})
    monkeypatch.setattr(hooks.oci.signer, 'Signer', lambda **k: 'operator-signer')
    identity = Mock()
    identity.list_region_subscriptions.return_value.data = [NS(region_name='us-ashburn-1', is_home_region=True)]
    identity.get_compartment.return_value.data = NS(id='ocid1.compartment.oc1..synthetic', name='existing', lifecycle_state='ACTIVE')
    monkeypatch.setattr(hooks.oci.identity, 'IdentityClient', lambda *a, **k: identity)
    storage = Mock()
    storage.get_namespace.return_value.data = 'namespace'
    monkeypatch.setattr(hooks.oci.object_storage, 'ObjectStorageClient', lambda *a, **k: storage)
    platform = Mock()
    platform.get_ai_data_platform.return_value.data = NS(compartment_id='ocid1.compartment.oc1..synthetic')
    monkeypatch.setattr(hooks.oci.ai_data_platform, 'AiDataPlatformClient', lambda *a, **k: platform)
    model = Mock()
    model.get_model.return_value.data = NS(lifecycle_state='ACTIVE')
    monkeypatch.setattr(hooks.oci.generative_ai, 'GenerativeAiClient', lambda *a, **k: model)
    monkeypatch.setattr(hooks, 'resolve_domain', lambda c: 'https://domain.identity.oraclecloud.com')
    auth = Mock(return_value={'administrator_verified': True})
    monkeypatch.setattr(hooks, 'verify_administrator', auth)
    monkeypatch.setattr(hooks, 'check_apify', lambda token: {'account': {'status': 'authenticated'},
        'actors': [{'profile': 'website', 'status': 'visible'}], 'reads_verified': True})
    return NS(identity=identity, storage=storage, auth=auth, model=model)


def secrets():
    return {'inputs': {'apify_token': 'synthetic-local-preflight-secret'}}


def test_new_preflight_does_not_consult_nonexistent_aidp_roles(native, monkeypatch):
    monkeypatch.setattr(hooks, 'MarketAidpClient', lambda *a: pytest.fail('New platform has no roles yet'))
    result = hooks.preflight(context(), secrets(), 'synthetic-config')
    assert result['inputs'] == {'home_region': 'us-ashburn-1', 'identity_domain_url': 'https://domain.identity.oraclecloud.com',
        'objectstorage_namespace': 'namespace'}
    assert native.auth.call_args.kwargs == {'roles_required': False}
    assert result['events'][-1]['status'] == 'warning'
    assert 'not verified' in result['events'][-1]['message']
    assert 'synthetic-local-preflight-secret' not in json.dumps(result)
    hooks.preflight_memory.assert_called_once()
    assert hooks.preflight_memory.call_args.args[2]['region'] == 'us-chicago-1'


def test_existing_preflight_uses_exact_key_and_revalidates_roles(native, monkeypatch):
    captured = []
    class Client:
        def __init__(self, settings): captured.append(settings)
        def _workspace(self): return {'key': 'exact-key'}
        async def close(self): pass
    monkeypatch.setattr(hooks, 'MarketAidpClient', Client)
    monkeypatch.setattr(hooks, '_platform', lambda c: None)
    hooks.preflight(context('existing'), secrets(), 'synthetic-config')
    assert captured[0].aidp_workspace_key == 'exact-key'
    assert native.auth.call_count == 2
    assert native.auth.call_args.kwargs == {}


def test_stale_new_selection_rejected_before_model_or_apify(native):
    c = context()
    c['inputs']['existing_workspace_key'] = 'foreign'
    with pytest.raises(ValueError): hooks.preflight(c, secrets(), 'config')
    native.model.get_model.assert_not_called()


def test_actor_read_failures_are_actionable_runner_events(native, monkeypatch):
    monkeypatch.setattr(hooks, 'check_apify', lambda token: {'account': {'status': 'authenticated'},
        'actors': [{'profile': 'website', 'status': 'access_denied', 'message': 'Check token scopes and rental restrictions.'}],
        'reads_verified': False})
    result = hooks.preflight(context(), secrets(), 'config')
    assert any(e['status'] == 'failed' and 'rental' in e['message'] for e in result['events'])


@pytest.mark.parametrize('state,wrong_id,wrong_tag', [('CREATING',False,False),('ACTIVE',True,False),('ACTIVE',False,True)])
def test_platform_mismatch_prevents_admin_assignment(monkeypatch, state, wrong_id, wrong_tag):
    client = NS(settings=NS(aidp_platform_id='platform', compartment_id='compartment'), _oci_config={}, signer='operator')
    platform = NS(id='wrong' if wrong_id else 'platform', compartment_id='compartment', lifecycle_state=state,
        freeform_tags={'deployment_binding': 'foreign' if wrong_tag else OP})
    sdk = Mock()
    sdk.get_ai_data_platform.return_value.data = platform
    monkeypatch.setattr(hooks.oci.ai_data_platform, 'AiDataPlatformClient', lambda *a, **k: sdk)
    with pytest.raises(AidpProvisionConflict): hooks._platform(client, newly_owned=True, deployment_binding=OP)


def test_postapply_rejects_changed_selected_admin_before_install():
    c = context()
    c['terraform_outputs'] = {'admin_user_ocid': 'foreign',
        'region': c['region'], 'owned_ai_data_platform': True, 'owned_artifacts_bucket': True}
    with pytest.raises(AidpProvisionConflict): hooks.post_apply(c, secrets(), 'config')


def test_runner_failure_never_serializes_secret_or_exception_body(tmp_path, monkeypatch, capsys):
    marker = 'private-body-must-never-be-logged'
    paths = {name: tmp_path / (name + '.json') for name in ['context', 'secrets', 'result']}
    paths['context'].write_text(json.dumps(context()))
    paths['secrets'].write_text(json.dumps({'inputs': {'apify_token': marker}}))
    for key, name in [('DEPLOY_STUDIO_CONTEXT','context'),('DEPLOY_STUDIO_SECRETS','secrets'),('DEPLOY_STUDIO_RESULT','result')]:
        monkeypatch.setenv(key, str(paths[name]))
    monkeypatch.setenv('DEPLOY_STUDIO_OCI_CONFIG', 'not-a-real-config')
    def failure(*args): raise ValueError(marker)
    monkeypatch.setattr(hooks, 'preflight', failure)
    assert hooks.run_hook('preflight') == 1
    assert marker not in paths['result'].read_text() + capsys.readouterr().out
    assert json.loads(paths['result'].read_text())['events'][0]['status'] == 'failed'


def test_transient_real_preflight_id_is_not_used_as_operation_identity(native):
    c = context()
    c['deployment_id'] = 'preflight-123456abcdef'
    result = hooks.preflight(c, secrets(), 'config')
    assert 'deployment_id' not in result['inputs']
    with pytest.raises(ValueError, match='durable'):
        hooks.operation_id(c['deployment_id'])
    assert hooks.operation_id(DEPLOYMENT) == hooks.operation_id(DEPLOYMENT)
    assert hooks.operation_id(DEPLOYMENT) != hooks.operation_id('dep_abcdef0123456789')


def test_existing_aidp_cannot_be_bound_to_a_new_compartment(native):
    c = context('existing')
    c['compartment_mode'] = 'new'
    with pytest.raises(AidpProvisionConflict, match='existing compartment'):
        hooks.preflight(c, secrets(), 'config')


@pytest.mark.parametrize('mode,bucket', [('new','new'),('new','existing'),('existing','new')])
def test_fresh_maintenance_request_cannot_create_platform_or_bucket(monkeypatch, mode, bucket):
    c = context(mode)
    c['inputs'].update(lifecycle_action='update', artifacts_bucket_mode=bucket)
    monkeypatch.setattr(hooks.oci.config, 'from_file', lambda *args: pytest.fail('Reject before credentials or native calls'))
    with pytest.raises(AidpProvisionConflict, match='cannot create infrastructure'):
        hooks.preflight(c, {}, 'config')


@pytest.mark.parametrize('foreign_bucket,pending_memory,revoked_admin', [(False,False,False), (True,False,False), (False,True,False), (False,False,True)])
def test_postapply_binds_exact_owned_bucket_before_install(monkeypatch, foreign_bucket, pending_memory, revoked_admin):
    memory = Mock(return_value='ocid1.autonomousdatabase.oc1..synthetic')
    if pending_memory:
        memory.side_effect = AidpProvisionConflict('Memory association is unverified')
    monkeypatch.setattr(hooks, 'ensure_memory', memory)
    auth = Mock()
    if revoked_admin:
        auth.side_effect = AidpProvisionConflict('Selected administrator was revoked')
    monkeypatch.setattr(hooks, 'verify_administrator', auth)
    c = context()
    c['compartment'] = {'id': 'ocid1.compartment.oc1..synthetic'}
    binding = hashlib.sha256(':'.join(['tenancy', c['region'], c['compartment']['id'], '89abcdef', DOMAIN, USER]).encode()).hexdigest()
    c['terraform_outputs'] = {**c['inputs'], 'region': c['region'], 'ai_data_platform_ocid': 'ocid1.aidataplatform.oc1..synthetic',
        'workspace_name': 'exact-new-name', 'workspace_key': '', 'compartment_id': c['compartment']['id'],
        'objectstorage_namespace': 'namespace', 'owned_ai_data_platform': True, 'owned_artifacts_bucket': True,
        'deployment_binding': binding}
    class Client:
        def __init__(self, settings):
            self.settings = settings
            self._oci_config = {'tenancy': 'tenancy'}
            bucket = NS(name='artifacts', namespace='namespace', compartment_id=c['compartment']['id'],
                freeform_tags={'deployment_binding': 'foreign' if foreign_bucket else binding})
            self.object_storage = NS(get_bucket=lambda *args: NS(data=bucket))
        def _workspace(self): return {'key': 'resolved-exact-key'}
        async def close(self): pass
    monkeypatch.setattr(hooks, 'MarketAidpClient', Client)
    platform = Mock()
    monkeypatch.setattr(hooks, '_platform', platform)
    monkeypatch.setattr(hooks, 'check_apify', lambda token: {'reads_verified': True})
    install = Mock(return_value={'status': 'active', 'phase': 'ready', 'operation_id': hooks.operation_id(DEPLOYMENT)})
    monkeypatch.setattr(hooks, 'install', install)
    if foreign_bucket:
        with pytest.raises(AidpProvisionConflict, match='not owned'):
            hooks.post_apply(c, secrets(), 'config')
        install.assert_not_called()
        memory.assert_not_called()
    elif pending_memory:
        with pytest.raises(AidpProvisionConflict, match='association'):
            hooks.post_apply(c, secrets(), 'config')
        install.assert_not_called()
    elif revoked_admin:
        with pytest.raises(AidpProvisionConflict, match='revoked'):
            hooks.post_apply(c, secrets(), 'config')
        memory.assert_not_called()
        install.assert_not_called()
    else:
        result = hooks.post_apply(c, secrets(), 'config')
        assert result['outputs']['research_enabled'] is False
        assert result['outputs']['deployment_surface'] == 'TEST'
        assert result['outputs']['operation_id'] == hooks.operation_id(DEPLOYMENT)
        assert install.call_args.args[0].settings.aidp_workspace_key == 'resolved-exact-key'
        assert install.call_args.kwargs['new_platform'] is True
        assert platform.call_args.kwargs['deployment_binding'] == binding
        memory.assert_called_once()
        assert auth.call_args.kwargs == {'roles_required':False}


def test_model_change_produces_new_automatic_maintenance_identity():
    c = context('existing')
    first = hooks.maintenance_operation_id(c, 'update', 'package-sha')
    assert hooks.maintenance_operation_id(c, 'update', 'package-sha') == first
    c['inputs']['agent_model_id'] = 'different-model'
    assert hooks.maintenance_operation_id(c, 'update', 'package-sha') != first


def test_maintenance_rejects_new_memory_before_loading_credentials(monkeypatch):
    c = context('existing')
    c['inputs'].update(lifecycle_action='update', artifacts_bucket_mode='existing', autonomous_database_mode='new')
    monkeypatch.setattr(hooks.oci.config, 'from_file', lambda *args: pytest.fail('Must reject before credentials'))
    with pytest.raises(AidpProvisionConflict, match='cannot create infrastructure'):
        hooks.preflight(c, {}, 'config')
