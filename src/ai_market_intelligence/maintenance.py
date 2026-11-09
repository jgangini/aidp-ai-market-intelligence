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
from .client import AGENT_ROOT, CONTROL_ROOT, MODULE_ID, UNSUPPORTED_LANGGRAPH_ERROR, bound_test_sessions, validate_test_endpoint, validated_runtime_config, runtime_config_hash
from .errors import AidpProvisionConflict, AidpProvisionError, AidpProvisionPending
from .installation.agent_package import agent_package
from .installation.layout import agent_root, metadata_root, ROOT
from .runtime.package_identity import MANIFEST, file_hashes, inventory_digest, source_identity, validate_inventory
from .installation.compute import prepare_compute
from .installation.credentials import _project_credential, existing_project_reference, ensure_llm_credential, llm_credential_reference, validate_llm_secret
from .runtime.config import OPENAI_MODE
from .installation.playground import MarketPlaygroundClient, deployment_identity
from .operation import installation_lock
from .package import load_package
from .research_activation import target_configuration


DESCRIPTOR = 'agent-manifest.json'
TERMINAL = {'FAILED', 'DELETED', 'DELETING', 'CANCELED', 'CANCELLED', 'DELETE_FAILED'}
PROBE_FAILURES = {
    UNSUPPORTED_LANGGRAPH_ERROR,
    'TEST did not verify this paused current source and authenticated session.',
    'TEST did not verify this active current source and authenticated session.',
    'TEST active runtime differs from its configured exact dependency profile.',
    'TEST returned incomplete runtime observations.',
    'TEST is missing required runtime packages.',
}
RUNNING_PHASES = {'backup', 'target_files', 'target_refresh', 'target_wait', 'commit',
                  'rollback_files', 'rollback_refresh', 'rollback_wait', 'rollback_commit'}
RESOURCE_IDENTITIES = ('agent_key', 'agent_compute_key', 'shared_oci_credential_key', 'apify_credential_key',
                       'compute_creation_operation')


def legacy_inventory():
    modules = ('__init__ runtime_core media actor_profiles contracts finance serp social social_search '
               'connectors model memory mcp_payload provider actor_tools research_contracts effects '
               'passages documents collector state artifacts financial workflow runtime_versions '
               'config services session wire native factory model_bridge').split()
    return {'market_intelligence.py', 'requirements.txt', DESCRIPTOR,
            *('ai_market_intelligence/' + name + '.py' for name in modules),
            'ai_market_intelligence/prompts.json', 'ai_market_intelligence/mcp_tools.json'}


def _invalid_journal():
    raise AidpProvisionConflict('The maintenance journal is invalid; preserve it without reading or replacing artifacts.')


def _journal_identity(state, journal):
    if not isinstance(journal, dict) or type(journal.get('schema_version')) is not int or journal['schema_version'] not in (1, 2):
        _invalid_journal()
    if state.get('layout_version', 1) != journal['schema_version']:
        _invalid_journal()
    try:
        canonical = str(UUID(journal['operation_id']))
    except (KeyError, ValueError, TypeError, AttributeError):
        _invalid_journal()
    if (canonical != journal['operation_id'] or not isinstance(journal.get('action'), str)
            or journal['action'] not in {'update', 'redeploy'} or not isinstance(journal.get('status'), str)
            or journal['status'] not in {'running', 'completed', 'rolled_back'} or not isinstance(journal.get('phase'), str)
            or journal['phase'] not in RUNNING_PHASES | {'ready'}
            or (journal['status'] == 'running') == (journal['phase'] == 'ready')):
        _invalid_journal()


def _journal_target_config(journal):
    try:
        config = validated_runtime_config(journal['target_runtime_config'])
        if runtime_config_hash(config) != journal.get('target_config_sha256'):
            _invalid_journal()
    except (KeyError, ValueError, TypeError):
        _invalid_journal()
    return config


