"""Deploy Studio maintenance dispatch never invokes installer/bootstrap or paid effects."""
import hashlib
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import UUID

import pytest
from ai_market_intelligence import hooks
from ai_market_intelligence.errors import AidpProvisionConflict
from tests.test_hooks import context, secrets, USER, DOMAIN, DEPLOYMENT, native as preflight_native

OP='11111111-1111-4111-8111-111111111111'
SESSION='22222222-2222-4222-8222-222222222222'


def deployment(action='update', deployment_id=DEPLOYMENT):
    c=context()
    c['deployment_id']=deployment_id
    c['inputs']['lifecycle_action']=action
    c['compartment']={'id':'ocid1.compartment.oc1..synthetic'}
    suffix=''.join(x for x in deployment_id.lower() if x.isalnum())[-8:]
    binding=hashlib.sha256(':'.join(['tenancy',c['region'],c['compartment']['id'],suffix,DOMAIN,USER]).encode()).hexdigest()
    c['terraform_outputs']={**c['inputs'],'region':c['region'],
        'ai_data_platform_ocid':'ocid1.aidataplatform.oc1..synthetic','workspace_name':'exact-new-name',
        'workspace_key':'exact-key','compartment_id':c['compartment']['id'],'objectstorage_namespace':'namespace',
        'owned_ai_data_platform':True,'owned_artifacts_bucket':True,'deployment_binding':binding}
    return c


@pytest.fixture
def native(monkeypatch):
    from ai_market_intelligence import lifecycle, maintenance
    clients=[]
    class Client:
        def __init__(self,settings):
            self.settings=settings
            self._oci_config={'tenancy':'tenancy'}
            self.closed=False
            bucket=NS(name='artifacts',namespace='namespace',compartment_id=settings.compartment_id,
                freeform_tags={'deployment_binding':deployment()['terraform_outputs']['deployment_binding']})
            self.object_storage=NS(get_bucket=lambda *args:NS(data=bucket))
            clients.append(self)
        def _workspace(self): return {'key':'exact-key'}
        def _read_installation(self,*args): return {'maintenance':{'operation_id':OP}}
        async def close(self): self.closed=True
    monkeypatch.setattr(hooks,'MarketAidpClient',Client)
    monkeypatch.setattr(hooks,'_platform',Mock())
    preflight=Mock(return_value={'reads_verified':True,'apify_starts':0})
    monkeypatch.setattr(hooks,'check_apify',preflight)
    install=Mock(side_effect=AssertionError('Maintenance cannot install'))
    monkeypatch.setattr(hooks,'install',install)
    bootstrap=Mock(side_effect=AssertionError('Maintenance cannot grant roles'))
    monkeypatch.setattr(lifecycle,'bind_new_administrator',bootstrap)
    def result(client,action,operation_id,**kwargs):
        return {'status':'rolled_back' if action=='rollback' else 'completed','phase':'ready',
            'operation_id':operation_id,'action':action,'research_enabled':False,'deployment_surface':'TEST'}
    maintain=Mock(side_effect=result)
    monkeypatch.setattr(maintenance,'maintain',maintain)
    return NS(clients=clients,maintain=maintain,preflight=preflight,install=install,bootstrap=bootstrap)


@pytest.mark.parametrize('action',['update','redeploy','rollback'])
def test_exact_explicit_operation_session_and_paused_outputs(native,action):
    c=deployment(action)
    c['inputs'].update(operation_request_id=OP,test_session_id=SESSION)
    result=hooks.post_apply(c,secrets(),'not-a-real-config')
    call=native.maintain.call_args
    assert call.args==(native.clients[0],action,OP)
    assert call.kwargs.get('test_session_id')==SESSION
    native.install.assert_not_called()
    native.bootstrap.assert_not_called()
    if action=='rollback':
        native.preflight.assert_not_called()
    else:
        assert native.preflight.call_args.args[0].get_secret_value()==secrets()['inputs']['apify_token']
    assert result['outputs']['research_enabled'] is False
    assert result['outputs']['deployment_surface']=='TEST'
    assert result['outputs']['operation_id']==OP
    assert result['outputs']['installation_status']==('rolled_back' if action=='rollback' else 'completed')
    assert native.clients[0].closed
    if action=='rollback':
        assert result['events'][0]['level']=='success'
        assert 'installed' not in result['events'][0]['message'].lower()


@pytest.mark.parametrize('action',['update','redeploy'])
def test_automatic_identity_is_stable_and_uses_full_deployment(native,action):
    c=deployment(action)
    first=hooks.post_apply(c,secrets(),'config')['outputs']['operation_id']
    again=hooks.post_apply(c,secrets(),'config')['outputs']['operation_id']
    other=hooks.post_apply(deployment(action,'dep_aaaaaaaa89abcdef'),secrets(),'config')['outputs']['operation_id']
    assert str(UUID(first))==first and first==again and first!=other


def test_automatic_identity_distinguishes_action(native):
    update=hooks.post_apply(deployment('update'),secrets(),'config')['outputs']['operation_id']
    redeploy=hooks.post_apply(deployment('redeploy'),secrets(),'config')['outputs']['operation_id']
    assert update!=redeploy


def test_explicit_uuid_can_start_another_same_source_redeploy(native):
    c=deployment('redeploy')
    first=hooks.post_apply(c,secrets(),'config')['outputs']['operation_id']
    c['inputs']['operation_request_id']=OP
    second=hooks.post_apply(c,secrets(),'config')['outputs']['operation_id']
    assert first!=second and second==OP


