"""Resume a CODE-input migration under the existing installation lock.

The plugin owns code staging, rebinding and TEST submission. This coordinator
owns the installer's receipt and moves its whole control folder only after probe.
"""
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import tomllib
import re
from contextlib import nullcontext
import time
from uuid import UUID

from .authorization import verify_administrator
from .client import CONTROL_ROOT, LEGACY_CONTROL_ROOT, MODULE_ID
from .client import bound_test_sessions, validate_test_endpoint
from .errors import AidpProvisionConflict, AidpProvisionPending
from .installation.layout import AGENT_ROOT, LEGACY_AGENT_ROOT, metadata_root
from .installation.playground import deployment_identity
from .maintenance import DESCRIPTOR, _target, legacy_inventory
from .operation import installation_lock
from .runtime.package_identity import file_hashes, source_identity, inventory_digest


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _bytes(client, state, path):
    raw = client._workspace_bytes(state['workspace'], path, phase='migration')
    if raw is not None and not isinstance(raw, bytes):
        raise AidpProvisionConflict('Migration requires exact workspace bytes.')
    return raw


def _put(client, state, path, raw):
    current = _bytes(client, state, path)
    if current not in (None, raw):
        raise AidpProvisionConflict('Migration destination changed; preserve its bytes.')
    if current is None:
        for parent in reversed(PurePosixPath(path).parents):
            if str(parent).startswith('/Workspace/'):
                client._ensure_folder(state['workspace'], str(parent))
        client._upload_file(state['workspace'], path, raw, repair_drift=False)
    if _bytes(client, state, path) != raw:
        raise AidpProvisionPending('Migration backup is not yet visible.', 'migration')


def _save(client, state):
    verify_administrator(client)
    client._save(state)


def _validate(state, operation):
    journal = state.get('migration')
    if (not isinstance(journal, dict) or journal.get('schema_version') != 1
            or journal.get('operation_id') != operation
            or journal.get('phase') not in {'backup', 'stage', 'rebind', 'refresh', 'test_wait',
                                            'commit', 'move_control', 'move_wait', 'ready'}
            or journal.get('status') not in {'running', 'completed'}):
        raise AidpProvisionConflict('Resume the exact recorded migration operation.')
    before = journal.get('before_state', {})
    hashes = journal.get('before_hashes')
    if (journal.get('control_before') not in (CONTROL_ROOT, LEGACY_CONTROL_ROOT)
            or not isinstance(hashes, dict) or set(hashes) != legacy_inventory()
            or any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
                   for value in hashes.values())):
        raise AidpProvisionConflict('Migration recovery roots or inventory are invalid.')
    if (_sha(_json(before)) != journal.get('before_sha256')
            or any(state.get(k) != before.get(k) for k in
                   ('owner', 'workspace', 'operation_id', 'module_id', 'publication_root'))
            or any(state['resources'].get(k) != before['resources'].get(k) for k in
                   ('agent_key', 'agent_compute_key', 'shared_oci_credential_key', 'apify_credential_key'))):
        raise AidpProvisionConflict('Migration identities or its immutable snapshot changed.')
    return journal


def _verified_test(client, lifecycle, state, *, require_probe=False):
    journal = state['migration']
    rows = lifecycle._deployments('TEST')
    if len(rows) != 1 or rows[0].get('lifecycleState') != 'ACTIVE':
        raise AidpProvisionConflict('The migrated TEST is not active; reconcile or restore its saved binding.')
    row = rows[0]
    if require_probe and deployment_identity(row) != journal['deployment_identity']:
        raise AidpProvisionConflict('TEST changed after its probe; the installer receipt was not committed.')
    validate_test_endpoint(row['endpointUrl'], client.settings.aidp_region)
    session, _ = lifecycle._request('GET', lifecycle.ws + '/agentFlowSessions/' + journal['session_key'], legacy=True)
    if not bound_test_sessions([session], state['resources']['agent_key'],
                               state['resources']['agent_compute_key'], row['endpointUrl']):
        raise AidpProvisionConflict('The existing TEST session binding changed.')
    return row


