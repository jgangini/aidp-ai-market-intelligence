import copy
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from ai_market_intelligence import rebuild as work
from ai_market_intelligence.errors import AidpProvisionConflict, AidpProvisionPending
from ai_market_intelligence.installation.layout import AGENT_ROOT, LEGACY_AGENT_ROOT


OP = '954dcc9b-ac24-4378-9b7f-d36b5ee31d14'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    plugin = ModuleType('aidp_agent_flow.native_layout')
    plugin.prune_tree = Mock(return_value={'tree_absent': True})
    plugin.inventory = lambda *a: ({'old.py'}, set())
    monkeypatch.setitem(__import__('sys').modules, 'aidp_agent_flow.native_layout', plugin)
    monkeypatch.setattr(work, 'installation_lock', lambda *a: nullcontext())
    monkeypatch.setattr(work, 'verify_administrator', Mock())
    monkeypatch.setattr(work, '_backup_obsolete', Mock())
    resources = dict(agent_key='before', test_session_key='old-session', agent_compute_key='compute',
                     shared_oci_credential_key='oci', apify_credential_key='apify',
                     compute_creation_operation='create-compute', package_hashes={'file.py': 'a' * 64})
    saved = {'layout_version': 2, 'phase': 'ready', 'owner': 'owner', 'workspace': 'workspace',
             'agent_metadata_root': LEGACY_AGENT_ROOT, 'resources': resources,
             'maintenance': {'status': 'completed', 'historical': True},
             'migration': {'before_hashes': {'old.py': 'a' * 64}}}
    client = NS(settings=NS(aidp_workspace_key='workspace', admin_user_ocid='owner'), base='https://exact-target', saved=saved)
    client._read_installation = lambda _: copy.deepcopy(client.saved)
    client._save = lambda state: setattr(client, 'saved', copy.deepcopy(state))
    client._ensure_role_permission = Mock()

    class Native:
        def __init__(self, old):
            self.base, self.workspace, self.compute = client.base, 'workspace', 'compute'
            self.remote_root, self.metadata_root, self.name = AGENT_ROOT, LEGACY_AGENT_ROOT if old else AGENT_ROOT, 'ai_market_intelligence'
            self.agent_key = 'before' if old else None
            self.config = {'native': {'agent_key': 'before'} if old else {}}
            self.root = tmp_path / ('previous' if old else 'current')
            self.root.mkdir()
            self.state, self.intents, self.creates, self.retirements = {}, [], 0, 0
            self.status = Mock()
            self._verify_package = Mock()
        def _package(self):
            return {'package_sha256': 'frozen'}, None
        def _object(self, path):
            return b'old'
        def _intents(self):
            return self.intents
        def retire_agent(self, **kwargs):
            if kwargs.get('apply'):
                self.retirements += 1
            return {'binding_sha256': 'binding', 'agent_absent': kwargs.get('apply', False)}
        def ensure_agent(self, **kwargs):
            self.creates += 1
            self.agent_key = 'after'
            self.intents = [('intent', {'kind': 'agent', 'state': 'RECONCILED', 'resource_key': 'after'})]
            return {'agent_key': 'after'}
        def inspect_layout(self):
            return {'layout_matches': True}
        def test_init(self, **kwargs):
            self.intents.append(('test', {'kind': 'test-init'}))
            self.state['test_receipt'] = {'accepted': True}
        def _deployments(self, kind):
            return [{'lifecycleState': 'ACTIVE'}] if kind == 'TEST' and self.state.get('test_receipt') else []

    previous, current = Native(True), Native(False)
    def test_step(state, _):
        if 'test_session_key' not in state['resources']:
            state['resources']['test_session_key'] = 'new-session'
            client._save(state)
            raise AidpProvisionPending('New session saved; verifying', 'test')
        state['resources']['test_verified'] = {'probe': True}
    client._test_step = test_step
    return client, previous, current, plugin


def advance(setup, apply=True):
    client, previous, current, _ = setup
    return work.advance(client, previous, current, OP, apply=apply)


def test_read_only_plan_has_no_mutations(setup):
    client, previous, current, plugin = setup
    before = copy.deepcopy(client.saved)
    assert advance(setup, False)['will_replace_agent_and_session']
    assert client.saved == before and previous.retirements == current.creates == 0
    plugin.prune_tree.assert_not_called()


def test_complete_replacement_keeps_compute_credentials_and_removes_maintenance(setup):
    client, previous, current, plugin = setup
    before = copy.deepcopy(client.saved['resources'])
    pending = 0
    for _ in range(12):
        try:
            result = advance(setup)
            if result['status'] == 'completed':
                break
        except AidpProvisionPending as error:
            assert error.phase == 'test'
            pending += 1
    else:
        pytest.fail('Replacement did not reach its verified terminal state')
    assert pending == 1
    assert previous.retirements == current.creates == 1
    assert client.saved['resources']['agent_key'] == 'after'
    assert client.saved['resources']['test_session_key'] == 'new-session'
    assert all(client.saved['resources'][k] == before[k] for k in work.PRESERVED)
    assert client.saved['agent_metadata_root'] == AGENT_ROOT and client.saved['phase'] == 'ready'
    assert 'maintenance' not in client.saved
    assert b'"historical": true' in (current.root.parent / 'before-installer.json').read_bytes()
    plugin.prune_tree.assert_called_once()


@pytest.mark.parametrize('field,value', [('workspace', 'other'), ('compute', 'other'), ('base', 'https://other'),
                                         ('metadata_root', LEGACY_AGENT_ROOT), ('remote_root', '/Workspace/other')])
def test_changed_resume_target_stops_before_any_create(setup, field, value):
    client, previous, current, plugin = setup
    advance(setup)
    assert client.saved['rebuild']['phase'] == 'create'
    setattr(current, field, value)
    with pytest.raises(AidpProvisionConflict):
        advance(setup)
    assert current.creates == 0
    plugin.prune_tree.assert_not_called()


def test_foreign_new_agent_cannot_be_adopted_from_edited_config(setup):
    _, _, current, _ = setup
    advance(setup)
    current.config['native']['agent_key'] = 'foreign'
    current.agent_key = 'foreign'
    with pytest.raises(AidpProvisionConflict):
        advance(setup)
    assert current.creates == 0


def test_unfinished_maintenance_blocks_replacement(setup):
    client, previous, current, _ = setup
    client.saved['maintenance']['status'] = 'running'
    with pytest.raises(AidpProvisionConflict):
        advance(setup)
    assert previous.retirements == current.creates == 0


def test_replacement_handles_cascade_leaving_only_empty_folders(setup):
    client, _, _, plugin = setup
    plugin.inventory = lambda *a: (set(), set())
    for _ in range(3):
        advance(setup)
    assert client.saved['rebuild']['phase'] == 'permissions'
    assert client.saved['rebuild']['prune_hashes'] == {}
    assert plugin.prune_tree.call_args.args[2] == {}


def test_failed_or_creating_TEST_does_not_commit_layout(setup):
    client, _, current, _ = setup
    for _ in range(5):
        advance(setup)
    assert client.saved['rebuild']['phase'] == 'test_wait'
    current._deployments = lambda kind: [{'lifecycleState': 'CREATING'}] if kind == 'TEST' else []
    with pytest.raises(AidpProvisionPending):
        advance(setup)
    assert client.saved['resources']['agent_key'] == 'before'
    current._deployments = lambda kind: [{'lifecycleState': 'FAILED'}] if kind == 'TEST' else []
    with pytest.raises(AidpProvisionConflict):
        advance(setup)
    assert client.saved['phase'] == 'rebuild'