def test_rollback_without_request_reuses_persisted_maintenance_operation(native):
    result=hooks.post_apply(deployment('rollback'),secrets(),'config')
    assert result['outputs']['operation_id']==OP
    assert native.maintain.call_args.args[2]==OP


@pytest.mark.parametrize('action',['update','redeploy'])
def test_failed_apify_reads_never_enter_maintenance_or_installer(native,action):
    native.preflight.return_value={'reads_verified':False,'apify_starts':0}
    with pytest.raises(AidpProvisionConflict):
        hooks.post_apply(deployment(action),secrets(),'config')
    native.maintain.assert_not_called()
    native.install.assert_not_called()
    native.bootstrap.assert_not_called()
    assert all(c.closed for c in native.clients)


@pytest.mark.parametrize('action',['unsupported','Update','delete'])
def test_unknown_action_fails_before_any_mutation(native,action):
    with pytest.raises(ValueError):
        hooks.post_apply(deployment(action),secrets(),'config')
    native.maintain.assert_not_called()
    native.install.assert_not_called()


@pytest.mark.parametrize('action',['update','redeploy'])
def test_failed_candidate_restoration_is_reported_as_error(native,action):
    native.maintain.side_effect=None
    native.maintain.return_value={'status':'rolled_back','phase':'ready','operation_id':OP,
        'action':action,'research_enabled':False,'deployment_surface':'TEST'}
    result=hooks.post_apply(deployment(action),secrets(),'config')
    assert result['outputs']['installation_status']=='rolled_back'
    assert result['events'][0]['level']=='error'
    assert 'failed' in result['events'][0]['message'].lower()
    assert result['outputs']['research_enabled'] is False


def test_new_package_changes_automatic_operation_but_not_explicit_request():
    c=deployment('update')
    first=hooks.maintenance_operation_id(c,'update','a'*64)
    changed=hooks.maintenance_operation_id(c,'update','b'*64)
    assert first!=changed
    c['inputs']['operation_request_id']=OP
    assert hooks.maintenance_operation_id(c,'update','a'*64)==OP
    assert hooks.maintenance_operation_id(c,'update','b'*64)==OP


@pytest.mark.parametrize('requested',['bad','AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'])
def test_invalid_requested_operation_cannot_reach_maintenance(native,requested):
    c=deployment('update')
    c['inputs']['operation_request_id']=requested
    with pytest.raises(ValueError):
        hooks.post_apply(c,secrets(),'config')
    native.maintain.assert_not_called()
    native.install.assert_not_called()


def test_rollback_cannot_replace_recorded_operation(native):
    c=deployment('rollback')
    c['inputs']['operation_request_id']=SESSION
    with pytest.raises(AidpProvisionConflict,match='recorded'):
        hooks.post_apply(c,secrets(),'config')
    native.maintain.assert_not_called()


@pytest.mark.parametrize('action,level,exit_code', [('update','error',1),('redeploy','error',1),('rollback','success',0)])
def test_runner_exit_and_output_keep_rollback_outcome_truthful(native,monkeypatch,tmp_path,action,level,exit_code):
    native.maintain.side_effect=None
    native.maintain.return_value={'status':'rolled_back','phase':'ready','operation_id':OP,
        'action':action,'research_enabled':False,'deployment_surface':'TEST'}
    files={name:tmp_path/(name+'.json') for name in ('context','secrets','output')}
    files['context'].write_text(json.dumps(deployment(action)))
    files['secrets'].write_text(json.dumps(secrets()))
    for name,path in files.items():
        monkeypatch.setenv('DEPLOY_STUDIO_'+name.upper(),str(path))
    monkeypatch.setenv('DEPLOY_STUDIO_OCI_CONFIG','config')
    assert hooks.run_hook('post-apply')==exit_code
    result=json.loads(files['output'].read_text())
    assert result['outputs']['installation_status']=='rolled_back'
    assert result['events'][0]['level']==level
    assert secrets()['inputs']['apify_token'] not in files['output'].read_text()


def test_rollback_postapply_restores_without_apify_or_protected_token(native):
    native.preflight.side_effect=AssertionError('Apify must not gate rollback')
    result=hooks.post_apply(deployment('rollback'),{},'config')
    native.preflight.assert_not_called()
    native.install.assert_not_called()
    native.bootstrap.assert_not_called()
    assert result['outputs']['installation_status']=='rolled_back'
    assert result['outputs']['research_enabled'] is False
    assert result['outputs']['apify_execution_permission']=='not_verified'
    assert native.clients[0].closed


@pytest.mark.parametrize('revoked',[False,True])
def test_rollback_preflight_skips_apify_model_but_requires_identity_and_roles(preflight_native,monkeypatch,revoked):
    class Client:
        def __init__(self,settings): pass
        def _workspace(self): return {'key':'exact-key'}
        async def close(self): pass
    monkeypatch.setattr(hooks,'MarketAidpClient',Client)
    monkeypatch.setattr(hooks,'_platform',lambda c:None)
    apify=Mock(side_effect=AssertionError('Rollback must not call Apify'))
    monkeypatch.setattr(hooks,'check_apify',apify)
    c=context('existing')
    c['inputs']['lifecycle_action']='rollback'
    if revoked:
        preflight_native.auth.side_effect=[None,AidpProvisionConflict('administrator revoked')]
        with pytest.raises(AidpProvisionConflict,match='revoked'):
            hooks.preflight(c,{},'config')
    else:
        result=hooks.preflight(c,{},'config')
        assert any(event['status']=='warning' and 'Rollback' in event['message'] for event in result['events'])
    assert preflight_native.auth.call_count==2
    assert preflight_native.auth.call_args.kwargs=={}
    preflight_native.model.get_model.assert_not_called()
    apify.assert_not_called()