def _begin(client, lifecycle, state, operation, target, pack):
    if (state.get('phase') != 'ready' or state.get('layout_version', 1) != 1
            or state.get('owner') != client.settings.admin_user_ocid
            or (state.get('maintenance') or {}).get('status') == 'running'):
        raise AidpProvisionConflict('Migration requires this administrator and an idle legacy installation.')
    actual, _ = lifecycle._source_agent()
    expected = {'key': state['resources']['agent_key'], 'computeKey': state['resources']['agent_compute_key'],
                'pathInfo': LEGACY_AGENT_ROOT, 'entryFilePath': LEGACY_AGENT_ROOT + '/market_intelligence.py',
                'dependenciesFilePath': LEGACY_AGENT_ROOT + '/requirements.txt', 'type': 'CODE'}
    if any(actual.get(k) != v for k, v in expected.items()):
        raise AidpProvisionConflict('Legacy binding differs from the installation receipt.')
    raw_descriptor = _bytes(client, state, LEGACY_AGENT_ROOT + '/' + DESCRIPTOR)
    if raw_descriptor is None or json.loads(raw_descriptor) != client._descriptor(state):
        raise AidpProvisionConflict('The legacy ownership descriptor differs from its receipt.')
    original = {}
    for name in sorted(legacy_inventory()):
        raw = _bytes(client, state, LEGACY_AGENT_ROOT + '/' + name)
        if raw is None:
            raise AidpProvisionConflict('The legacy recovery inventory is incomplete.')
        original[name] = _sha(raw)
    entry_hash = original['market_intelligence.py']
    if entry_hash != state['resources'].get('source_sha256'):
        raise AidpProvisionConflict('The legacy entry no longer matches its accepted receipt.')
    tests = lifecycle._deployments('TEST')
    if len(tests) != 1 or tests[0].get('lifecycleState') != 'ACTIVE':
        raise AidpProvisionConflict('Migration requires one active existing TEST.')
    session = state['resources'].get('test_session_key')
    if not session:
        raise AidpProvisionConflict('Migration preserves an existing TEST session; none was recorded.')
    before = copy.deepcopy(state)
    state['migration'] = {'schema_version': 1, 'operation_id': operation, 'status': 'running',
        'phase': 'backup', 'before_state': before, 'before_sha256': _sha(_json(before)),
        'before_hashes': original, 'target_hashes': file_hashes(target), 'target_pack_sha256': pack,
        'before_test': lifecycle._snapshot(tests), 'session_key': session,
        'control_before': client.control_root}
    # Older installers reject this phase rather than overwriting a migrating receipt.
    state.update(phase='migration', status='updating')
    _save(client, state)


def _backup(client, state, target):
    journal = state['migration']
    root = journal['control_before'] + '/migration/' + journal['operation_id']
    _put(client, state, root + '/before-installer.json', _json(journal['before_state']))
    for name, digest in journal['before_hashes'].items():
        raw = _bytes(client, state, LEGACY_AGENT_ROOT + '/' + name)
        if raw is None or _sha(raw) != digest:
            raise AidpProvisionConflict('Legacy source changed before recovery backup.')
        _put(client, state, root + '/before/' + name, raw)
    for name, raw in target.items():
        _put(client, state, root + '/target/' + name, raw)
    journal['phase'] = 'stage'
    _save(client, state)


def _verify_target(client, state, target):
    for name, raw in target.items():
        if _bytes(client, state, AGENT_ROOT + '/' + name) != raw:
            raise AidpProvisionConflict('The migrated code differs from the saved candidate.')


def _move_control(client, state):
    journal = state['migration']
    # Retain a versioned idle lock so older hooks cannot recreate the retired receipt.
    client.retain_installation_guard = True
    if journal['control_before'] == CONTROL_ROOT:
        journal.update(phase='ready', status='completed')
        state.update(phase='ready', status='active')
        _save(client, state)
        return
    if journal['control_before'] != LEGACY_CONTROL_ROOT:
        raise AidpProvisionConflict('Unknown control root; no workspace object was moved.')
    from .installation.control_move import advance
    if not advance(client, state, LEGACY_CONTROL_ROOT, CONTROL_ROOT, read=_bytes, save=_save):
        return
    journal.update(phase='ready', status='completed')
    state.update(phase='ready', status='active')
    _save(client, state)


