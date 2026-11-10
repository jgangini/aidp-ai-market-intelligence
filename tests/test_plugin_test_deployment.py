import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_market_intelligence.errors import AidpProvisionConflict,AidpProvisionPending
from ai_market_intelligence.installation.plugin_test import PluginTestDeployment
from tests.test_maintenance import OP,FakeClient,FakeNative,candidate,NEW,OLD,client,run,additive_candidate


def driver_fixture(tmp_path):
    root=tmp_path/'project';root.mkdir()
    scripts=tmp_path/'plugin/scripts';(scripts/'aidp_agent_flow').mkdir(parents=True)
    (scripts/'aidp_agent_flow/cli.py').write_text('pass\n',encoding='utf-8')
    (root/'aidp-agent.toml').write_text('''[agent]
entry = "old/market_intelligence.py"
dependencies = "old/requirements.txt"
[native]
workspace_key = "workspace"
agent_key = "agent"
compute_key = "compute"
remote_root = "/Workspace/.artifact/ai_market_intelligence/agents"
metadata_root = "/Workspace/.artifact/ai_market_intelligence/agents"
''',encoding='utf-8')
    driver=object.__new__(PluginTestDeployment)
    driver.project=root;driver.scripts=scripts;driver.operation_id=OP;driver.scripts_sha256='a'*64
    return driver


def test_candidate_preparation_preserves_project_identity_and_rejects_changed_bytes(tmp_path):
    driver=driver_fixture(tmp_path);state=FakeClient().saved
    state['maintenance']={'operation_id':OP}
    binding=driver.driver_binding()
    files={**candidate(NEW),'agent-manifest.json':b'ownership'}
    driver.prepare_candidate(state,files)
    assert driver.driver_binding()==binding
    import tomllib
    config=tomllib.loads((driver.project/'aidp-agent.toml').read_text(encoding='utf-8'))
    path=driver.project/config['agent']['entry'];assert path.read_bytes()==NEW
    path.write_bytes(b'foreign')
    with pytest.raises(AidpProvisionConflict,match='candidate changed'):
        driver.prepare_candidate(state,files)


def test_preparation_rejects_another_native_identity_before_writing_candidate(tmp_path):
    driver=driver_fixture(tmp_path);state=FakeClient().saved
    state['maintenance']={'operation_id':OP};state['resources']['agent_key']='foreign'
    with pytest.raises(AidpProvisionConflict,match='does not select'):
        driver.prepare_candidate(state,candidate(NEW))
    assert not (driver.project/'.aidp-local').exists()


@pytest.mark.parametrize('applied',[False,True])
@pytest.mark.parametrize('addition',[False,True])
def test_guarded_sync_accepts_verified_package_even_when_already_uploaded(tmp_path,applied,addition):
    from ai_market_intelligence.runtime.package_identity import file_hashes,inventory_digest
    driver=driver_fixture(tmp_path);state=FakeClient().saved
    files={**candidate(NEW),'agent-manifest.json':b'owner'}
    before={**candidate(NEW),'agent-manifest.json':b'old owner'}
    if addition:
        files['core/new_module.py'] = b'new module\n'
        from ai_market_intelligence.runtime.package_identity import package_manifest
        files.pop('package-manifest.json')
        files['package-manifest.json'] = json.dumps(package_manifest({name: raw for name, raw in files.items()
            if name != 'agent-manifest.json'})).encode()
    state['maintenance']={'operation_id':OP,'before_hashes':file_hashes(before),'target_hashes':file_hashes(files)}
    expected=inventory_digest(file_hashes(files));calls=[]
    def cli(*args):
        calls.append(args)
        path=driver.project/args[args.index('--expected-inventory')+1]
        guard=json.loads(path.read_bytes())
        assert guard['package_sha256']==expected
        assert set(guard['files'])==set(files)
        if addition:
            assert guard['files']['core/new_module.py'] == [file_hashes(files)['core/new_module.py']]
        return {'applied':applied,'readback_verified':True,'preconditions_checked':True,'package_sha256':expected}
    driver.cli=cli
    driver.sync_candidate(state,files)
    driver.sync_candidate(state,files)
    assert len(calls)==2


