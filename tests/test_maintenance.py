import copy
import hashlib
import json
import sys
from urllib.parse import unquote
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

from ai_market_intelligence import maintenance as work
from ai_market_intelligence.client import AGENT_ROOT, ROOT, MarketAidpClient, market_probe_receipt, RUNTIME_DISTRIBUTIONS
from ai_market_intelligence.runtime.package_identity import package_manifest, source_identity, file_hashes
from ai_market_intelligence.runtime.runtime_versions import TARGET_GRAPH_PACKAGES
from ai_market_intelligence.errors import AidpProvisionConflict, AidpProvisionError, AidpProvisionPending


INSTALL = '11111111-1111-4111-8111-111111111111'
OP = '22222222-2222-4222-8222-222222222222'
OTHER = '33333333-3333-4333-8333-333333333333'
ENDPOINT = 'https://gateway.aidp.us-chicago-1.oci.oraclecloud.com/market'
OLD = b'old paused source\n'
NEW = b'new paused source\n'
REQ = b'httpx==0.28.1\n'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def candidate(source):
    files = {'market_intelligence.py': source, 'requirements.txt': REQ,
             'core/runtime.py': b'old authoring\n' if source == OLD else b'new authoring\n'}
    files['package-manifest.json'] = encoded(package_manifest(files))
    return files


def assets_for(source):
    return {name: raw for name, raw in candidate(source).items() if name != 'market_intelligence.py'}


def identity(source):
    return source_identity(candidate(source))


class FakeNative:
    def __init__(self, client):
        self.client = client
        self.session = NS(close=lambda: None)

    def test_deployments(self, workspace, agent_key):
        assert workspace == 'workspace' and agent_key == 'agent'
        return [copy.deepcopy(self.client.row)]

    def _request(self, method, path):
        session = path.rsplit('/', 1)[-1]
        assert method == 'GET' and path.startswith('/workspaces/workspace/agentFlowSessions/') and session in self.client.sessions
        return {'key': session, 'agentFlowKey': 'agent', 'agentFlowComputeKey': 'compute',
                'endpointUrl': ENDPOINT, 'deploymentType': 'TEST', 'lifecycleState': 'ACTIVE'}

    def refresh(self, workspace, agent, compute, source, *, assets, expected_workspace, agent_root, initialize, on_submit,
                metadata_root=None):
        assert (workspace, agent, compute, expected_workspace, agent_root, initialize) == (
            'workspace', 'agent', 'compute', 'workspace', work.agent_root(self.client.saved), False)
        assert metadata_root in (None, work.metadata_root(self.client.saved))
        assert assets == (assets_for(source) if self.client.saved.get('layout_version') == 2 else {'requirements.txt': REQ})
        assert self.client.files[agent_root + '/market_intelligence.py'] == source
        digest = sha(encoded(file_hashes({'market_intelligence.py': source, **assets})))
        on_submit({'package_sha256': digest, 'previous_version': self.client.row['deploymentVersion']})
        saved = self.client.saved['maintenance']
        assert saved.get('target_intent') or saved.get('rollback_intent')
        self.client.refreshes.append(source)
        if self.client.lose_refresh:
            self.client.lose_refresh = False
            raise TimeoutError('response lost after submit')
        self.client.publish_revision()
        return {'package_sha256': digest, 'compute_key': compute, 'deployment_type': 'TEST'}