def prepare(root, client, operation, plugin_scripts, *, restoring=False):
    """Build a private plugin project without changing the evaluation campaign."""
    import sys
    sys.path.insert(0, str(Path(plugin_scripts).resolve()))
    from aidp_agent_flow.native_lifecycle import NativeLifecycle
    workspace = str(client._workspace()['key'])
    state = client._read_installation(workspace)
    if not state:
        raise AidpProvisionConflict('No existing installation was selected.')
    before = (state.get('migration') or {}).get('before_state', state)
    target_state = {**before, 'layout_version': 2, 'agent_metadata_root': metadata_root(before)}
    files = {} if restoring else _target(client, target_state)[1]
    folder = Path(root).resolve() / '.aidp-local/market/migrations' / operation
    if restoring:
        journal = _validate(state, operation)
        if journal['phase'] in {'move_control', 'move_wait'} and client.control_root != CONTROL_ROOT:
            raise AidpProvisionPending('Complete the recorded control handoff before restoring.', 'migration')
        files = {}
        for name, digest in journal['before_hashes'].items():
            raw = _bytes(client, state, client.control_root + '/migration/' + operation + '/before/' + name)
            if raw is None or _sha(raw) != digest:
                raise AidpProvisionConflict('Complete the immutable migration backup before restoring.')
            files[name] = raw
        folder = folder / 'restore'
    for name, raw in files.items():
        path = folder / 'candidate' / name
        if path.exists() and path.read_bytes() != raw:
            raise AidpProvisionConflict('The saved migration candidate changed; preserve it.')
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(raw)
    fields = {'agent': {'factory': 'market_intake:build_graph', 'python_path': '.',
                       'entry': 'candidate/market_intelligence.py', 'dependencies': 'candidate/requirements.txt'},
              'oci': {'config_file': str(Path(client.settings.oci_config_file).resolve()), 'profile': 'DEFAULT'},
              'native': {'endpoint': client.base.split('/20260430/')[0],
                         'platform_id': client.settings.aidp_platform_id, 'workspace_key': workspace,
                         'remote_root': LEGACY_AGENT_ROOT if restoring else AGENT_ROOT,
                         'metadata_root': metadata_root(before),
                         'display_name': MODULE_ID, 'agent_key': state['resources']['agent_key'],
                         'compute_key': state['resources']['agent_compute_key']}}
    text = '\n'.join('[' + section + ']\n' + '\n'.join(k + ' = ' + json.dumps(v)
                       for k, v in values.items()) + '\n' for section, values in fields.items())
    path = folder / 'aidp-agent.toml'
    if path.exists() and tomllib.loads(path.read_text(encoding='utf-8')) != fields:
        raise AidpProvisionConflict('The migration project configuration changed.')
    if not path.exists():
        path.write_text(text, encoding='utf-8', newline='\n')
    client.session.auth = client.signer
    return NativeLifecycle(folder, fields, transport=client.session)


def saved_forward(root, client, operation, plugin_scripts):
    """Reconcile the fixed forward project without regenerating its candidate."""
    import sys
    sys.path.insert(0, str(Path(plugin_scripts).resolve()))
    from aidp_agent_flow.native_lifecycle import NativeLifecycle
    from aidp_agent_flow.project import load_project
    folder = Path(root).resolve() / '.aidp-local/market/migrations' / operation
    _, config = load_project(folder)
    client.session.auth = client.signer
    return NativeLifecycle(folder, config, transport=client.session)


