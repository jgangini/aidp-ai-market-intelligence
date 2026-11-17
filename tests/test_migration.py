"""Installer handoff uses the plugin without recreating agent resources."""
import copy
from contextlib import contextmanager
from pathlib import Path
from pathlib import PurePosixPath
from types import SimpleNamespace as NS

import pytest

from ai_market_intelligence import migration as work
from ai_market_intelligence.client import MarketAidpClient, CONTROL_ROOT, LEGACY_CONTROL_ROOT, ROOT
from ai_market_intelligence.installation.layout import AGENT_ROOT, LEGACY_AGENT_ROOT
from ai_market_intelligence.runtime.package_identity import file_hashes
from ai_market_intelligence.errors import AidpProvisionConflict, AidpProvisionPending
from test_maintenance import FakeClient, INSTALL, OP, OLD, NEW, candidate, encoded, sha


@pytest.fixture
def setup(tmp_path, monkeypatch):
    client = FakeClient()
    client.installation_root = ROOT
    client.control_root = LEGACY_CONTROL_ROOT
    client.saved['layout_version'] = 1
    client.saved['resources']['source_sha256'] = sha(OLD)
    client.saved['resources'].pop('package_hashes')
    client.files = {LEGACY_AGENT_ROOT + '/' + k: v for k, v in candidate(OLD).items()}
    client.files[LEGACY_AGENT_ROOT + '/agent-manifest.json'] = encoded(client._descriptor(client.saved))
    client.folders = {str(parent) for path in client.files for parent in PurePosixPath(path).parents}
    client.folders.update({LEGACY_CONTROL_ROOT, '/Workspace/.control/modules', '/Workspace/.control'})
    original_save = client._save
    def save(state):
        original_save(state)
        client.files[client.control_root + '/installer.json'] = encoded(state)
    client._save = save
    save(client.saved)
    client._workspace_json = lambda workspace, path, message: (
        __import__('json').loads(client.files[path]) if path in client.files else None)
    client._read_installation = lambda workspace: MarketAidpClient._read_installation(client, workspace)
    def upload(workspace, path, raw, *, repair_drift=True):
        client.files[path] = raw
    client._upload_file = upload
    client._workspace_object_type = lambda body, headers: headers.get('object-type') or (body.get('type', '') if isinstance(body, dict) else '')
    client._workspace_object_exists = lambda body, headers: body is not None
    client._workspace_object = lambda workspace, path, phase: (
        (client.files[path], {'object-type': 'FILE'}) if path in client.files else
        (({'type': 'FOLDER'}, {}) if path in client.folders or any(k.startswith(path + '/') for k in client.files) else (None, {})))
    client._ensure_folder = lambda workspace, path: client.folders.add(path)
    def listing(path, *, params):
        root = params['path']
        folders = client.folders | {str(parent) for path in client.files for parent in PurePosixPath(path).parents}
        objects = {**{k: 'FOLDER' for k in folders}, **{k: 'FILE' for k in client.files}}
        return [{'path': k, 'type': v} for k, v in sorted(objects.items()) if str(PurePosixPath(k).parent) == root]
    client._list = listing
    original_request = client._request
    def request(method, path, **kwargs):
        if path.endswith('/actions/moveObject'):
            src = kwargs['payload']['fromPath']
            destination_uri = kwargs['payload']['toPath']
            assert destination_uri.endswith('/'), 'The verified service requires an explicit folder URI'
            parent = destination_uri.rstrip('/')
            assert parent in client.folders, 'The service requires an existing destination folder'
            dest = parent + '/' + PurePosixPath(src).name
            assert client.saved['migration']['phase'] == 'move_wait'
            for name in list(client.files):
                if name == src or name.startswith(src + '/'):
                    client.files[dest + name[len(src):]] = client.files.pop(name)
            for folder in list(client.folders):
                if folder == src or folder.startswith(src + '/'):
                    client.folders.remove(folder)
                    client.folders.add(dest + folder[len(src):])
            return {}
        return original_request(method, path, **kwargs)
    client._request = request
    locks = []
    @contextmanager
    def lock(c, operation):
        locks.append(operation)
        yield
    monkeypatch.setattr(work, 'installation_lock', lock)
    monkeypatch.setattr(work, 'verify_administrator', lambda c: None)
    monkeypatch.setattr(work, 'legacy_inventory', lambda: set(candidate(OLD)) | {'agent-manifest.json'})
    def target(c, state):
        files = candidate(NEW)
        files['agent-manifest.json'] = encoded(c._descriptor({**state, 'pack_sha256': 'new-pack'}))
        return 'new-pack', files
    monkeypatch.setattr(work, '_target', target)
    files = target(client, {**client.saved, 'layout_version': 2})[1]
    local = tmp_path / 'candidate'
    for name, raw in files.items():
        path = local / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    class Lifecycle:
        remote_root = AGENT_ROOT
        metadata_root = LEGACY_AGENT_ROOT
        agent_key, compute, workspace, ws = 'agent', 'compute', 'workspace', '/workspaces/workspace'
        state = {}
        rebinds, refreshes = 0, 0
        actual = {'key': 'agent', 'type': 'CODE', 'computeKey': 'compute', 'pathInfo': LEGACY_AGENT_ROOT,
                  'entryFilePath': LEGACY_AGENT_ROOT + '/market_intelligence.py',
                  'dependenciesFilePath': LEGACY_AGENT_ROOT + '/requirements.txt'}
        def _package(self): return {'files': file_hashes(files)}, local
        def _source_agent(self): return copy.deepcopy(self.actual), {}
        def _deployments(self, kind): return client.production if kind == 'PROD' else [copy.deepcopy(client.row)]
        def _snapshot(self, rows): return copy.deepcopy(rows)
        def _request(self, method, path, **kwargs):
            return {'key': 'session', 'agentFlowKey': 'agent', 'agentFlowComputeKey': 'compute',
                    'endpointUrl': client.row['endpointUrl'], 'deploymentType': 'TEST', 'lifecycleState': 'ACTIVE'}, {}
        def sync_package(self, **kwargs):
            for name, raw in files.items():
                client.files[AGENT_ROOT + '/' + name] = raw
        def rebind(self, apply=False, **kwargs):
            if apply and self.actual['entryFilePath'].startswith(LEGACY_AGENT_ROOT + '/'):
                self.rebinds += 1
                self.actual.update(entryFilePath=AGENT_ROOT + '/market_intelligence.py',
                                   dependenciesFilePath=AGENT_ROOT + '/requirements.txt')
            return {'binding_sha256': 'hash'}
        def test_refresh(self, **kwargs):
            if not self.state.get('test_receipt'):
                self.refreshes += 1
                client.publish_revision()
                self.state['test_receipt'] = {'package_sha256': 'current'}
            return {'submitted': True}
        def status(self): return {}
    return NS(client=client, native=Lifecycle(), locks=locks, files=files)