class FakeClient:
    def __init__(self):
        self.settings = NS(admin_user_ocid='owner', aidp_region='us-chicago-1', agent_model_id='selected-model', compartment_id='compartment')
        self.files = {}
        self.refreshes = []
        self.probes = []
        self.probe_sessions = []
        self.sessions = {'session', 'other-existing-session'}
        self.uploads = []
        self.reads = []
        self.lose_refresh = False
        self.fail_new_probe = False
        self.langgraph_versions = {identity(NEW): '1.2.4', identity(OLD): '1.2.4'}
        self.fail_path = None
        self.compute_state = 'ACTIVE'
        self.compute_starts = []
        self.start_enters_transition = True
        self.deletions = []
        self.lose_delete_response = False
        self.lose_start_response = False
        self.production = []
        self.revoked = False
        self.revision = 0
        self.row = self.make_row()
        hashes = file_hashes(candidate(OLD))
        self.saved = {'installer_version': 2, 'layout_version': 2, 'module_id': 'ai_market_intelligence', 'publication_root': ROOT,
            'workspace': 'workspace', 'owner': 'owner', 'operation_id': INSTALL, 'pack_sha256': 'old-pack',
            'phase': 'ready', 'status': 'active', 'reuse_existing': True, 'runtime_config': self._selected_runtime_config(),
            'resources': {'agent_key': 'agent', 'agent_compute_key': 'compute', 'shared_oci_credential_key': 'shared',
                          'apify_credential_key': 'apify', 'compute_creation_operation': INSTALL, 'source_sha256': identity(OLD), 'package_hashes': hashes,
                          'test_session_key': 'session', 'test_intent': {'package_sha256': sha(encoded(hashes))},
                          'test_verified': {'deployment_identity': work.deployment_identity(self.row)}}}
        self.files = {AGENT_ROOT + '/' + name: raw for name, raw in candidate(OLD).items()}
        self.files[AGENT_ROOT + '/agent-manifest.json'] = encoded(self._descriptor(self.saved))
        self.original_files = copy.deepcopy(self.files)
        self.original_resources = copy.deepcopy(self.saved['resources'])

    def make_row(self):
        return {'key': 'test-' + str(self.revision), 'timeCreated': f'2026-11-05T12:00:{self.revision:02d}Z',
                'timeUpdated': f'2026-11-05T12:00:{self.revision:02d}Z', 'deploymentVersion': str(self.revision),
                'agentFlowKey': 'agent', 'agentFlowComputeKey': 'compute', 'deploymentType': 'TEST',
                'lifecycleState': 'ACTIVE', 'endpointUrl': ENDPOINT}

    def publish_revision(self):
        self.revision += 1
        self.row = self.make_row()

    def _workspace(self):
        return {'key': 'workspace'}

    def _read_installation(self, workspace):
        return copy.deepcopy(self.saved)

    def _save(self, state):
        self.saved = copy.deepcopy(state)

    def _descriptor(self, state):
        return MarketAidpClient._descriptor(self, state)

    def _runtime_config(self, state):
        return MarketAidpClient._runtime_config(self, state)

    def _selected_runtime_config(self):
        return MarketAidpClient._selected_runtime_config(self)

    def _workspace_bytes(self, workspace, path, *, phase):
        self.reads.append(path)
        return self.files.get(path)

    _workspace_object_type = staticmethod(MarketAidpClient._workspace_object_type)

    def _ensure_folder(self, workspace, path):
        pass

    def _upload_file(self, workspace, path, content, *, repair_drift):
        self.uploads.append(path)
        self.files[path] = content
        if path == self.fail_path:
            self.fail_path = None
            raise TimeoutError('file committed; response lost')

    def _agents(self, workspace, name):
        return [{'key': 'agent'}]

    def _request(self, method, path, **kwargs):
        if '/objects/' in path:
            name = unquote(path.split('/objects/', 1)[1])
            if method == 'GET':
                return self.files.get(name), {'etag': 'fixture-etag', 'object-type': 'FILE'}
            assert method == 'DELETE' and kwargs['headers'] == {'if-match': 'fixture-etag'}
            assert self.saved['maintenance']['rollback_delete_intents']
            self.deletions.append(name)
            self.files.pop(name, None)
            if self.lose_delete_response:
                self.lose_delete_response = False
                raise TimeoutError('delete committed; response lost')
            return None
        if method == 'POST':
            assert path == '/workspaces/workspace/clusters/compute/actions/start' and kwargs['payload'] == {}
            assert self.saved['maintenance']['compute_start_intent']['compute_key'] == 'compute'
            self.compute_starts.append(kwargs['retry_scope'])
            if self.start_enters_transition:
                self.compute_state = 'STARTING'
            if self.lose_start_response:
                self.lose_start_response = False
                raise TimeoutError('compute start accepted; response lost')
            return {}
        assert method == 'GET'
        if '/clusters/' in path:
            return {'key': 'compute', 'displayName': 'aidp_ai_market_intelligence_compute', 'type': 'AI_COMPUTE',
                    'description': 'AI Market Intelligence development compute; operation=' + INSTALL,
                    'driverConfig': {'driverShapeConfig': {'ocpus': 1, 'memoryInGBs': 16}}, 'lifecycleState': self.compute_state}
        assert path == '/workspaces/workspace/agents/agent'
        root = work.agent_root(self.saved)
        return {'key': 'agent', 'type': 'CODE', 'pathInfo': work.metadata_root(self.saved), 'entryFilePath': root + '/market_intelligence.py',
                'dependenciesFilePath': root + '/requirements.txt', 'computeKey': 'compute'}

    def _agent_deployments(self, path):
        return self.production

    def _shared_oci_credential(self):
        return {'key': 'shared'}

    def _probe_market(self, endpoint, session, source_hash):
        assert endpoint == ENDPOINT and session in self.sessions
        self.probes.append(source_hash)
        self.probe_sessions.append(session)
        if self.fail_new_probe and source_hash == identity(NEW):
            raise AidpProvisionError('TEST did not verify this paused current source and authenticated session.')
        return market_probe_receipt({'source_sha256': source_hash, 'schema_version': 1, 'research_enabled': False,
            'business_tables_required': False, 'authenticated_session_bound': True, 'model_calls': 0, 'apify_starts': 0,
            'versions': {'python': '3.11.13', 'packages': {name: self.langgraph_versions.get(source_hash, '1.2.4') if name == 'langgraph' else TARGET_GRAPH_PACKAGES.get(name, 'fixture-version') for name in RUNTIME_DISTRIBUTIONS}}}, source_hash)


@pytest.fixture
def client(monkeypatch):
    client = FakeClient()
    @contextmanager
    def lock(value, operation):
        assert value is client and operation in {OP, OTHER}
        yield
    def authorize(value):
        if value.revoked:
            raise AidpProvisionConflict('administrator revoked')
    monkeypatch.setattr(work, 'installation_lock', lock)
    monkeypatch.setattr(work, 'verify_administrator', authorize)
    monkeypatch.setattr(work, 'MarketPlaygroundClient', lambda settings: FakeNative(client))
    monkeypatch.setattr(work, '_project_credential', lambda value: {'key': 'apify', 'description': 'AI Market Intelligence owned',
        'type': 'SECRET_TOKEN', 'lifecycleState': 'ACTIVE'})
    monkeypatch.setattr(work, 'agent_package', lambda config: (NEW, assets_for(NEW)))
    monkeypatch.setattr(work, 'load_package', lambda *a, **k: NS(pack_sha256='new-pack'))
    return client


