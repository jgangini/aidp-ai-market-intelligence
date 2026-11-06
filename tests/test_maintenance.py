import copy
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

from ai_market_intelligence import maintenance as work
from ai_market_intelligence.client import AGENT_ROOT, ROOT, MarketAidpClient, market_probe_receipt, RUNTIME_DISTRIBUTIONS
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

    def refresh(self, workspace, agent, compute, source, *, assets, expected_workspace, agent_root, initialize, on_submit):
        assert (workspace, agent, compute, expected_workspace, agent_root, initialize) == (
            'workspace', 'agent', 'compute', 'workspace', AGENT_ROOT, False)
        assert assets == {'requirements.txt': REQ}
        assert self.client.files[AGENT_ROOT + '/market_intelligence.py'] == source
        digest = sha(encoded({'market_intelligence.py': sha(source), 'requirements.txt': sha(REQ)}))
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
        self.settings = NS(admin_user_ocid='owner', aidp_region='us-chicago-1')
        self.files = {}
        self.refreshes = []
        self.probes = []
        self.probe_sessions = []
        self.sessions = {'session', 'other-existing-session'}
        self.uploads = []
        self.reads = []
        self.lose_refresh = False
        self.fail_new_probe = False
        self.fail_path = None
        self.compute_state = 'ACTIVE'
        self.compute_starts = []
        self.start_enters_transition = True
        self.lose_start_response = False
        self.production = []
        self.revoked = False
        self.revision = 0
        self.row = self.make_row()
        hashes = {'market_intelligence.py': sha(OLD), 'requirements.txt': sha(REQ)}
        self.saved = {'installer_version': 2, 'module_id': 'ai_market_intelligence', 'publication_root': ROOT,
            'workspace': 'workspace', 'owner': 'owner', 'operation_id': INSTALL, 'pack_sha256': 'old-pack',
            'phase': 'ready', 'status': 'active', 'reuse_existing': True,
            'resources': {'agent_key': 'agent', 'agent_compute_key': 'compute', 'shared_oci_credential_key': 'shared',
                          'apify_credential_key': 'apify', 'compute_creation_operation': INSTALL, 'source_sha256': sha(OLD),
                          'test_session_key': 'session', 'test_intent': {'package_sha256': sha(encoded(hashes))},
                          'test_verified': {'deployment_identity': work.deployment_identity(self.row)}}}
        self.files = {AGENT_ROOT + '/market_intelligence.py': OLD, AGENT_ROOT + '/requirements.txt': REQ,
                      AGENT_ROOT + '/ai_market_intelligence/runtime.py': b'old authoring\n',
                      AGENT_ROOT + '/agent-manifest.json': encoded(self._descriptor(self.saved))}
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
        return {'schema_version': 1, 'research_enabled': False}

    def _workspace_bytes(self, workspace, path, *, phase):
        self.reads.append(path)
        return self.files.get(path)

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
        return {'key': 'agent', 'type': 'CODE', 'pathInfo': AGENT_ROOT, 'entryFilePath': AGENT_ROOT + '/market_intelligence.py',
                'dependenciesFilePath': AGENT_ROOT + '/requirements.txt', 'computeKey': 'compute'}

    def _agent_deployments(self, path):
        return self.production

    def _shared_oci_credential(self):
        return {'key': 'shared'}

    def _probe_market(self, endpoint, session, source_hash):
        assert endpoint == ENDPOINT and session in self.sessions
        self.probes.append(source_hash)
        self.probe_sessions.append(session)
        if self.fail_new_probe and source_hash == sha(NEW):
            raise AidpProvisionError('TEST did not verify this paused current source and authenticated session.')
        return market_probe_receipt({'source_sha256': source_hash, 'schema_version': 1, 'research_enabled': False,
            'business_tables_required': False, 'authenticated_session_bound': True, 'model_calls': 0, 'apify_starts': 0,
            'versions': {'python': '3.11.13', 'packages': {name: 'fixture-version' for name in RUNTIME_DISTRIBUTIONS}}}, source_hash)


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
    monkeypatch.setattr(work, 'agent_package', lambda config: (NEW, {'requirements.txt': REQ}))
    monkeypatch.setattr(work, 'authoring_files', lambda: {'ai_market_intelligence/runtime.py': b'new authoring\n'})
    monkeypatch.setattr(work, 'load_package', lambda *a, **k: NS(pack_sha256='new-pack'))
    return client


def run(client, action='update', operation=OP, **kw):
    return work.maintain(client, action, operation, sleep=lambda _: None, **kw)


def assert_resource_identity_preserved(client):
    assert client.saved['operation_id'] == INSTALL
    for key in ('agent_key', 'agent_compute_key', 'shared_oci_credential_key', 'apify_credential_key', 'test_session_key'):
        assert client.saved['resources'][key] == client.original_resources[key]


def test_update_accepts_exact_revision_then_publishes_descriptor_and_receipt(client):
    result = run(client)
    assert result['status'] == 'completed' and result['deployment_surface'] == 'TEST' and result['research_enabled'] is False
    assert client.refreshes == [NEW] and client.probes == [sha(NEW)]
    assert client.saved['pack_sha256'] == 'new-pack'
    assert json.loads(client.files[AGENT_ROOT + '/agent-manifest.json'])['pack_sha256'] == 'new-pack'
    assert_resource_identity_preserved(client)
    uploads = len(client.uploads)
    assert run(client) == result
    assert len(client.uploads) == uploads and client.refreshes == [NEW]


def test_partial_file_write_resumes_from_backup_without_overwriting_drift(client):
    client.fail_path = AGENT_ROOT + '/ai_market_intelligence/runtime.py'
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


def test_confirmed_probe_failure_restores_old_source_and_preserves_data(client):
    client.files['/Workspace/customer/data.json'] = b'customer data remains'
    client.fail_new_probe = True
    result = run(client)
    assert result['status'] == 'rolled_back'
    assert client.refreshes == [NEW, OLD] and client.probes == [sha(NEW), sha(OLD)]
    assert client.saved['pack_sha256'] == 'old-pack'
    for path, raw in client.original_files.items():
        assert client.files[path] == raw
    assert client.files['/Workspace/customer/data.json'] == b'customer data remains'
    assert_resource_identity_preserved(client)
    assert run(client)['status'] == 'rolled_back' and client.refreshes == [NEW, OLD]


def test_explicit_rollback_reuses_backup_and_its_single_refresh(client):
    assert run(client)['status'] == 'completed'
    assert run(client, 'rollback')['status'] == 'rolled_back'
    assert client.refreshes == [NEW, OLD]
    assert run(client, 'rollback')['status'] == 'rolled_back'
    assert client.refreshes == [NEW, OLD]
    assert_resource_identity_preserved(client)


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
    monkeypatch.setattr(work, 'agent_package', lambda config: (OLD, {'requirements.txt': REQ}))
    monkeypatch.setattr(work, 'authoring_files', lambda: {'ai_market_intelligence/runtime.py': b'old authoring\n'})
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
    ('schema_version', True), ('schema_version', 2), ('operation_id', '../../outside'),
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
