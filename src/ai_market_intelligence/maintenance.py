"""Durable TEST candidate replacement; resources and the installation UUID stay fixed."""
import copy
import hashlib
import json
import re
import time
from pathlib import PurePosixPath
from uuid import UUID
from oci._vendor.requests.exceptions import RequestException

from .authorization import verify_administrator
from .client import AGENT_ROOT, CONTROL_ROOT, MODULE_ID, bound_test_sessions, validate_test_endpoint, paused_runtime_config, runtime_config_hash
from .errors import AidpProvisionConflict, AidpProvisionError, AidpProvisionPending
from .installation.agent_package import agent_package, authoring_files
from .installation.compute import prepare_compute
from .installation.credentials import _project_credential, existing_project_reference
from .installation.playground import MarketPlaygroundClient, deployment_identity
from .operation import installation_lock
from .package import load_package


DESCRIPTOR = 'agent-manifest.json'
TERMINAL = {'FAILED', 'DELETED', 'DELETING', 'CANCELED', 'CANCELLED', 'DELETE_FAILED'}
PROBE_FAILURES = {
    'TEST did not verify this paused current source and authenticated session.',
    'TEST returned incomplete runtime observations.',
    'TEST is missing required runtime packages.',
}
RUNNING_PHASES = {'backup', 'target_files', 'target_refresh', 'target_wait', 'commit',
                  'rollback_files', 'rollback_refresh', 'rollback_wait', 'rollback_commit'}
RESOURCE_IDENTITIES = ('agent_key', 'agent_compute_key', 'shared_oci_credential_key', 'apify_credential_key',
                       'compute_creation_operation')