def run(client, action='update', operation=OP, **kw):
    return work.maintain(client, action, operation, sleep=lambda _: None, **kw)


@pytest.fixture
def additive_candidate(client, monkeypatch):
    original = candidate
    def extended(source):
        files = original(source)
        if source == NEW:
            files['core/new_module.py'] = b'new module\n'
            files.pop('package-manifest.json')
            files['package-manifest.json'] = encoded(package_manifest(files))
        return files
    monkeypatch.setattr(sys.modules[__name__], 'candidate', extended)
    return client


def test_update_adds_absent_module_and_records_exact_before_and_target_inventories(additive_candidate):
    client = additive_candidate
    assert run(client)['status'] == 'completed'
    journal = client.saved['maintenance']
    assert journal['inventory_additions'] == ['core/new_module.py']
    assert 'core/new_module.py' not in journal['before_hashes']
    assert client.files[AGENT_ROOT + '/core/new_module.py'] == b'new module\n'
    assert client.saved['resources']['package_hashes'] == file_hashes(candidate(NEW))
    assert_resource_identity_preserved(client)


@pytest.mark.parametrize('raw', [b'foreign module\n', b'new module\n'])
def test_inventory_addition_never_adopts_preexisting_bytes(additive_candidate, raw):
    client = additive_candidate
    client.files[AGENT_ROOT + '/core/new_module.py'] = raw
    before = copy.deepcopy(client.files)
    with pytest.raises(AidpProvisionConflict, match='cannot adopt'):
        run(client)
    assert client.files == before and client.uploads == [] and client.refreshes == []
    assert 'maintenance' not in client.saved


def test_failed_candidate_removes_only_its_new_module_during_rollback(additive_candidate):
    client = additive_candidate
    client.production = [published_fixture()]
    client.fail_new_probe = True
    client.lose_delete_response = True
    result = run(client, preserve_production=True)
    assert result['status'] == 'rolled_back'
    assert client.deletions == [AGENT_ROOT + '/core/new_module.py']
    assert client.refreshes == [NEW, OLD]
    assert 'core/new_module.py' not in client.saved['resources']['package_hashes']
    assert {path: raw for path, raw in client.files.items() if path.startswith(AGENT_ROOT + '/')} == client.original_files
    assert client.production == [published_fixture()]
    assert_resource_identity_preserved(client)


def test_addition_with_lost_upload_response_resumes_without_duplicate_creation(additive_candidate):
    client = additive_candidate
    path = AGENT_ROOT + '/core/new_module.py'
    client.fail_path = path
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=2)
    assert client.saved['maintenance']['phase'] == 'target_files'
    assert run(client)['status'] == 'completed'
    assert client.uploads.count(path) == 1 and client.refreshes == [NEW]


def test_rollback_preserves_changed_addition_and_its_running_journal(additive_candidate):
    client = additive_candidate
    assert run(client)['status'] == 'completed'
    path = AGENT_ROOT + '/core/new_module.py'
    client.files[path] = b'operator change\n'
    with pytest.raises(AidpProvisionConflict, match='added artifact changed'):
        run(client, action='rollback')
    assert client.files[path] == b'operator change\n' and not client.deletions
    assert client.saved['maintenance']['phase'] == 'rollback_files'


@pytest.mark.parametrize('action', ['update', 'rollback'])
def test_identical_foreign_addition_after_backup_cannot_be_adopted_or_deleted(additive_candidate, action):
    client = additive_candidate
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=1)
    path = AGENT_ROOT + '/core/new_module.py'
    client.files[path] = b'new module\n'
    with pytest.raises(AidpProvisionConflict, match='creation intent'):
        run(client, action=action)
    assert client.files[path] == b'new module\n' and not client.deletions and not client.refreshes
    assert path not in client.uploads


def test_rollback_does_not_delete_a_folder_with_bytes_matching_an_empty_added_file(additive_candidate, monkeypatch):
    client = additive_candidate
    original = candidate
    def empty(source):
        files = original(source)
        if source == NEW:
            files['core/new_module.py'] = b''
            files.pop('package-manifest.json')
            files['package-manifest.json'] = encoded(package_manifest(files))
        return files
    monkeypatch.setattr(sys.modules[__name__], 'candidate', empty)
    assert run(client)['status'] == 'completed'
    request = client._request
    def folder(method, path, **kwargs):
        if method == 'GET' and '/objects/' in path:
            return b'', {'etag': 'fixture-etag', 'object-type': 'FOLDER'}
        return request(method, path, **kwargs)
    client._request = folder
    with pytest.raises(AidpProvisionConflict, match='added artifact changed'):
        run(client, action='rollback')
    assert not client.deletions