def test_read_only_plan_acquires_no_cloud_lock(setup):
    before = copy.deepcopy(setup.client.files)
    assert not work.migrate(setup.client, setup.native, OP)['applied']
    assert setup.locks == [] and setup.client.files == before


def test_layout_enforcing_plugin_blocks_incompatible_migration_before_any_mutation(setup):
    setup.native.inspect_layout = lambda: {'layout_matches': False}
    before = copy.deepcopy(setup.client.files)
    with pytest.raises(AidpProvisionConflict, match='historical rebinding-only migration'):
        work.migrate(setup.client, setup.native, OP, apply=True)
    assert setup.locks == [] and setup.client.files == before
    assert setup.native.rebinds == setup.native.refreshes == 0


def test_migration_moves_whole_control_tree_and_preserves_identities(setup):
    setup.client.files[LEGACY_CONTROL_ROOT + '/historic/receipt.json'] = b'historic'
    identities = copy.deepcopy(setup.client.saved['resources'])
    result = work.migrate(setup.client, setup.native, OP, apply=True, max_steps=20, sleep=lambda _: None)
    assert result['status'] == 'completed' and not result['native_test_accepted']
    assert setup.client.control_root == CONTROL_ROOT
    assert setup.client.saved['layout_version'] == 2
    assert setup.client.saved['agent_metadata_root'] == LEGACY_AGENT_ROOT
    assert setup.client.saved['migration']['control_move']['pending'] is None
    assert setup.client.files[CONTROL_ROOT + '/historic/receipt.json'] == b'historic'
    assert not any(k.startswith(LEGACY_CONTROL_ROOT + '/') for k in setup.client.files)
    assert setup.client.files[LEGACY_AGENT_ROOT + '/market_intelligence.py'] == OLD
    for key in ['agent_key', 'agent_compute_key', 'shared_oci_credential_key', 'apify_credential_key', 'test_session_key']:
        assert setup.client.saved['resources'][key] == identities[key]
    assert setup.native.rebinds == setup.native.refreshes == 1


def test_test_drift_after_probe_blocks_receipt_commit(setup):
    with pytest.raises(AidpProvisionPending):
        work.migrate(setup.client, setup.native, OP, apply=True, max_steps=5, sleep=lambda _: None)
    assert setup.client.saved['migration']['phase'] == 'commit'
    setup.client.publish_revision()
    with pytest.raises(AidpProvisionConflict, match='changed after its probe'):
        work.migrate(setup.client, setup.native, OP, apply=True)
    assert setup.client.saved['layout_version'] == 1