def _validate_journal(state):
    journal = state.get('maintenance')
    if journal is None:
        return
    def reject():
        raise AidpProvisionConflict('The maintenance journal is invalid; preserve it without reading or replacing artifacts.')
    if not isinstance(journal, dict) or type(journal.get('schema_version')) is not int or journal['schema_version'] != 1:
        reject()
    try:
        canonical = str(UUID(journal['operation_id']))
    except (KeyError, ValueError, TypeError, AttributeError):
        reject()
    if (canonical != journal['operation_id'] or not isinstance(journal.get('action'), str)
            or journal['action'] not in {'update', 'redeploy'} or not isinstance(journal.get('status'), str)
            or journal['status'] not in {'running', 'completed', 'rolled_back'} or not isinstance(journal.get('phase'), str)
            or journal['phase'] not in RUNNING_PHASES | {'ready'}
            or (journal['status'] == 'running') == (journal['phase'] == 'ready')):
        reject()
    try:
        config = paused_runtime_config(journal['target_runtime_config'])
        if runtime_config_hash(config) != journal.get('target_config_sha256'):
            reject()
    except (KeyError, ValueError, TypeError):
        reject()
    inventory = {'market_intelligence.py', 'requirements.txt', DESCRIPTOR, *authoring_files()}
    for side in ('before', 'target'):
        hashes = journal.get(side + '_hashes')
        if not isinstance(hashes, dict) or set(hashes) != inventory:
            reject()
        for name, digest in hashes.items():
            path = PurePosixPath(name)
            if (not isinstance(name, str) or path.is_absolute() or str(path) != name or '\\' in name
                    or '..' in path.parts or not re.fullmatch(r'[A-Za-z0-9_./-]+', name)
                    or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                reject()
    before = journal.get('before_state')
    if (not isinstance(before, dict) or before.get('phase') != 'ready'
            or any(key in before for key in ('maintenance', 'maintenance_history', 'history'))
            or any(before.get(key) != state.get(key) for key in
                   ('installer_version', 'module_id', 'publication_root', 'owner', 'workspace', 'operation_id'))
            or not isinstance(before.get('resources'), dict) or not isinstance(state.get('resources'), dict)):
        reject()
    try:
        if str(UUID(before['operation_id'])) != before['operation_id']:
            reject()
    except (KeyError, ValueError, TypeError, AttributeError):
        reject()
    try:
        before_config = paused_runtime_config(before.get('runtime_config', {'schema_version': 1, 'research_enabled': False}))
        current_config = paused_runtime_config(state.get('runtime_config', {'schema_version': 1, 'research_enabled': False}))
        if runtime_config_hash(before_config) != journal.get('before_config_sha256'):
            reject()
        expected = [before_config, config] if journal['status'] == 'running' else [
            config if journal['status'] == 'completed' else before_config]
        if current_config not in expected:
            reject()
    except (ValueError, TypeError):
        reject()
    for key in RESOURCE_IDENTITIES:
        if before['resources'].get(key) != state['resources'].get(key):
            reject()
    if not isinstance(journal.get('session_key'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', journal['session_key']):
        reject()
    if state['resources'].get('test_session_key') not in (before['resources'].get('test_session_key'), journal['session_key']):
        reject()


def _digest(body):
    return hashlib.sha256(body).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _read(client, workspace, path):
    body = client._workspace_bytes(workspace, path, phase='content')
    if body is not None and not isinstance(body, bytes):
        raise AidpProvisionConflict('Exact managed artifact bytes are unavailable; nothing was replaced.')
    return body


def _save(client, state):
    verify_administrator(client)
    _assert_descriptor(client, state)
    client._save(state)


def _assert_descriptor(client, state):
    raw = _read(client, state['workspace'], AGENT_ROOT + '/' + DESCRIPTOR)
    try:
        descriptor = json.loads(raw)
    except (ValueError, TypeError):
        raise AidpProvisionConflict('The managed ownership descriptor is missing or invalid.') from None
    journal = state.get('maintenance')
    if journal and journal['status'] == 'running':
        if _digest(raw) in {journal['before_hashes'][DESCRIPTOR], journal['target_hashes'][DESCRIPTOR]}:
            return
    elif descriptor == client._descriptor(state):
        return
    raise AidpProvisionConflict('The managed ownership descriptor changed; unrelated files were preserved.')


def _guarded_upload(client, state, path, content, *, allowed):
    verify_administrator(client)
    _assert_descriptor(client, state)
    workspace = state['workspace']
    actual = _read(client, workspace, path)
    if actual not in allowed:
        raise AidpProvisionConflict('Managed artifact drift was preserved; reconcile before resuming maintenance.')
    if actual != content:
        if path.startswith(AGENT_ROOT + '/') and client._agent_deployments(
                f"/workspaces/{workspace}/agents/{state['resources']['agent_key']}/deployments"):
            raise AidpProvisionConflict('An existing PROD deployment prevents edits to shared source.')
        client._upload_file(workspace, path, content, repair_drift=actual is not None)
    if _read(client, workspace, path) != content:
        raise AidpProvisionPending('The exact maintenance artifact is not visible yet.', 'maintenance')


def _target(client, state):
    source, assets = agent_package(client._runtime_config(state))
    files = {'market_intelligence.py': source, **assets, **authoring_files()}
    pack = load_package(MODULE_ID, require_available=False).pack_sha256
    descriptor = client._descriptor({**state, 'pack_sha256': pack})
    files[DESCRIPTOR] = _json(descriptor)
    return pack, files


def _bindings(client, state):
    workspace = str(client._workspace()['key'])
    if (state.get('workspace') != workspace or state.get('owner') != client.settings.admin_user_ocid
            or state.get('phase') != 'ready' or state.get('publication_root') != AGENT_ROOT.rsplit('/agents/', 1)[0]):
        raise AidpProvisionConflict('Maintenance requires this administrator and an exact ready installation receipt.')
    _assert_descriptor(client, state)
    resources = state['resources']
    agents = client._agents(workspace, MODULE_ID)
    if len(agents) != 1 or agents[0].get('key') != resources.get('agent_key'):
        raise AidpProvisionConflict('The managed agent identity changed; no resource was adopted.')
    agent = client._request('GET', f"/workspaces/{workspace}/agents/{resources['agent_key']}")
    expected = {'key': resources['agent_key'], 'type': 'CODE', 'pathInfo': AGENT_ROOT,
                'entryFilePath': AGENT_ROOT + '/market_intelligence.py',
                'dependenciesFilePath': AGENT_ROOT + '/requirements.txt', 'computeKey': resources['agent_compute_key']}
    if any(agent.get(key) != value for key, value in expected.items()):
        raise AidpProvisionConflict('Managed source, agent or compute binding changed.')
    if client._agent_deployments(f"/workspaces/{workspace}/agents/{resources['agent_key']}/deployments"):
        raise AidpProvisionConflict('An existing PROD deployment prevents edits to shared source.')
    shared = client._shared_oci_credential()
    if not shared or (shared.get('key') or shared.get('id')) != resources.get('shared_oci_credential_key'):
        raise AidpProvisionConflict('The recorded shared OCI credential identity changed.')
    try:
        existing_project_reference(_project_credential(client), resources.get('apify_credential_key', ''))
    except RuntimeError:
        raise AidpProvisionConflict('The recorded Apify credential identity is not available; no secret was changed.') from None
    compute = prepare_compute(client, workspace, resources.get('compute_creation_operation', state['operation_id']),
                              expected_key=resources['agent_compute_key'])
    return compute


def _ensure_compute(client, state, compute):
    if compute['state'] == 'ACTIVE':
        return
    if compute['state'] == 'STOPPED':
        journal = state['maintenance']
        key = state['resources']['agent_compute_key']
        intent = {'compute_key': key, 'retry_scope': 'market-maintenance-start:' + journal['operation_id']}
        if journal.get('compute_start_intent', intent) != intent:
            raise AidpProvisionConflict('Recorded compute start identity changed; no replacement was created.')
        if 'compute_start_intent' not in journal:
            journal['compute_start_intent'] = intent
            _save(client, state)
        verify_administrator(client)
        _assert_descriptor(client, state)
        client._request('POST', f"/workspaces/{state['workspace']}/clusters/{key}/actions/start",
                        payload={}, phase='compute', retry_scope=intent['retry_scope'])
    raise AidpProvisionPending('Waiting for the exact dedicated AI Compute to become ACTIVE.', 'maintenance')


def _row(native, state):
    rows = native.test_deployments(state['workspace'], state['resources']['agent_key'])
    if len(rows) != 1:
        raise AidpProvisionPending('The submitted TEST revision is not visible; no refresh was repeated.', 'maintenance')
    row = rows[0]
    if row.get('agentFlowComputeKey') != state['resources']['agent_compute_key']:
        raise AidpProvisionConflict('TEST compute differs from the installation; preserve its resources.')
    deployment_identity(row)
    return row


def _begin(client, native, state, action, operation, session):
    config = client._selected_runtime_config()
    pack, target = _target(client, {**state, 'runtime_config': config})
    before = {name: _read(client, state['workspace'], AGENT_ROOT + '/' + name) for name in target}
    if any(value is None for value in before.values()):
        raise AidpProvisionConflict('The managed artifact inventory is incomplete; update cannot add or adopt files.')
    try:
        descriptor = json.loads(before[DESCRIPTOR])
    except (ValueError, TypeError):
        raise AidpProvisionConflict('The current ownership descriptor is invalid.') from None
    if descriptor != client._descriptor(state) or _digest(before['market_intelligence.py']) != state['resources'].get('source_sha256'):
        raise AidpProvisionConflict('The installed descriptor/source no longer matches its receipt.')
    candidate_digest = _digest(_json({name: _digest(before[name]) for name in ('market_intelligence.py', 'requirements.txt')}))
    if candidate_digest != state['resources'].get('test_intent', {}).get('package_sha256'):
        raise AidpProvisionConflict('The installed source or dependencies changed after TEST acceptance.')
    if action == 'redeploy' and (pack != state['pack_sha256'] or before != target):
        raise AidpProvisionConflict('Redeploy requires the same package and exact current sources; use update for a new candidate.')
    row = _row(native, state)
    if row.get('lifecycleState') != 'ACTIVE' or deployment_identity(row) != state['resources'].get('test_verified', {}).get('deployment_identity'):
        raise AidpProvisionConflict('The installed TEST revision differs from its accepted receipt.')
    chosen = session or state['resources'].get('test_session_key')
    if not chosen:
        raise AidpProvisionConflict('Select an existing bound TEST session; maintenance never creates a session.')
    _session(native, state, row, chosen)
    snapshot = {key: copy.deepcopy(value) for key, value in state.items()
                if key not in {'maintenance', 'maintenance_history', 'history'}}
    journal = {'schema_version': 1, 'operation_id': operation, 'action': action, 'phase': 'backup', 'status': 'running',
               'before_state': snapshot, 'before_config_sha256': runtime_config_hash(client._runtime_config(state)),
               'target_pack_sha256': pack, 'session_key': chosen,
               'target_runtime_config': copy.deepcopy(config), 'target_config_sha256': runtime_config_hash(config),
               'before_identity': deployment_identity(row), 'before_hashes': {name: _digest(raw) for name, raw in before.items()},
               'target_hashes': {name: _digest(raw) for name, raw in target.items()}}
    previous = state.get('maintenance')
    if previous:
        state.setdefault('maintenance_history', []).append({key: previous.get(key) for key in
            ('operation_id', 'action', 'status', 'phase', 'target_pack_sha256')})
    state['maintenance'] = journal
    _validate_journal(state)
    _save(client, state)


def _backup_root(journal, side):
    return CONTROL_ROOT + '/maintenance/' + journal['operation_id'] + '/' + side


def _backups(client, state, side):
    journal = state['maintenance']
    result = {}
    for name, digest in journal[side + '_hashes'].items():
        raw = _read(client, state['workspace'], _backup_root(journal, side) + '/' + name)
        if raw is None or _digest(raw) != digest:
            raise AidpProvisionConflict('The immutable maintenance backup is missing or changed.')
        result[name] = raw
    return result


def _snapshot_files(client, state):
    journal = state['maintenance']
    pack, target = _target(client, {**journal['before_state'], 'runtime_config': journal['target_runtime_config']})
    if pack != journal['target_pack_sha256'] or {name: _digest(raw) for name, raw in target.items()} != journal['target_hashes']:
        raise AidpProvisionConflict('Resume with the exact saved maintenance package; its target changed.')
    for side in ('before', 'target'):
        parents = {str(parent) for name in journal[side + '_hashes']
                   for parent in PurePosixPath(_backup_root(journal, side) + '/' + name).parents
                   if str(parent).startswith(CONTROL_ROOT + '/maintenance')}
        for parent in sorted(parents, key=lambda path: (path.count('/'), path)):
            verify_administrator(client)
            _assert_descriptor(client, state)
            client._ensure_folder(state['workspace'], parent)
        for name in journal[side + '_hashes']:
            destination = _backup_root(journal, side) + '/' + name
            if side == 'before':
                raw = _read(client, state['workspace'], AGENT_ROOT + '/' + name)
                if raw is None or _digest(raw) != journal['before_hashes'][name]:
                    raise AidpProvisionConflict('The original source changed before backup; it was preserved.')
            else:
                raw = target[name]
            _guarded_upload(client, state, destination, raw, allowed=(None, raw))
    _backups(client, state, 'before')
    _backups(client, state, 'target')
    journal['phase'] = 'target_files'
    _save(client, state)


def _replace_files(client, state, side):
    before, target = _backups(client, state, 'before'), _backups(client, state, 'target')
    desired = before if side == 'before' else target
    for name, raw in desired.items():
        if name != DESCRIPTOR:
            _guarded_upload(client, state, AGENT_ROOT + '/' + name, raw, allowed=(before[name], target[name]))
    state['maintenance']['phase'] = 'rollback_refresh' if side == 'before' else 'target_refresh'
    _save(client, state)


def _session(native, state, row, session):
    detail = native._request('GET', f"/workspaces/{state['workspace']}/agentFlowSessions/{session}")
    if detail.get('key') != session or not bound_test_sessions([detail], state['resources']['agent_key'], state['resources']['agent_compute_key'], row['endpointUrl']):
        raise AidpProvisionConflict('Use an existing session bound to the exact TEST agent, compute and endpoint.')


def _refresh(client, native, state, rollback):
    journal = state['maintenance']
    side, prefix = ('before', 'rollback') if rollback else ('target', 'target')
    files = _backups(client, state, side)
    row = _row(native, state)
    prior = deployment_identity(row)
    if not rollback and prior != journal['before_identity']:
        raise AidpProvisionConflict('TEST changed before the recorded maintenance refresh; no revision was submitted.')
    def submitted(intent):
        journal[prefix + '_intent'] = {**intent, 'previous_identity': prior}
        journal['phase'] = 'rollback_wait' if rollback else 'target_wait'
        _save(client, state)
    verify_administrator(client)
    receipt = native.refresh(state['workspace'], state['resources']['agent_key'], state['resources']['agent_compute_key'],
        files['market_intelligence.py'], assets={'requirements.txt': files['requirements.txt']},
        expected_workspace=state['workspace'], agent_root=AGENT_ROOT, initialize=False, on_submit=submitted)
    journal[prefix + '_receipt'] = receipt
    _save(client, state)


def _poll(client, native, state, rollback):
    journal = state['maintenance']
    prefix, side = ('rollback', 'before') if rollback else ('target', 'target')
    row = _row(native, state)
    identity = deployment_identity(row)
    previous = journal[prefix + '_intent']['previous_identity']
    if identity == previous:
        raise AidpProvisionPending('Waiting for the submitted TEST identity; no refresh was repeated.', 'maintenance')
    if row.get('lifecycleState') in TERMINAL:
        if rollback:
            raise AidpProvisionConflict('Rollback TEST failed; preserve the backup and reconcile this exact submission.')
        journal.update(phase='rollback_files', failure='candidate_native_terminal')
        _save(client, state)
        return
    if row.get('lifecycleState') != 'ACTIVE':
        raise AidpProvisionPending('Waiting for the submitted TEST to become ACTIVE.', 'maintenance')
    validate_test_endpoint(row['endpointUrl'], client.settings.aidp_region)
    _session(native, state, row, journal['session_key'])
    files = _backups(client, state, side)
    for name, content in files.items():
        if name != DESCRIPTOR and _read(client, state['workspace'], AGENT_ROOT + '/' + name) != content:
            raise AidpProvisionConflict('Managed files changed after refresh; the revision was not accepted.')
    try:
        verify_administrator(client)
        probe = client._probe_market(row['endpointUrl'], journal['session_key'], _digest(files['market_intelligence.py']))
    except (RequestException, TimeoutError, ValueError):
        raise AidpProvisionPending('TEST probe delivery is uncertain; resume the exact candidate without another refresh.', 'maintenance') from None
    except AidpProvisionError as exc:
        if str(exc) == 'TEST exceeded the verification window.':
            raise AidpProvisionPending('TEST probe timed out; its candidate remains pending without another refresh.', 'maintenance') from None
        if rollback or str(exc) not in PROBE_FAILURES:
            raise
        journal.update(phase='rollback_files', failure='candidate_probe_rejected')
        _save(client, state)
        return
    if deployment_identity(_row(native, state)) != identity:
        raise AidpProvisionConflict('TEST changed during its probe; this revision was not accepted.')
    journal[prefix + '_verified'] = {**probe, 'deployment_identity': identity}
    journal['phase'] = 'rollback_commit' if rollback else 'commit'
    _save(client, state)


def _complete(client, native, state, rollback):
    journal = state['maintenance']
    before, target = _backups(client, state, 'before'), _backups(client, state, 'target')
    chosen = before if rollback else target
    prefix = 'rollback' if rollback else 'target'
    row = _row(native, state)
    if row.get('lifecycleState') != 'ACTIVE' or deployment_identity(row) != journal[prefix + '_verified']['deployment_identity']:
        raise AidpProvisionConflict('The probed TEST revision changed before commit; its receipt was not published.')
    validate_test_endpoint(row['endpointUrl'], client.settings.aidp_region)
    _guarded_upload(client, state, AGENT_ROOT + '/' + DESCRIPTOR, chosen[DESCRIPTOR],
                    allowed=(before[DESCRIPTOR], target[DESCRIPTOR]))
    resources = copy.deepcopy(journal['before_state']['resources'])
    resources.update(source_sha256=_digest(chosen['market_intelligence.py']),
                     test_intent=journal[prefix + '_intent'], test_verified=journal[prefix + '_verified'],
                     test_session_key=journal['session_key'])
    if prefix + '_receipt' in journal:
        resources['test_receipt'] = journal[prefix + '_receipt']
    state['resources'] = resources
    if rollback and 'runtime_config' not in journal['before_state']:
        state.pop('runtime_config', None)
    else:
        state['runtime_config'] = copy.deepcopy(journal['before_state']['runtime_config'] if rollback else journal['target_runtime_config'])
    state['pack_sha256'] = journal['before_state']['pack_sha256'] if rollback else journal['target_pack_sha256']
    state.update(status='active', phase='ready')
    journal.update(status='rolled_back' if rollback else 'completed', phase='ready')
    _save(client, state)


def _result(journal, action):
    return {'status': journal['status'], 'phase': journal['phase'], 'operation_id': journal['operation_id'],
            'action': action, 'target_pack_sha256': journal['target_pack_sha256'],
            'research_enabled': False, 'deployment_surface': 'TEST'}


def maintain(client, action, operation_id, *, test_session_id=None, max_steps=80, sleep=time.sleep):
    if action not in {'update', 'redeploy', 'rollback'} or str(UUID(operation_id)) != operation_id or not 1 <= max_steps <= 120:
        raise ValueError('Choose update/redeploy/rollback with a canonical maintenance UUID and bounded progress.')
    with installation_lock(client, operation_id):
        verify_administrator(client)
        state = client._read_installation(str(client._workspace()['key']))
        if not state:
            raise AidpProvisionConflict('Install this product before maintenance; no resources were created.')
        _validate_journal(state)
        _bindings(client, state)
        journal = state.get('maintenance')
        if journal and journal['operation_id'] == operation_id:
            if action != 'rollback' and journal['status'] in {'completed', 'rolled_back'}:
                bundled = load_package(MODULE_ID, require_available=False).pack_sha256
                if runtime_config_hash(client._selected_runtime_config()) != journal['target_config_sha256']:
                    raise AidpProvisionConflict('This maintenance operation belongs to another model configuration; use a new operation for changed configuration.')
                if bundled != journal['target_pack_sha256']:
                    raise AidpProvisionConflict('This maintenance operation belongs to another bundled candidate; use a new operation for a changed package.')
            if action == 'rollback' and journal['status'] != 'rolled_back':
                if journal['phase'] == 'backup':
                    raise AidpProvisionConflict('Finish the immutable backup before selecting rollback.')
                if journal['phase'] == 'target_wait':
                    observer = MarketPlaygroundClient(client.settings)
                    try:
                        candidate = _row(observer, state)
                    finally:
                        observer.session.close()
                    if (deployment_identity(candidate) == journal['target_intent']['previous_identity']
                            or candidate.get('lifecycleState') not in TERMINAL | {'ACTIVE'}):
                        raise AidpProvisionPending('Reconcile the uncertain candidate refresh before rollback.', 'maintenance')
                if not journal['phase'].startswith('rollback'):
                    journal.update(status='running', phase='rollback_files')
                    _save(client, state)
            elif action != journal['action'] and action != 'rollback':
                raise AidpProvisionConflict('Resume the saved maintenance action and operation.')
            if journal['status'] in {'completed', 'rolled_back'}:
                return _result(journal, action)
            if test_session_id and test_session_id != journal['session_key']:
                raise AidpProvisionConflict('Resume with the recorded TEST session; no session was reset.')
        else:
            if journal and journal['status'] == 'running':
                raise AidpProvisionConflict('Another maintenance operation is active; resume its exact identity.')
            if action == 'rollback':
                raise AidpProvisionConflict('Rollback requires the recorded maintenance operation and immutable backup.')
            observer = MarketPlaygroundClient(client.settings)
            try:
                _begin(client, observer, state, action, operation_id, test_session_id)
            finally:
                observer.session.close()
        native = MarketPlaygroundClient(client.settings)
        try:
            for _ in range(max_steps):
                verify_administrator(client)
                compute = _bindings(client, state)
                journal = state['maintenance']
                try:
                    _ensure_compute(client, state, compute)
                    phase = journal['phase']
                    if phase == 'backup':
                        _snapshot_files(client, state)
                    elif phase in {'target_files', 'rollback_files'}:
                        _replace_files(client, state, 'before' if phase == 'rollback_files' else 'target')
                    elif phase in {'target_refresh', 'rollback_refresh'}:
                        _refresh(client, native, state, phase == 'rollback_refresh')
                    elif phase in {'target_wait', 'rollback_wait'}:
                        _poll(client, native, state, phase == 'rollback_wait')
                    elif phase in {'commit', 'rollback_commit'}:
                        _complete(client, native, state, phase == 'rollback_commit')
                    if journal['status'] in {'completed', 'rolled_back'}:
                        return _result(journal, action)
                except (AidpProvisionPending, TimeoutError):
                    # Intents survive uncertain responses; subsequent iterations only poll or reconcile files.
                    pass
                sleep(5)
        finally:
            native.session.close()
    raise AidpProvisionPending('Maintenance remains pending. Resume its exact operation without resubmitting a refresh.', 'maintenance')