@pytest.mark.parametrize('tamper', ['undeclared', 'foreign_delete', 'before_inventory'])
def test_resumed_additions_require_the_exact_saved_inventory_contract(additive_candidate, tamper):
    client = additive_candidate
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=1)
    journal = client.saved['maintenance']
    if tamper == 'undeclared':
        journal.pop('inventory_additions')
    elif tamper == 'foreign_delete':
        journal['rollback_delete_intents'] = {'core/runtime.py': journal['target_hashes']['core/runtime.py']}
    else:
        journal['before_state']['resources']['package_hashes'].pop('core/runtime.py')
    before, uploads = copy.deepcopy(client.files), list(client.uploads)
    with pytest.raises(AidpProvisionConflict, match='journal is invalid'):
        run(client)
    assert client.files == before and client.uploads == uploads and not client.deletions


@pytest.mark.parametrize('change', ['directory', 'removal'])
def test_inventory_update_keeps_layout_migrations_explicit(client, monkeypatch, change):
    files = candidate(NEW)
    files.pop('package-manifest.json')
    if change == 'directory':
        files['core/new_folder/module.py'] = b'new module\n'
    else:
        files.pop('core/runtime.py')
    files['package-manifest.json'] = encoded(package_manifest(files))
    monkeypatch.setattr(work, 'agent_package', lambda _: (NEW, {name: raw for name, raw in files.items()
        if name != 'market_intelligence.py'}))
    before = copy.deepcopy(client.files)
    with pytest.raises(AidpProvisionConflict, match='layout migration'):
        run(client)
    assert client.files == before and client.uploads == [] and not client.refreshes


def published_fixture():
    return {'key':'prod-fixed','agentKey':'agent','agentComputeKey':'compute','deploymentType':'PROD',
            'lifecycleState':'ACTIVE','endpointUrl':'https://fixture.example/agent','runAsCredentialKey':'shared'}


def test_explicit_test_update_preserves_the_pinned_production_snapshot(client):
    client.production=[published_fixture()]
    before=copy.deepcopy(client.production)
    result=run(client,preserve_production=True)
    assert result['status']=='completed' and client.refreshes==[NEW]
    assert client.production==before==client.saved['maintenance']['prod_snapshot']
    assert client.saved['resources']['agent_key']=='agent'
    assert client.saved['resources']['agent_compute_key']=='compute'


def test_production_change_during_source_write_blocks_commit_and_preserves_journal(client):
    client.production=[published_fixture()]
    upload=client._upload_file
    def drift(workspace,path,content,**kwargs):
        upload(workspace,path,content,**kwargs)
        if path==AGENT_ROOT+'/market_intelligence.py':
            client.production[0]['key']='foreign-new-prod'
    client._upload_file=drift
    with pytest.raises(AidpProvisionConflict,match='PROD snapshot changed'):
        run(client,preserve_production=True)
    assert client.saved['maintenance']['status']=='running'
    assert client.saved['pack_sha256']=='old-pack' and client.refreshes==[]


def test_failed_test_candidate_can_restore_files_without_mutating_production(client):
    client.production=[published_fixture()]
    before=copy.deepcopy(client.production)
    client.fail_new_probe=True
    result=run(client,preserve_production=True)
    assert result['status']=='rolled_back' and client.refreshes==[NEW,OLD]
    assert client.production==before and client.saved['pack_sha256']=='old-pack'


def test_resume_cannot_silently_drop_the_production_snapshot_contract(client):
    client.production=[published_fixture()]
    with pytest.raises(AidpProvisionPending):
        run(client,preserve_production=True,max_steps=1)
    with pytest.raises(AidpProvisionConflict,match='explicit PROD-preserving'):
        run(client)


def test_production_change_after_test_verification_still_blocks_the_commit(client,monkeypatch):
    client.production=[published_fixture()]
    original=FakeNative.test_deployments
    def drift(native,*args):
        result=original(native,*args)
        if native.client.saved.get('maintenance',{}).get('phase')=='commit':
            native.client.production[0]['key']='foreign-publication'
        return result
    monkeypatch.setattr(FakeNative,'test_deployments',drift)
    with pytest.raises(AidpProvisionConflict,match='PROD snapshot changed'):
        run(client,preserve_production=True)
    assert client.saved['maintenance']['status']=='running'
    assert client.saved['pack_sha256']=='old-pack'