@pytest.mark.parametrize('change', ['root', 'inventory'])
def test_changed_journal_cannot_redirect_backups(setup, change):
    with pytest.raises(AidpProvisionPending):
        work.migrate(setup.client, setup.native, OP, apply=True, max_steps=1)
    journal = setup.client.saved['migration']
    if change == 'root': journal['control_before'] = '/Workspace/other'
    else: journal['before_hashes']['../other'] = 'a' * 64
    setup.client._save(setup.client.saved)
    before = copy.deepcopy(setup.client.files)
    with pytest.raises(AidpProvisionConflict, match='roots or inventory'):
        work.migrate(setup.client, setup.native, OP, apply=True)
    assert setup.client.files == before


def test_resume_after_lost_rebind_reply_never_repeats_update(setup, monkeypatch):
    original = setup.native.rebind
    lose = True
    def rebind(**kwargs):
        nonlocal lose
        result = original(**kwargs)
        if kwargs.get('apply') and lose:
            lose = False
            raise TimeoutError('reply lost')
        return result
    monkeypatch.setattr(setup.native, 'rebind', rebind)
    with pytest.raises(TimeoutError):
        work.migrate(setup.client, setup.native, OP, apply=True, max_steps=20, sleep=lambda _: None)
    result = work.migrate(setup.client, setup.native, OP, apply=True, max_steps=20, sleep=lambda _: None)
    assert result['status'] == 'completed' and setup.native.rebinds == 1


def test_lost_move_reply_resumes_from_new_control_root(setup, monkeypatch):
    original = setup.client._request
    moves = []
    def request(method, path, **kwargs):
        result = original(method, path, **kwargs)
        if path.endswith('/actions/moveObject') and kwargs['payload']['fromPath'].endswith('/installer.json'):
            moves.append(path)
            raise TimeoutError('move applied; reply lost')
        return result
    monkeypatch.setattr(setup.client, '_request', request)
    with pytest.raises(TimeoutError):
        work.migrate(setup.client, setup.native, OP, apply=True, max_steps=20, sleep=lambda _: None)
    assert CONTROL_ROOT + '/installer.json' in setup.client.files
    result = work.migrate(setup.client, setup.native, OP, apply=True, max_steps=20, sleep=lambda _: None)
    assert result['status'] == 'completed' and len(moves) == 1


def test_terminal_test_failure_is_reported_without_refresh_retry(setup):
    with pytest.raises(AidpProvisionPending):
        work.migrate(setup.client, setup.native, OP, apply=True, max_steps=4, sleep=lambda _: None)
    setup.client.row['lifecycleState'] = 'FAILED'
    with pytest.raises(AidpProvisionConflict, match='TEST failed'):
        work.migrate(setup.client, setup.native, OP, apply=True)
    assert setup.native.refreshes == 1


def test_wrong_owner_cannot_resume_or_plan(setup):
    setup.client.saved['owner'] = 'other'
    setup.client._save(setup.client.saved)
    with pytest.raises(AidpProvisionConflict, match='owner or workspace'):
        work.migrate(setup.client, setup.native, OP)
    assert setup.locks == []


def test_explicit_restore_verifies_original_source_before_restoring_receipt(setup, monkeypatch, tmp_path):
    work.migrate(setup.client, setup.native, OP, apply=True, max_steps=20, sleep=lambda _: None)
    original = copy.deepcopy(setup.client.saved['migration']['before_state'])
    saved_hashes = setup.client.saved['migration']['before_hashes']
    before_files = {k: setup.client.files[LEGACY_AGENT_ROOT + '/' + k] for k in saved_hashes}
    previous = copy.copy(setup.native)
    previous.remote_root = LEGACY_AGENT_ROOT
    previous.state = {}
    previous._package = lambda: ({'files': file_hashes(before_files)}, tmp_path)
    previous._verify_package = lambda package: all(setup.client.files[LEGACY_AGENT_ROOT + '/' + k] == v
                                                  for k, v in before_files.items())
    def rebind(**kwargs):
        if kwargs.get('apply'):
            previous.actual.update(entryFilePath=LEGACY_AGENT_ROOT + '/market_intelligence.py',
                                   dependenciesFilePath=LEGACY_AGENT_ROOT + '/requirements.txt')
        return {'binding_sha256': 'restore-hash'}
    previous.rebind = rebind
    setup.native._reconcile = lambda: None
    setup.native._guard = lambda: None
    result = work.restore(setup.client, previous, setup.native, OP, max_steps=5, sleep=lambda _: None)
    assert result['status'] == 'restored'
    assert setup.client.saved['layout_version'] == original['layout_version']
    assert setup.client.saved['resources']['agent_key'] == original['resources']['agent_key']
    assert setup.client.saved['resources']['source_sha256'] == sha(OLD)
    assert setup.client.saved['resources']['test_verified']['deployment_identity'] != original['resources']['test_verified']['deployment_identity']