def test_owner_creates_addition_before_plugin_sync_and_removes_it_before_rollback(additive_candidate):
    client = additive_candidate
    client.fail_new_probe = True
    binding={'kind':'plugin_cli','project':'fixture-project','scripts':'fixture-scripts',
             'scripts_sha256':'a'*64,'project_sha256':'b'*64}
    class Driver(FakeNative):
        deployment_driver='plugin_cli'
        def driver_binding(self): return binding
        def sync_candidate(self,state,files):
            from ai_market_intelligence.maintenance import agent_root
            path = agent_root(state) + '/core/new_module.py'
            if 'core/new_module.py' in files:
                assert client.files[path] == files['core/new_module.py']
            else:
                assert path not in client.files
        def refresh(self,*args,metadata_descriptor,**kwargs):
            return super().refresh(*args,**kwargs)
        def reconcile_refresh(self,intent): return True
    assert run(client, native_factory=lambda _: Driver(client))['status'] == 'rolled_back'
    assert client.refreshes == [NEW, OLD]


def test_plugin_failure_never_becomes_a_successful_owner_commit(client,tmp_path):
    binding={'kind':'plugin_cli','project':str(tmp_path),'scripts':str(tmp_path/'scripts'),
             'scripts_sha256':'a'*64,'project_sha256':'b'*64}
    class Driver(FakeNative):
        deployment_driver='plugin_cli'
        def driver_binding(self):return copy.deepcopy(binding)
        def sync_candidate(self,state,files):
            raise AidpProvisionConflict('plugin refused sync')
    with pytest.raises(AidpProvisionConflict,match='refused sync'):
        run(client,native_factory=lambda _:Driver(client))
    assert client.saved['pack_sha256']=='old-pack' and client.refreshes==[]
    assert client.saved['maintenance']['phase']=='target_files'


def test_plugin_driver_is_pinned_and_its_real_receipt_remains_distinct(client,tmp_path):
    binding={'kind':'plugin_cli','project':str(tmp_path),'scripts':str(tmp_path/'scripts'),
             'scripts_sha256':'a'*64,'project_sha256':'b'*64}
    class Driver(FakeNative):
        deployment_driver='plugin_cli'
        def driver_binding(self):return copy.deepcopy(binding)
        def sync_candidate(self,state,files):
            root=work_root(state)
            for name,raw in files.items():client._upload_file(state['workspace'],root+'/'+name,raw,repair_drift=True)
        def refresh(self,*args,metadata_descriptor,**kwargs):
            assert metadata_descriptor==client.files[work_root(client.saved)+'/agent-manifest.json']
            result=super().refresh(*args,**kwargs)
            return {'driver':'plugin_cli','plugin_submission':result}
        def reconcile_refresh(self,intent):return True
    from ai_market_intelligence.maintenance import agent_root as work_root
    result=run(client,native_factory=lambda _:Driver(client))
    assert result['status']=='completed'
    assert client.saved['resources']['test_receipt']['driver']=='plugin_cli'
    assert client.saved['resources']['test_receipt']['plugin_submission']['deployment_type']=='TEST'
    binding['scripts_sha256']='c'*64
    # Same completed operation is idempotent; an unfinished operation must keep its driver.
    client.saved['maintenance'].update(status='running',phase='target_files')
    with pytest.raises(AidpProvisionConflict,match='pinned plugin'):
        run(client,native_factory=lambda _:Driver(client))