def historical_journal(client, phase):
    """A persisted v1 operation, not a v2 fixture reinterpreted at the new root."""
    from ai_market_intelligence.client import LEGACY_CONTROL_ROOT
    client.control_root = LEGACY_CONTROL_ROOT
    state = copy.deepcopy(client.saved)
    state.pop('layout_version')
    state['resources'].pop('package_hashes')
    state['resources']['source_sha256'] = sha(OLD)
    state['resources']['test_intent'] = {'package_sha256': sha(encoded(file_hashes(
        {'market_intelligence.py': OLD, 'requirements.txt': REQ})))}
    before = {name: b'historical authoring\n' for name in work.legacy_inventory()}
    before.update({'market_intelligence.py': OLD, 'requirements.txt': REQ,
                   work.DESCRIPTOR: encoded(client._descriptor(state))})
    target = {**before, 'market_intelligence.py': NEW,
              work.DESCRIPTOR: encoded(client._descriptor({**state, 'pack_sha256': 'new-pack'}))}
    config = client._runtime_config(state)
    journal = {'schema_version': 1, 'operation_id': OP, 'action': 'update',
               'phase': phase, 'status': 'running', 'before_state': copy.deepcopy(state),
               'before_config_sha256': work.runtime_config_hash(config),
               'target_config_sha256': work.runtime_config_hash(config), 'target_runtime_config': config,
               'target_pack_sha256': 'new-pack', 'session_key': 'session',
               'before_identity': work.deployment_identity(client.row),
               'before_hashes': file_hashes(before), 'target_hashes': file_hashes(target)}
    root = work.agent_root(state)
    client.files = {root + '/' + name: raw for name, raw in before.items()}
    for side, files in (('before', before), ('target', target)):
        backup = work._backup_root(client, journal, side)
        client.files.update({backup + '/' + name: raw for name, raw in files.items()})
    if phase != 'backup':
        client.files[root + '/market_intelligence.py'] = NEW
    state['maintenance'] = journal
    client.saved = state
    return root, before


def test_historical_journal_rolls_back_at_its_original_roots_and_identities(client):
    root, before = historical_journal(client, 'target_files')
    assert run(client, action='rollback')['status'] == 'rolled_back'
    assert client.refreshes == [OLD] and client.probes == [sha(OLD)]
    assert {name: client.files[root + '/' + name] for name in before} == before
    assert 'layout_version' not in client.saved
    assert 'package_hashes' not in client.saved['resources']
    assert client.saved['operation_id'] == INSTALL
    assert client.saved['resources']['agent_key'] == 'agent'
    assert not any(path.startswith(AGENT_ROOT + '/core') for path in client.uploads)


def test_historical_backup_requires_original_installer_without_writes(client):
    historical_journal(client, 'backup')
    before = copy.deepcopy(client.files)
    with pytest.raises(AidpProvisionConflict, match='exact saved installer'):
        run(client)
    assert client.files == before and client.uploads == [] and client.refreshes == []


def test_legacy_layout_is_not_silently_migrated_by_update(client):
    historical_journal(client, 'backup')
    client.saved.pop('maintenance')
    before = copy.deepcopy(client.files)
    with pytest.raises(AidpProvisionConflict, match='verified native rebinding'):
        run(client)
    assert client.files == before and client.uploads == [] and client.refreshes == []


def assert_resource_identity_preserved(client):
    assert client.saved['operation_id'] == INSTALL
    for key in ('agent_key', 'agent_compute_key', 'shared_oci_credential_key', 'apify_credential_key', 'test_session_key'):
        assert client.saved['resources'][key] == client.original_resources[key]


def test_update_accepts_exact_revision_then_publishes_descriptor_and_receipt(client):
    result = run(client)
    assert result['status'] == 'completed' and result['deployment_surface'] == 'TEST' and result['research_enabled'] is False
    assert client.refreshes == [NEW] and client.probes == [identity(NEW)]
    assert client.saved['pack_sha256'] == 'new-pack'
    assert json.loads(client.files[AGENT_ROOT + '/agent-manifest.json'])['pack_sha256'] == 'new-pack'
    assert_resource_identity_preserved(client)
    uploads = len(client.uploads)
    assert run(client) == result
    assert len(client.uploads) == uploads and client.refreshes == [NEW]


def test_partial_file_write_resumes_from_backup_without_overwriting_drift(client):
    client.fail_path = AGENT_ROOT + '/core/runtime.py'
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=2)
    assert client.saved['maintenance']['phase'] == 'target_files'
    assert client.saved['pack_sha256'] == 'old-pack'
    assert json.loads(client.files[AGENT_ROOT + '/agent-manifest.json'])['pack_sha256'] == 'old-pack'
    assert run(client)['status'] == 'completed'
    assert client.refreshes == [NEW]
    assert_resource_identity_preserved(client)


def test_refresh_timeout_is_never_resubmitted_and_requires_observed_revision(client):
    client.lose_refresh = True
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=4)
    assert client.saved['maintenance']['phase'] == 'target_wait'
    assert client.refreshes == [NEW]
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=2)
    assert client.refreshes == [NEW]
    with pytest.raises(AidpProvisionPending, match='uncertain'):
        run(client, 'rollback', max_steps=2)
    client.publish_revision()
    assert run(client)['status'] == 'completed'
    assert client.refreshes == [NEW]


@pytest.mark.parametrize('fault', ['source', 'langgraph'])
def test_confirmed_probe_failure_restores_old_source_and_preserves_data(client, fault):
    client.files['/Workspace/customer/data.json'] = b'customer data remains'
    client.fail_new_probe = fault == 'source'
    if fault == 'langgraph':
        client.langgraph_versions[identity(NEW)] = '1.0.1'
    result = run(client)
    assert result['status'] == 'rolled_back'
    assert client.refreshes == [NEW, OLD] and client.probes == [identity(NEW), identity(OLD)]
    assert client.saved['pack_sha256'] == 'old-pack'
    for path, raw in client.original_files.items():
        assert client.files[path] == raw
    assert client.files['/Workspace/customer/data.json'] == b'customer data remains'
    assert_resource_identity_preserved(client)
    assert run(client)['status'] == 'rolled_back' and client.refreshes == [NEW, OLD]