def test_finished_legacy_maintenance_is_archived_in_before_snapshot(setup):
    old = {'schema_version': 1, 'status': 'completed', 'phase': 'ready', 'recovery': 'original'}
    setup.client.saved['maintenance'] = copy.deepcopy(old)
    setup.client._save(setup.client.saved)
    work.migrate(setup.client, setup.native, OP, apply=True, max_steps=20, sleep=lambda _: None)
    assert 'maintenance' not in setup.client.saved
    assert setup.client.saved['migration']['before_state']['maintenance'] == old


def test_restore_does_not_write_while_control_move_is_uncertain(setup):
    with pytest.raises(AidpProvisionPending):
        work.migrate(setup.client, setup.native, OP, apply=True, max_steps=6, sleep=lambda _: None)
    setup.client.saved['migration']['phase'] = 'move_wait'
    setup.client._save(setup.client.saved)
    before = copy.deepcopy(setup.client.files)
    with pytest.raises(AidpProvisionPending, match='control move'):
        work.restore(setup.client, setup.native, setup.native, OP)
    assert setup.client.files == before and setup.native.rebinds == 1


def test_restore_preparation_uses_backup_even_when_backend_changed(setup, monkeypatch, tmp_path):
    import sys
    work.migrate(setup.client, setup.native, OP, apply=True, max_steps=20, sleep=lambda _: None)
    monkeypatch.setattr(work, '_target', lambda *args: pytest.fail('Restore must not regenerate the current backend'))
    module = NS(NativeLifecycle=lambda folder, fields, transport: NS(root=folder, config=fields))
    monkeypatch.setitem(sys.modules, 'aidp_agent_flow.native_lifecycle', module)
    setup.client.session, setup.client.signer = NS(), object()
    setup.client.base = 'https://datalake.us-chicago-1.oci.oraclecloud.com/20260430/aiDataPlatforms/platform'
    setup.client.settings.oci_config_file = str(tmp_path / 'config')
    setup.client.settings.aidp_platform_id = 'platform'
    result = work.prepare(tmp_path, setup.client, OP, tmp_path, restoring=True)
    assert result.config['native']['remote_root'] == LEGACY_AGENT_ROOT
    assert (result.root / 'candidate/market_intelligence.py').read_bytes() == OLD


def test_bounded_polling_finishes_without_resubmitting_refresh(setup, monkeypatch):
    polls = []
    def refresh(**kwargs):
        setup.native.refreshes += 1
        setup.client.row['lifecycleState'] = 'CREATING'
        return {'submitted': True}
    def status():
        polls.append(True)
        if len(polls) == 2:
            setup.client.publish_revision()
            setup.native.state['test_receipt'] = {'package_sha256': 'verified'}
        return {}
    monkeypatch.setattr(setup.native, 'test_refresh', refresh)
    monkeypatch.setattr(setup.native, 'status', status)
    result = work.migrate(setup.client, setup.native, OP, apply=True, max_steps=20, sleep=lambda _: None)
    assert result['status'] == 'completed' and setup.native.refreshes == 1 and len(polls) == 2


def test_lost_child_move_reply_reconciles_before_receipt_handoff(setup, monkeypatch):
    original = setup.client._request
    moved = []
    def request(method, path, **kwargs):
        result = original(method, path, **kwargs)
        if path.endswith('/actions/moveObject'):
            name = PurePosixPath(kwargs['payload']['fromPath']).name
            moved.append(name)
            if name == 'migration':
                raise TimeoutError('child moved; reply lost')
        return result
    monkeypatch.setattr(setup.client, '_request', request)
    with pytest.raises(TimeoutError):
        work.migrate(setup.client, setup.native, OP, apply=True, max_steps=20, sleep=lambda _: None)
    assert LEGACY_CONTROL_ROOT + '/installer.json' in setup.client.files
    result = work.migrate(setup.client, setup.native, OP, apply=True, max_steps=20, sleep=lambda _: None)
    assert result['status'] == 'completed'
    assert moved == ['migration', 'installer.json']


def test_definitely_rejected_legacy_move_adopts_existing_parent_protocol(setup):
    with pytest.raises(AidpProvisionPending):
        work.migrate(setup.client, setup.native, OP, apply=True, max_steps=6, sleep=lambda _: None)
    journal = setup.client.saved['migration']
    journal.update(phase='move_wait', move_rejection={'status_code': 400})
    setup.client._save(setup.client.saved)
    result = work.migrate(setup.client, setup.native, OP, apply=True, max_steps=20, sleep=lambda _: None)
    assert result['status'] == 'completed'
    assert setup.client.saved['migration']['control_move']['completed'] == ['migration']