@pytest.mark.parametrize('fresh,state,ready,expected',[
    (True,'RECONCILED',True,True),
    (True,'FAILED_RECONCILED',False,False),
    (True,'SUBMITTED',False,'pending'),
    (True,'RECONCILED',False,'conflict'),
])
def test_reconciliation_never_repeats_an_existing_plugin_post(tmp_path,fresh,state,ready,expected):
    driver=driver_fixture(tmp_path);calls=[]
    def cli(*args):
        calls.append(args)
        if args==('status',):return {'operations':[{'kind':'test-refresh','operation_key':'fresh','state':state}],
                                    'test':[{'key':'before','lifecycleState':'ACTIVE'}]}
        if state=='SUBMITTED':raise AssertionError('A creating TEST permits status, not another deployment plan')
        return {'package_sha256':'pinned-package','up_to_date':ready}
    driver.cli=cli
    intent={'plugin_package_sha256':'pinned-package','plugin_operations_before':[],
            'previous_identity':{'key':'before'},'recover_failed':False}
    if expected=='pending':
        with pytest.raises(AidpProvisionPending):driver.reconcile_refresh(intent)
    elif expected=='conflict':
        with pytest.raises(AidpProvisionConflict):driver.reconcile_refresh(intent)
    else:assert driver.reconcile_refresh(intent) is expected
    assert not any('--apply' in args for args in calls)


def test_resume_after_owner_intent_before_plugin_post_submits_only_once(tmp_path):
    driver=driver_fixture(tmp_path);calls=[];submitted=[]
    previous=FakeClient().row
    def cli(*args):
        calls.append(args)
        if args==('status',):
            return {'operations':([{'kind':'test-refresh','operation_key':'one-operation','state':'SUBMITTED'}]
                                  if submitted else []),'test':[previous]}
        if '--apply' in args:submitted.append(True);return {'applied':True}
        return {'package_sha256':'pinned','up_to_date':False}
    driver.cli=cli;driver.test_deployments=lambda *args:[previous]
    from ai_market_intelligence.installation.playground import deployment_identity
    intent={'plugin_package_sha256':'pinned','plugin_operations_before':[],
            'previous_identity':deployment_identity(previous),'recover_failed':False}
    for _ in range(2):
        with pytest.raises(AidpProvisionPending):driver.reconcile_refresh(intent)
    assert len(submitted)==1


def test_foreign_test_after_owner_intent_cannot_start_another_plugin_post(tmp_path):
    driver=driver_fixture(tmp_path);calls=[]
    driver.cli=lambda *args: calls.append(args) or ({'operations':[],'test':[]}
        if args==('status',) else {'package_sha256':'pinned','up_to_date':False})
    previous=FakeClient().row;changed={**previous,'key':'foreign'}
    driver.test_deployments=lambda *args:[changed]
    from ai_market_intelligence.installation.playground import deployment_identity
    with pytest.raises(AidpProvisionConflict,match='outside the pinned'):
        driver.reconcile_refresh({'plugin_package_sha256':'pinned','plugin_operations_before':[],
            'previous_identity':deployment_identity(previous),'recover_failed':False})
    assert not any('--apply' in args for args in calls)


def test_refresh_records_owner_intent_before_the_plugin_write(tmp_path):
    driver=driver_fixture(tmp_path);owner=[];calls=[]
    driver.test_deployments=lambda *args:[FakeClient().row]
    def cli(*args):
        calls.append(args)
        if args==('status',):return {'test':[{'lifecycleState':'ACTIVE'}],'operations':[]}
        if '--apply' in args:
            assert owner and owner[0]['driver']=='plugin_cli'
            return {'applied':True,'operation_key':'canonical-plugin-operation'}
        return {'package_sha256':'pinned','up_to_date':False}
    driver.cli=cli
    result=driver.refresh('workspace','agent','compute',NEW,assets={},expected_workspace='workspace',
        agent_root='/Workspace/.artifact/ai_market_intelligence/agents',initialize=False,
        on_submit=lambda value:owner.append(value),metadata_descriptor=b'owner')
    assert result['plugin_submission']['operation_key']=='canonical-plugin-operation'
    assert result['driver']=='plugin_cli' and len(owner)==1