def test_rollback_cannot_accept_an_unsupported_previous_runtime_or_repeat_refresh(client):
    assert run(client)['status'] == 'completed'
    client.langgraph_versions[identity(OLD)] = '0.6.7'
    for _ in range(2):
        with pytest.raises(AidpProvisionError, match='selected managed LangGraph profile'):
            run(client, 'rollback')
        assert client.saved['maintenance']['phase'] == 'rollback_wait'
        assert 'rollback_verified' not in client.saved['maintenance']
        assert client.refreshes == [NEW, OLD]
    assert_resource_identity_preserved(client)


@pytest.mark.parametrize('control_root', [work.CONTROL_ROOT, '/Workspace/.control/modules/ai_market_intelligence'])
def test_explicit_rollback_reuses_backup_and_its_single_refresh(client, control_root):
    client.control_root = control_root
    assert run(client)['status'] == 'completed'
    assert run(client, 'rollback')['status'] == 'rolled_back'
    assert client.refreshes == [NEW, OLD]
    assert run(client, 'rollback')['status'] == 'rolled_back'
    assert client.refreshes == [NEW, OLD]
    assert_resource_identity_preserved(client)
    backup_paths = [path for path in client.uploads if '/maintenance/' in path]
    assert backup_paths and all(path.startswith(control_root + '/maintenance/') for path in backup_paths)
    assert all(path.startswith(control_root + '/maintenance/') for path in client.reads if '/maintenance/' in path)


def test_selected_existing_session_survives_update_idempotent_repeat_and_rollback(client):
    selected = 'other-existing-session'
    result = run(client, test_session_id=selected)
    assert result['status'] == 'completed'
    assert client.saved['resources']['test_session_key'] == selected
    assert run(client, test_session_id=selected) == result
    assert client.refreshes == [NEW] and client.probe_sessions == [selected]
    assert run(client, 'rollback', test_session_id=selected)['status'] == 'rolled_back'
    assert client.saved['resources']['test_session_key'] == selected
    assert client.probe_sessions == [selected, selected]
    assert client.sessions == {'session', selected}
    assert run(client, 'rollback')['status'] == 'rolled_back'
    assert client.refreshes == [NEW, OLD]


def test_foreign_production_or_operation_is_rejected_without_mutation(client):
    client.production = [{'key': 'prod'}]
    with pytest.raises(AidpProvisionConflict, match='PROD'):
        run(client)
    assert not client.uploads and not client.refreshes
    client.production = []
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=1)
    writes = len(client.uploads)
    with pytest.raises(AidpProvisionConflict, match='Another maintenance'):
        run(client, operation=OTHER)
    assert len(client.uploads) == writes and not client.refreshes


def test_source_drift_during_partial_update_is_preserved(client):
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=1)
    client.files[AGENT_ROOT + '/market_intelligence.py'] = b'foreign edits'
    with pytest.raises(AidpProvisionConflict, match='drift'):
        run(client)
    assert client.files[AGENT_ROOT + '/market_intelligence.py'] == b'foreign edits'
    assert not client.refreshes


def test_revoked_administrator_cannot_resume_or_rollback(client):
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=1)
    client.revoked = True
    uploads = len(client.uploads)
    for action in ('update', 'rollback'):
        with pytest.raises(AidpProvisionConflict, match='revoked'):
            run(client, action)
    assert len(client.uploads) == uploads and not client.refreshes


def test_redeploy_accepts_only_current_package_and_source(client, monkeypatch):
    with pytest.raises(AidpProvisionConflict, match='same package'):
        run(client, 'redeploy')
    monkeypatch.setattr(work, 'agent_package', lambda config: (OLD, assets_for(OLD)))
    monkeypatch.setattr(work, 'load_package', lambda *a, **k: NS(pack_sha256='old-pack'))
    assert run(client, 'redeploy')['status'] == 'completed'
    assert client.refreshes == [OLD]
    assert_resource_identity_preserved(client)


def test_foreign_descriptor_on_resume_is_preserved_before_file_mutation(client):
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=1)
    descriptor_path = AGENT_ROOT + '/agent-manifest.json'
    client.files[descriptor_path] = encoded({**client._descriptor(client.saved), 'owner': 'another-owner'})
    writes = len(client.uploads)
    with pytest.raises(AidpProvisionConflict, match='ownership descriptor changed'):
        run(client)
    assert len(client.uploads) == writes and not client.refreshes
    assert json.loads(client.files[descriptor_path])['owner'] == 'another-owner'


def test_native_revision_changed_after_probe_is_not_committed(client):
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=4)
    assert client.saved['maintenance']['phase'] == 'commit'
    client.publish_revision()
    with pytest.raises(AidpProvisionConflict, match='before commit'):
        run(client)
    assert client.saved['pack_sha256'] == 'old-pack'
    assert json.loads(client.files[AGENT_ROOT + '/agent-manifest.json'])['pack_sha256'] == 'old-pack'
    assert client.refreshes == [NEW]