def restore(client, lifecycle, forward, operation, *, max_steps=1, sleep=time.sleep):
    """Explicitly restore the immutable legacy package; never retry an uncertain write."""
    if str(UUID(operation)) != operation or not 1 <= max_steps <= 120:
        raise ValueError('A canonical operation and bounded progress are required.')
    with installation_lock(client, operation):
        verify_administrator(client)
        state = client._read_installation(lifecycle.workspace)
        journal = _validate(state, operation)
        if state['owner'] != client.settings.admin_user_ocid or state['workspace'] != lifecycle.workspace:
            raise AidpProvisionConflict('Restore requires the original owner and workspace.')
        if journal['phase'] == 'move_wait' and client.control_root != CONTROL_ROOT:
            raise AidpProvisionPending('Reconcile the submitted control move before restoring; no binding changed.', 'migration')
        forward_package, _ = forward._package()
        if forward_package['files'] != journal['target_hashes']:
            raise AidpProvisionConflict('Preserve the fixed forward package before reconciling its writes.')
        forward._reconcile()
        forward._guard()
        if lifecycle.remote_root != LEGACY_AGENT_ROOT or lifecycle.agent_key != state['resources']['agent_key']:
            raise AidpProvisionConflict('Restore must select the exact legacy binding and agent.')
        package, _ = lifecycle._package()
        if package['files'] != journal['before_hashes']:
            raise AidpProvisionConflict('Restore package differs from the immutable backup.')
        for _ in range(max_steps):
            verify_administrator(client)
            if lifecycle._deployments('PROD'):
                raise AidpProvisionConflict('Production prevents TEST restoration.')
            phase = journal.get('restore_phase', 'rebind')
            if phase == 'rebind':
                plan = lifecycle.rebind()
                lifecycle.rebind(apply=True, expected_binding=plan['binding_sha256'], allow_no_etag=True)
                journal['restore_phase'] = 'refresh'
                state.update(phase='migration', status='updating')
                _save(client, state)
            elif phase == 'refresh':
                lifecycle.test_refresh(apply=True, recover_failed=True)
                journal['restore_phase'] = 'wait'
                _save(client, state)
            elif phase == 'wait':
                lifecycle.status()
                rows = lifecycle._deployments('TEST')
                if any(r.get('lifecycleState') in {'FAILED', 'CANCELED', 'CANCELLED'} for r in rows):
                    raise AidpProvisionConflict('Restore TEST failed; preserve its exact operation.')
                if len(rows) != 1 or rows[0].get('lifecycleState') != 'ACTIVE' or not lifecycle.state.get('test_receipt'):
                    if max_steps > 1:
                        sleep(2)
                        continue
                    raise AidpProvisionPending('Waiting for the recorded restore TEST.', 'migration')
                _verified_test(client, lifecycle, state)
                lifecycle._verify_package(package)
                probe = client._probe_market(rows[0]['endpointUrl'], journal['session_key'],
                                             journal['before_state']['resources']['source_sha256'])
                if lifecycle._snapshot(lifecycle._deployments('TEST')) != lifecycle._snapshot(rows):
                    raise AidpProvisionConflict('Restored TEST changed during its probe.')
                restored = copy.deepcopy(journal['before_state'])
                restored['resources']['test_verified'] = {**probe, 'deployment_identity': deployment_identity(rows[0])}
                restored['migration_recovery'] = {'operation_id': operation, 'status': 'restored',
                                                   'recovery_control_root': client.control_root}
                _save(client, restored)
                return {'action': 'restore-migration', 'status': 'restored', 'research_enabled': False,
                        'native_test_accepted': False, 'code_root': LEGACY_AGENT_ROOT}
            if max_steps > 1:
                sleep(2)
    raise AidpProvisionPending('Restore remains pending; resume the same operation.', 'migration')