def _journal_file(name, digest):
    if not isinstance(name, str):
        _invalid_journal()
    path = PurePosixPath(name)
    if (path.is_absolute() or str(path) != name or '\\' in name
            or '..' in path.parts or not re.fullmatch(r'[A-Za-z0-9_./-]+', name)
            or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
        _invalid_journal()


def _journal_inventory(journal):
    inventory = None if journal['schema_version'] == 2 else legacy_inventory()
    for side in ('before', 'target'):
        hashes = journal.get(side + '_hashes')
        if not isinstance(hashes, dict) or (inventory is not None and set(hashes) != inventory):
            _invalid_journal()
        if journal['schema_version'] == 2:
            try:
                validate_inventory({name: digest for name, digest in hashes.items() if name != DESCRIPTOR})
            except ValueError:
                _invalid_journal()
            if DESCRIPTOR not in hashes or MANIFEST not in hashes:
                _invalid_journal()
        for name, digest in hashes.items():
            _journal_file(name, digest)
    if set(journal['before_hashes']) != set(journal['target_hashes']):
        _invalid_journal()


def _journal_previous_state(state, journal):
    before = journal.get('before_state')
    if (not isinstance(before, dict) or before.get('phase') != 'ready'
            or any(key in before for key in ('maintenance', 'maintenance_history', 'history'))
            or any(before.get(key) != state.get(key) for key in
                   ('installer_version', 'layout_version', 'module_id', 'publication_root', 'owner', 'workspace', 'operation_id'))
            or not isinstance(before.get('resources'), dict) or not isinstance(state.get('resources'), dict)):
        _invalid_journal()
    try:
        if str(UUID(before['operation_id'])) != before['operation_id']:
            _invalid_journal()
    except (KeyError, ValueError, TypeError, AttributeError):
        _invalid_journal()
    for key in RESOURCE_IDENTITIES:
        if before['resources'].get(key) != state['resources'].get(key):
            _invalid_journal()
    return before


def _journal_config_transition(state, journal, before, config):
    try:
        before_config = validated_runtime_config(before.get('runtime_config', {'schema_version': 1, 'research_enabled': False}))
        current_config = validated_runtime_config(state.get('runtime_config', {'schema_version': 1, 'research_enabled': False}))
        if runtime_config_hash(before_config) != journal.get('before_config_sha256'):
            _invalid_journal()
        expected = [before_config, config] if journal['status'] == 'running' else [
            config if journal['status'] == 'completed' else before_config]
        if current_config not in expected:
            _invalid_journal()
    except (ValueError, TypeError):
        _invalid_journal()


def _journal_production_binding(state, journal):
    if 'prod_snapshot' in journal:
        rows = journal['prod_snapshot']
        if (not isinstance(rows, list) or len(rows) > 1 or any(not isinstance(row, dict)
                or row.get('lifecycleState') != 'ACTIVE' or row.get('deploymentType') != 'PROD'
                or row.get('agentKey') != state['resources']['agent_key']
                or row.get('agentComputeKey') != state['resources']['agent_compute_key']
                or not isinstance(row.get('key'), str) or not row['key'] for row in rows)):
            _invalid_journal()


def _journal_test_driver(journal):
    if 'test_driver' in journal:
        driver = journal['test_driver']
        if (not isinstance(driver, dict) or set(driver) != {'kind', 'project', 'scripts', 'scripts_sha256', 'project_sha256'}
                or driver['kind'] != 'plugin_cli' or any(not isinstance(driver[field], str)
                or not driver[field] for field in ('project', 'scripts'))
                or not isinstance(driver['scripts_sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', driver['scripts_sha256'])
                or not isinstance(driver['project_sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', driver['project_sha256'])):
            _invalid_journal()


def _journal_session(state, journal, before):
    if not isinstance(journal.get('session_key'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', journal['session_key']):
        _invalid_journal()
    if state['resources'].get('test_session_key') not in (before['resources'].get('test_session_key'), journal['session_key']):
        _invalid_journal()


def _validate_journal(state):
    journal = state.get('maintenance')
    if journal is None:
        return
    _journal_identity(state, journal)
    config = _journal_target_config(journal)
    _journal_inventory(journal)
    before = _journal_previous_state(state, journal)
    _journal_config_transition(state, journal, before, config)
    _journal_production_binding(state, journal)
    _journal_test_driver(journal)
    _journal_session(state, journal, before)


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
    raw = _read(client, state['workspace'], agent_root(state) + '/' + DESCRIPTOR)
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
        if path.startswith(agent_root(state) + '/'):
            _check_production(client, state)
        client._upload_file(workspace, path, content, repair_drift=actual is not None)
        if path.startswith(agent_root(state) + '/'):
            _check_production(client, state)
    if _read(client, workspace, path) != content:
        raise AidpProvisionPending('The exact maintenance artifact is not visible yet.', 'maintenance')


def _target(client, state):
    source, assets = agent_package(client._runtime_config(state))
    files = {'market_intelligence.py': source, **assets}
    pack = load_package(MODULE_ID, require_available=False).pack_sha256
    descriptor = client._descriptor({**state, 'pack_sha256': pack})
    files[DESCRIPTOR] = _json(descriptor)
    return pack, files


def _production(client, state):
    rows = client._agent_deployments(f"/workspaces/{state['workspace']}/agents/{state['resources']['agent_key']}/deployments")
    if len(rows) > 1 or any(row.get('lifecycleState') != 'ACTIVE' or row.get('deploymentType') != 'PROD'
            or row.get('agentKey') != state['resources']['agent_key']
            or row.get('agentComputeKey') != state['resources']['agent_compute_key']
            or not isinstance(row.get('key'), str) or not row['key'] for row in rows):
        raise AidpProvisionConflict('PROD is not one stable deployment of this agent and compute.')
    return copy.deepcopy(rows)


def _check_production(client, state, *, preserve=False):
    rows = _production(client, state)
    journal = state.get('maintenance') or {}
    if journal.get('status') == 'running' and 'prod_snapshot' in journal:
        if rows != journal['prod_snapshot']:
            raise AidpProvisionConflict('The pinned PROD snapshot changed; TEST maintenance was not committed.')
    elif rows and not preserve:
        raise AidpProvisionConflict('An existing PROD deployment prevents edits to shared source.')
    return rows


def _bindings(client, state, *, preserve_production=False):
    workspace = str(client._workspace()['key'])
    if (state.get('workspace') != workspace or state.get('owner') != client.settings.admin_user_ocid
            or state.get('phase') != 'ready' or state.get('publication_root') != ROOT):
        raise AidpProvisionConflict('Maintenance requires this administrator and an exact ready installation receipt.')
    _assert_descriptor(client, state)
    resources = state['resources']
    agents = client._agents(workspace, MODULE_ID)
    if len(agents) != 1 or agents[0].get('key') != resources.get('agent_key'):
        raise AidpProvisionConflict('The managed agent identity changed; no resource was adopted.')
    agent = client._request('GET', f"/workspaces/{workspace}/agents/{resources['agent_key']}")
    expected = {'key': resources['agent_key'], 'type': 'CODE', 'pathInfo': metadata_root(state),
                'entryFilePath': agent_root(state) + '/market_intelligence.py',
                'dependenciesFilePath': agent_root(state) + '/requirements.txt', 'computeKey': resources['agent_compute_key']}
    if any(agent.get(key) != value for key, value in expected.items()):
        raise AidpProvisionConflict('Managed source, agent or compute binding changed.')
    _check_production(client, state, preserve=preserve_production)
    shared = client._shared_oci_credential()
    if not shared or (shared.get('key') or shared.get('id')) != resources.get('shared_oci_credential_key'):
        raise AidpProvisionConflict('The recorded shared OCI credential identity changed.')
    try:
        existing_project_reference(_project_credential(client), resources.get('apify_credential_key', ''))
    except RuntimeError:
        raise AidpProvisionConflict('The recorded Apify credential identity is not available; no secret was changed.') from None
    if resources.get('llm_credential_key'):
        llm_credential_reference(client, state)
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


def _begin(client, native, state, action, operation, session, runtime_config=None, *, preserve_production=False):
    if state.get('layout_version') != 2:
        raise AidpProvisionConflict('Legacy layout migration requires verified native rebinding; the installation and backups were preserved.')
    config = target_configuration(client, state, runtime_config)
    pack, target = _target(client, {**state, 'runtime_config': config})
    before = {name: _read(client, state['workspace'], agent_root(state) + '/' + name) for name in target}
    if any(value is None for value in before.values()):
        raise AidpProvisionConflict('The managed artifact inventory is incomplete; update cannot add or adopt files.')
    try:
        descriptor = json.loads(before[DESCRIPTOR])
    except (ValueError, TypeError):
        raise AidpProvisionConflict('The current ownership descriptor is invalid.') from None
    if descriptor != client._descriptor(state) or source_identity({name: raw for name, raw in before.items() if name != DESCRIPTOR}) != state['resources'].get('source_sha256'):
        raise AidpProvisionConflict('The installed descriptor/source no longer matches its receipt.')
    candidate_digest = inventory_digest(file_hashes({name: raw for name, raw in before.items() if name != DESCRIPTOR}))
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
    journal = {'schema_version': 2, 'operation_id': operation, 'action': action, 'phase': 'backup', 'status': 'running',
               'before_state': snapshot, 'before_config_sha256': runtime_config_hash(client._runtime_config(state)),
               'target_pack_sha256': pack, 'session_key': chosen,
               'target_runtime_config': copy.deepcopy(config), 'target_config_sha256': runtime_config_hash(config),
               'before_identity': deployment_identity(row), 'before_hashes': {name: _digest(raw) for name, raw in before.items()},
               'target_hashes': {name: _digest(raw) for name, raw in target.items()}}
    if preserve_production:
        journal['prod_snapshot'] = _production(client, state)
    if getattr(native, 'deployment_driver', None) == 'plugin_cli':
        journal['test_driver'] = native.driver_binding()
    previous = state.get('maintenance')
    if previous:
        state.setdefault('maintenance_history', []).append({key: previous.get(key) for key in
            ('operation_id', 'action', 'status', 'phase', 'target_pack_sha256')})
    state['maintenance'] = journal
    _validate_journal(state)
    _save(client, state)


def _backup_root(client, journal, side):
    return getattr(client, 'control_root', CONTROL_ROOT) + '/maintenance/' + journal['operation_id'] + '/' + side


def _backups(client, state, side):
    journal = state['maintenance']
    result = {}
    for name, digest in journal[side + '_hashes'].items():
        raw = _read(client, state['workspace'], _backup_root(client, journal, side) + '/' + name)
        if raw is None or _digest(raw) != digest:
            raise AidpProvisionConflict('The immutable maintenance backup is missing or changed.')
        result[name] = raw
    return result


def _snapshot_files(client, state):
    journal = state['maintenance']
    if journal['schema_version'] == 1:
        raise AidpProvisionConflict('Finish this historical backup with its exact saved installer before migrating layouts.')
    pack, target = _target(client, {**journal['before_state'], 'runtime_config': journal['target_runtime_config']})
    if pack != journal['target_pack_sha256'] or {name: _digest(raw) for name, raw in target.items()} != journal['target_hashes']:
        raise AidpProvisionConflict('Resume with the exact saved maintenance package; its target changed.')
    for side in ('before', 'target'):
        parents = {str(parent) for name in journal[side + '_hashes']
                   for parent in PurePosixPath(_backup_root(client, journal, side) + '/' + name).parents
                   if str(parent).startswith(getattr(client, 'control_root', CONTROL_ROOT) + '/maintenance')}
        for parent in sorted(parents, key=lambda path: (path.count('/'), path)):
            verify_administrator(client)
            _assert_descriptor(client, state)
            client._ensure_folder(state['workspace'], parent)
        for name in journal[side + '_hashes']:
            destination = _backup_root(client, journal, side) + '/' + name
            if side == 'before':
                raw = _read(client, state['workspace'], agent_root(state) + '/' + name)
                if raw is None or _digest(raw) != journal['before_hashes'][name]:
                    raise AidpProvisionConflict('The original source changed before backup; it was preserved.')
            else:
                raw = target[name]
            _guarded_upload(client, state, destination, raw, allowed=(None, raw))
    _backups(client, state, 'before')
    _backups(client, state, 'target')
    journal['phase'] = 'target_files'
    _save(client, state)


def _replace_files(client, state, side, native=None):
    before, target = _backups(client, state, 'before'), _backups(client, state, 'target')
    desired = before if side == 'before' else target
    if 'test_driver' in state['maintenance']:
        _check_production(client, state)
        native.sync_candidate(state, desired)
        _check_production(client, state)
    for name, raw in desired.items():
        if name != DESCRIPTOR or 'test_driver' in state['maintenance']:
            _guarded_upload(client, state, agent_root(state) + '/' + name, raw, allowed=(before[name], target[name]))
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
        files['market_intelligence.py'], assets={name: raw for name, raw in files.items() if name not in {DESCRIPTOR, 'market_intelligence.py'} and (state.get('layout_version') == 2 or name == 'requirements.txt')},
        expected_workspace=state['workspace'], agent_root=agent_root(state), initialize=False, on_submit=submitted,
        **({'metadata_descriptor': files[DESCRIPTOR]} if 'test_driver' in journal else {}),
        **({'metadata_root': metadata_root(state)} if metadata_root(state) != agent_root(state) else {}))
    journal[prefix + '_receipt'] = receipt
    _save(client, state)


def _poll(client, native, state, rollback):
    journal = state['maintenance']
    prefix, side = ('rollback', 'before') if rollback else ('target', 'target')
    if 'test_driver' in journal:
        if native.reconcile_refresh(journal[prefix + '_intent']) is False:
            if rollback:
                raise AidpProvisionConflict('The plugin rollback TEST failed; preserve its exact journals.')
            journal.update(phase='rollback_files', failure='plugin_native_terminal')
            _save(client, state)
            return
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
        if name != DESCRIPTOR and _read(client, state['workspace'], agent_root(state) + '/' + name) != content:
            raise AidpProvisionConflict('Managed files changed after refresh; the revision was not accepted.')
    try:
        verify_administrator(client)
        config = journal['before_state'].get('runtime_config', {'research_enabled': False}) if rollback else journal['target_runtime_config']
        probe = client._probe_market(row['endpointUrl'], journal['session_key'],
            source_identity({name: raw for name, raw in files.items() if name != DESCRIPTOR}),
            **({'research_enabled': True, 'runtime_profile': config['services']['runtime_profile']}
               if config['research_enabled'] else {}))
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
    _guarded_upload(client, state, agent_root(state) + '/' + DESCRIPTOR, chosen[DESCRIPTOR],
                    allowed=(before[DESCRIPTOR], target[DESCRIPTOR]))
    resources = copy.deepcopy(journal['before_state']['resources'])
    executable = {name: raw for name, raw in chosen.items() if name != DESCRIPTOR}
    if state.get('layout_version') == 2:
        resources['package_hashes'] = file_hashes(executable)
    resources.update(source_sha256=source_identity(executable),
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
    _check_production(client, state)
    journal.update(status='rolled_back' if rollback else 'completed', phase='ready')
    _save(client, state)


def _result(journal, action):
    config = journal['before_state'].get('runtime_config', {'research_enabled': False}) if journal['status'] == 'rolled_back' else journal['target_runtime_config']
    return {'status': journal['status'], 'phase': journal['phase'], 'operation_id': journal['operation_id'],
            'action': action, 'target_pack_sha256': journal['target_pack_sha256'],
            'research_enabled': config['research_enabled'], 'deployment_surface': 'TEST',
            'configured_agent_model_id': config.get('services', {}).get('model_id', '')}


def _select_rollback(client, state, factory):
    journal = state['maintenance']
    if journal['phase'] == 'backup':
        raise AidpProvisionConflict('Finish the immutable backup before selecting rollback.')
    if journal['phase'] == 'target_wait':
        observer = factory(client.settings)
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


def _resume_operation(client, state, action, test_session_id, runtime_config, factory):
    journal = state['maintenance']
    if action != 'rollback' and journal['status'] in {'completed', 'rolled_back'}:
        bundled = load_package(MODULE_ID, require_available=False).pack_sha256
        if runtime_config_hash(target_configuration(client, state, runtime_config)) != journal['target_config_sha256']:
            raise AidpProvisionConflict('This maintenance operation belongs to another model configuration; use a new operation for changed configuration.')
        if bundled != journal['target_pack_sha256']:
            raise AidpProvisionConflict('This maintenance operation belongs to another bundled candidate; use a new operation for a changed package.')
    if action == 'rollback' and journal['status'] != 'rolled_back':
        _select_rollback(client, state, factory)
    elif action != journal['action'] and action != 'rollback':
        raise AidpProvisionConflict('Resume the saved maintenance action and operation.')
    if journal['status'] in {'completed', 'rolled_back'}:
        return _result(journal, action)
    if test_session_id and test_session_id != journal['session_key']:
        raise AidpProvisionConflict('Resume with the recorded TEST session; no session was reset.')
    return None


def _prepare_operation(client, state, action, operation_id, test_session_id, runtime_config, preserve_production, factory):
    journal = state.get('maintenance')
    resuming = journal is not None and journal['operation_id'] == operation_id
    if resuming and 'prod_snapshot' in journal and not preserve_production:
        raise AidpProvisionConflict('Resume the explicit PROD-preserving maintenance contract.')
    if runtime_config is not None:
        if action != 'update' or not validated_runtime_config(runtime_config)['research_enabled']:
            raise ValueError('An explicit research target requires the update operation')
        if resuming and runtime_config_hash(runtime_config) != journal['target_config_sha256']:
            raise AidpProvisionConflict('Resume the exact saved research binding and operation.')
    if resuming:
        return _resume_operation(client, state, action, test_session_id, runtime_config, factory)
    if journal and journal['status'] == 'running':
        raise AidpProvisionConflict('Another maintenance operation is active; resume its exact identity.')
    if action == 'rollback':
        raise AidpProvisionConflict('Rollback requires the recorded maintenance operation and immutable backup.')
    observer = factory(client.settings)
    try:
        _begin(client, observer, state, action, operation_id, test_session_id, runtime_config,
               preserve_production=preserve_production)
    finally:
        observer.session.close()
    return None


def _advance_phase(client, native, state):
    phase = state['maintenance']['phase']
    if phase == 'backup':
        _snapshot_files(client, state)
    elif phase in {'target_files', 'rollback_files'}:
        _replace_files(client, state, 'before' if phase == 'rollback_files' else 'target', native)
    elif phase in {'target_refresh', 'rollback_refresh'}:
        _refresh(client, native, state, phase == 'rollback_refresh')
    elif phase in {'target_wait', 'rollback_wait'}:
        _poll(client, native, state, phase == 'rollback_wait')
    elif phase in {'commit', 'rollback_commit'}:
        _complete(client, native, state, phase == 'rollback_commit')


def _admit_maintenance(client, action, operation_id, preserve_production, llm_api_key):
    """Validate the existing installation before preparing a connection under its lock."""
    verify_administrator(client)
    state = client._read_installation(str(client._workspace()['key']))
    if not state:
        raise AidpProvisionConflict('Install this product before maintenance; no resources were created.')
    _validate_journal(state)
    _bindings(client, state, preserve_production=preserve_production)
    journal = state.get('maintenance')
    if journal and journal.get('status') == 'running' and journal['operation_id'] != operation_id:
        raise AidpProvisionConflict('Another maintenance operation is active; resume its exact identity.')
    resuming = journal and journal['operation_id'] == operation_id
    validate_llm_secret(state, llm_api_key)
    if action == 'update' and not resuming and getattr(client.settings, 'llm_auth_mode', 'oci_iam') == OPENAI_MODE:
        ensure_llm_credential(client, state, llm_api_key)
    return state


def maintain(client, action, operation_id, *, test_session_id=None, max_steps=80, sleep=time.sleep, runtime_config=None,
             preserve_production=False, native_factory=None, llm_api_key=None):
    if action not in {'update', 'redeploy', 'rollback'} or str(UUID(operation_id)) != operation_id or not 1 <= max_steps <= 120:
        raise ValueError('Choose update/redeploy/rollback with a canonical maintenance UUID and bounded progress.')
    with installation_lock(client, operation_id):
        state = _admit_maintenance(client, action, operation_id, preserve_production, llm_api_key)
        factory = native_factory or MarketPlaygroundClient
        result = _prepare_operation(client, state, action, operation_id, test_session_id, runtime_config,
                                    preserve_production, factory)
        if result is not None:
            return result
        journal = state['maintenance']
        native = factory(client.settings)
        actual_driver = native.driver_binding() if getattr(native, 'deployment_driver', None) == 'plugin_cli' else None
        if actual_driver != journal.get('test_driver'):
            native.session.close()
            raise AidpProvisionConflict('Resume with the pinned plugin deployment driver.')
        try:
            for _ in range(max_steps):
                verify_administrator(client)
                compute = _bindings(client, state, preserve_production=preserve_production)
                journal = state['maintenance']
                try:
                    _ensure_compute(client, state, compute)
                    _advance_phase(client, native, state)
                    if journal['status'] in {'completed', 'rolled_back'}:
                        return _result(journal, action)
                except (AidpProvisionPending, TimeoutError):
                    # Intents survive uncertain responses; subsequent iterations only poll or reconcile files.
                    pass
                sleep(5)
        finally:
            native.session.close()
    raise AidpProvisionPending('Maintenance remains pending. Resume its exact operation without resubmitting a refresh.', 'maintenance')