def test_stopped_compute_starts_exact_recorded_identity_then_resume_waits(client):
    client.compute_state = 'STOPPED'
    client.lose_start_response = True
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=2)
    assert client.compute_starts == ['market-maintenance-start:' + OP]
    assert not client.uploads and not client.refreshes
    assert client.saved['maintenance']['phase'] == 'backup'
    client.compute_state = 'ACTIVE'
    assert run(client)['status'] == 'completed'
    assert client.compute_starts == ['market-maintenance-start:' + OP]
    assert_resource_identity_preserved(client)


def test_repeated_stopped_observation_uses_same_recorded_retry_scope(client):
    client.compute_state = 'STOPPED'
    client.start_enters_transition = False
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=2)
    assert client.compute_starts == ['market-maintenance-start:' + OP] * 2
    assert not client.uploads and not client.refreshes


def test_transient_compute_waits_without_start_or_source_writes(client):
    client.compute_state = 'UPDATING'
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=2)
    assert not client.compute_starts and not client.uploads and not client.refreshes


@pytest.mark.parametrize('state', ['FAILED', 'DELETED', 'DELETING'])
def test_terminal_compute_is_not_replaced_or_started(client, state):
    client.compute_state = state
    with pytest.raises(AidpProvisionError, match='terminal'):
        run(client)
    assert not client.compute_starts and not client.uploads and not client.refreshes
    assert 'maintenance' not in client.saved


def test_completed_operation_does_not_restart_stopped_compute(client):
    result = run(client)
    client.compute_state = 'STOPPED'
    assert run(client) == result
    assert not client.compute_starts


@pytest.mark.parametrize('rolled_back', [False, True])
def test_completed_uuid_cannot_attest_a_different_bundled_candidate(client, monkeypatch, rolled_back):
    client.fail_new_probe = rolled_back
    result = run(client)
    assert result['target_pack_sha256'] == 'new-pack'
    assert run(client) == result
    uploads, refreshes = len(client.uploads), list(client.refreshes)
    monkeypatch.setattr(work, 'load_package', lambda *a, **k: NS(pack_sha256='another-bundle'))
    with pytest.raises(AidpProvisionConflict, match='another bundled candidate'):
        run(client)
    assert len(client.uploads) == uploads and client.refreshes == refreshes


def test_incomplete_probe_remains_reconcilable_without_refresh_or_rollback(client):
    original = client._probe_market
    def interrupted(*args):
        raise AidpProvisionError('TEST exceeded the verification window.')
    client._probe_market = interrupted
    with pytest.raises(AidpProvisionPending, match='pending'):
        run(client, max_steps=5)
    assert client.refreshes == [NEW] and client.saved['maintenance']['phase'] == 'target_wait'
    client._probe_market = original
    assert run(client)['status'] == 'completed' and client.refreshes == [NEW]


@pytest.mark.parametrize('field,value', [
    ('schema_version', True), ('schema_version', 3), ('operation_id', '../../outside'),
    ('action', 'delete'), ('action', []), ('status', 'invented'), ('phase', '../../outside'),
    ('session_key', '../foreign-session'),
])
def test_invalid_journal_metadata_is_rejected_before_artifact_access(client, field, value):
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=1)
    client.saved['maintenance'][field] = value
    client.reads.clear()
    writes = len(client.uploads)
    with pytest.raises(AidpProvisionConflict, match='journal is invalid'):
        run(client)
    assert not client.reads and len(client.uploads) == writes and not client.refreshes


@pytest.mark.parametrize('name', ['../outside.py', '/Workspace/foreign.py', 'a\\outside.py', 'C:/outside.py'])
def test_tampered_backup_paths_never_redirect_operator_reads_or_writes(client, name):
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=1)
    journal = client.saved['maintenance']
    for side in ('before_hashes', 'target_hashes'):
        journal[side][name] = journal[side].pop('requirements.txt')
    client.reads.clear()
    writes = len(client.uploads)
    with pytest.raises(AidpProvisionConflict, match='journal is invalid'):
        run(client)
    assert not client.reads and len(client.uploads) == writes and not client.refreshes


@pytest.mark.parametrize('side,digest', [('before_hashes', 'A' * 64), ('target_hashes', 'not-sha256')])
def test_tampered_hashes_are_rejected_before_backup_access(client, side, digest):
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=1)
    client.saved['maintenance'][side]['requirements.txt'] = digest
    client.reads.clear()
    writes = len(client.uploads)
    with pytest.raises(AidpProvisionConflict, match='journal is invalid'):
        run(client)
    assert not client.reads and len(client.uploads) == writes


@pytest.mark.parametrize('binding', ['owner', 'workspace', 'operation_id', 'agent_key', 'agent_compute_key',
                                     'shared_oci_credential_key', 'apify_credential_key'])
def test_backup_state_cannot_restore_foreign_installation_or_resource_keys(client, binding):
    with pytest.raises(AidpProvisionPending):
        run(client, max_steps=1)
    before = client.saved['maintenance']['before_state']
    if binding in before['resources']:
        before['resources'][binding] = 'foreign-resource'
    else:
        before[binding] = OTHER if binding == 'operation_id' else 'foreign-owner-or-workspace'
    client.reads.clear()
    writes = len(client.uploads)
    with pytest.raises(AidpProvisionConflict, match='journal is invalid'):
        run(client, 'rollback')
    assert not client.reads and len(client.uploads) == writes and not client.refreshes