def migrate(client, lifecycle, operation, *, apply=False, max_steps=1, sleep=time.sleep):
    """Advance the same migration; never create an agent, session or compute."""
    if str(UUID(operation)) != operation or not 1 <= max_steps <= 120:
        raise ValueError('A canonical operation and bounded progress are required.')
    if (apply and callable(getattr(lifecycle, 'inspect_layout', None))
            and lifecycle.metadata_root != lifecycle.remote_root):
        raise AidpProvisionConflict('This plugin requires metadata and source roots to match before TEST. The historical rebinding-only migration cannot complete that contract; preserve its recovery context and use the reviewed clean replacement route.')
    with installation_lock(client, operation) if apply else nullcontext():
        verify_administrator(client)
        state = client._read_installation(str(client._workspace()['key']))
        if not state:
            raise AidpProvisionConflict('No installation exists to migrate.')
        if state.get('owner') != client.settings.admin_user_ocid or state.get('workspace') != lifecycle.workspace:
            raise AidpProvisionConflict('Migration owner or workspace differs from the selected installation.')
        before = (state.get('migration') or {}).get('before_state', state)
        target_state = {**before, 'layout_version': 2, 'agent_metadata_root': metadata_root(before)}
        pack, target = _target(client, target_state)
        package, local_folder = lifecycle._package()
        if (lifecycle.remote_root != AGENT_ROOT or lifecycle.metadata_root != metadata_root(before)
                or lifecycle.agent_key != state['resources']['agent_key']
                or lifecycle.compute != state['resources']['agent_compute_key']
                or package['files'] != file_hashes(target)
                or any((local_folder / name).read_bytes() != raw for name, raw in target.items())):
            raise AidpProvisionConflict('Prepare the exact installer candidate and existing identity before migration.')
        if lifecycle._deployments('PROD'):
            raise AidpProvisionConflict('Production shares this agent; TEST migration cannot change it.')
        if not apply:
            return {'action': 'migrate', 'applied': False, 'candidate_revision': pack,
                    'code_root': AGENT_ROOT, 'control_root': CONTROL_ROOT,
                    'metadata_moved': False, 'native_test_accepted': False}
        if 'migration' not in state:
            _begin(client, lifecycle, state, operation, target, pack)
        journal = _validate(state, operation)
        if journal.get('restore_phase'):
            raise AidpProvisionConflict('Continue the recorded restore instead of advancing migration.')
        if journal['target_pack_sha256'] != pack or journal['target_hashes'] != file_hashes(target):
            raise AidpProvisionConflict('Resume with the exact saved migration candidate.')
        for _ in range(max_steps):
            verify_administrator(client)
            if lifecycle._deployments('PROD'):
                raise AidpProvisionConflict('Production appeared during migration; reconcile before continuing.')
            phase = journal['phase']
            if phase == 'backup':
                _backup(client, state, target)
            elif phase == 'stage':
                lifecycle.sync_package(apply=True, for_rebind=True)
                journal['phase'] = 'rebind'
                _save(client, state)
            elif phase == 'rebind':
                plan = lifecycle.rebind()
                lifecycle.rebind(apply=True, expected_binding=plan['binding_sha256'], allow_no_etag=True)
                journal['phase'] = 'refresh'
                _save(client, state)
            elif phase == 'refresh':
                # Plugin intents survive a reply lost before this phase is saved.
                result = lifecycle.test_refresh(apply=True)
                journal.update(phase='test_wait', refresh_submitted=bool(result.get('submitted')))
                _save(client, state)
            elif phase == 'test_wait':
                lifecycle.status()
                tests = lifecycle._deployments('TEST')
                if any(row.get('lifecycleState') in {'FAILED', 'CANCELED', 'CANCELLED', 'DELETED', 'DELETE_FAILED'}
                       for row in tests):
                    raise AidpProvisionConflict('Migration TEST failed; preserve the recovery backup and restore the recorded binding before retrying.')
                active = [row for row in tests if row.get('lifecycleState') == 'ACTIVE']
                if (len(active) != 1 or lifecycle._snapshot(tests) == journal['before_test']
                        or not lifecycle.state.get('test_receipt')):
                    if max_steps > 1:
                        sleep(2)
                        continue
                    raise AidpProvisionPending('Waiting for the recorded TEST candidate; refresh was not repeated.', 'migration')
                _verify_target(client, state, target)
                _verified_test(client, lifecycle, state)
                source = source_identity({k: v for k, v in target.items() if k != DESCRIPTOR})
                probe = client._probe_market(active[0]['endpointUrl'], journal['session_key'], source)
                if lifecycle._snapshot(lifecycle._deployments('TEST')) != lifecycle._snapshot(tests):
                    raise AidpProvisionConflict('TEST changed during verification.')
                journal.update(phase='commit', probe=probe, deployment_identity=deployment_identity(active[0]))
                _save(client, state)
            elif phase == 'commit':
                _verified_test(client, lifecycle, state, require_probe=True)
                _verify_target(client, state, target)
                # The full historical journal remains in the immutable before snapshot.
                state.pop('maintenance', None)
                state.update(layout_version=2, agent_metadata_root=metadata_root(before), pack_sha256=pack)
                executable = {k: v for k, v in target.items() if k != DESCRIPTOR}
                state['resources'].update(source_sha256=source_identity(executable), package_hashes=file_hashes(executable),
                    test_intent={'package_sha256': inventory_digest(file_hashes(executable))},
                    test_verified={**journal['probe'], 'deployment_identity': journal['deployment_identity']})
                journal['phase'] = 'move_control'
                _save(client, state)
            elif phase in {'move_control', 'move_wait'}:
                _verified_test(client, lifecycle, state, require_probe=True)
                try:
                    _move_control(client, state)
                except AidpProvisionPending:
                    if max_steps == 1:
                        raise
            if journal['status'] == 'completed':
                return {'action': 'migrate', 'applied': True, 'status': 'completed', 'candidate_revision': pack,
                        'code_root': AGENT_ROOT, 'control_root': client.control_root,
                        'metadata_moved': False, 'runtime_probe_verified': True,
                        'native_test_accepted': False, 'research_enabled': False}
            if max_steps > 1:
                sleep(2)
    raise AidpProvisionPending('Migration remains pending; resume its exact operation.', 'migration')