def test_model_update_commits_frozen_config_and_rollback_restores_it(client,monkeypatch):
    original=copy.deepcopy(client.saved['runtime_config'])
    client.settings.agent_model_id='replacement-model'
    monkeypatch.setattr(work,'agent_package',lambda config:(encoded(config),assets_for(encoded(config))))
    run(client, change_model=True)
    selected=client.saved['runtime_config']
    assert selected['services']['model_id']=='replacement-model'
    assert client.files[AGENT_ROOT+'/market_intelligence.py']==encoded(selected)
    assert client.saved['maintenance']['target_runtime_config']==selected
    assert client.saved['maintenance']['target_config_sha256']==work.runtime_config_hash(selected)
    assert json.loads(client.files[AGENT_ROOT+'/agent-manifest.json'])['runtime_config_sha256']==work.runtime_config_hash(selected)
    client.settings.agent_model_id='another-live-setting'
    run(client,'rollback')
    assert client.saved['runtime_config']==original
    assert all(client.files[path]==raw for path,raw in client.original_files.items())
    assert_resource_identity_preserved(client)


def test_pending_model_update_resumes_saved_target_instead_of_live_settings(client,monkeypatch):
    client.settings.agent_model_id='requested-model'
    monkeypatch.setattr(work,'agent_package',lambda config:(encoded(config),assets_for(encoded(config))))
    with pytest.raises(AidpProvisionPending):
        run(client,max_steps=1, change_model=True)
    target=copy.deepcopy(client.saved['maintenance']['target_runtime_config'])
    client.settings.agent_model_id='unrelated-after-restart'
    run(client)
    assert client.saved['runtime_config']==target
    assert client.files[AGENT_ROOT+'/market_intelligence.py']==encoded(target)


def test_same_operation_cannot_attest_a_different_model(client):
    run(client)
    writes=len(client.uploads)
    client.settings.agent_model_id='different-model'
    with pytest.raises(AidpProvisionConflict,match='model configuration'):
        run(client, change_model=True)
    assert len(client.uploads)==writes


def test_redeploy_cannot_change_model_even_when_package_and_mocked_program_match(client,monkeypatch):
    monkeypatch.setattr(work,'agent_package',lambda config:(OLD,assets_for(OLD)))
    monkeypatch.setattr(work,'load_package',lambda *a,**k:NS(pack_sha256='old-pack'))
    client.settings.agent_model_id='another-model'
    with pytest.raises(AidpProvisionConflict,match='Redeploy requires'):
        run(client,'redeploy')
    assert not client.uploads and not client.refreshes


def test_invalid_frozen_target_config_does_not_reach_artifact_writes(client):
    with pytest.raises(AidpProvisionPending):
        run(client,max_steps=1)
    client.saved['maintenance']['target_runtime_config']['research_enabled']=True
    writes=len(client.uploads)
    with pytest.raises(AidpProvisionConflict,match='journal is invalid'):
        run(client)
    assert len(client.uploads)==writes


@pytest.mark.parametrize('side',['before','target'])
def test_changed_frozen_model_config_is_rejected_before_resume_or_rollback(client,side):
    with pytest.raises(AidpProvisionPending):
        run(client,max_steps=1)
    journal=client.saved['maintenance']
    config=journal['before_state']['runtime_config'] if side=='before' else journal['target_runtime_config']
    config['services']['model_id']='tampered-model'
    writes=len(client.uploads)
    with pytest.raises(AidpProvisionConflict,match='journal is invalid'):
        run(client,'rollback')
    assert len(client.uploads)==writes


def test_same_package_model_change_uses_update_and_can_restore_unbound_paused_receipt(client,monkeypatch):
    client.saved.pop('runtime_config')
    client.files[AGENT_ROOT+'/agent-manifest.json']=encoded(client._descriptor(client.saved))
    original_descriptor=client.files[AGENT_ROOT+'/agent-manifest.json']
    monkeypatch.setattr(work,'load_package',lambda *a,**k:NS(pack_sha256='old-pack'))
    monkeypatch.setattr(work,'agent_package',lambda config:(encoded(config),assets_for(encoded(config))))
    run(client, change_model=True)
    assert client.saved['runtime_config']['services']['model_id']=='selected-model'
    assert client.saved['pack_sha256']=='old-pack'
    run(client,'rollback')
    assert 'runtime_config' not in client.saved
    assert client.files[AGENT_ROOT+'/agent-manifest.json']==original_descriptor
    assert client.files[AGENT_ROOT+'/market_intelligence.py']==OLD


def test_code_update_keeps_installed_selection_when_operator_settings_are_stale(client):
    original = copy.deepcopy(client.saved['runtime_config'])
    client.settings.agent_model_id = 'stale-operator-model'
    run(client)
    assert client.saved['runtime_config'] == original
    writes = len(client.uploads)
    client.settings.agent_model_id = 'another-stale-model'
    run(client)
    assert len(client.uploads) == writes


@pytest.mark.parametrize('action', ['redeploy', 'rollback'])
def test_explicit_model_change_requires_update_without_writes(client, action):
    with pytest.raises(ValueError, match='requires update'):
        run(client, action, change_model=True)
    assert not client.uploads and not client.refreshes
