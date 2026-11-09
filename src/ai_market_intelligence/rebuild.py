"""Replace a development CODE object to match the current source layout exactly."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from uuid import UUID

from .authorization import verify_administrator
from .client import MODULE_ID
from .errors import AidpProvisionConflict, AidpProvisionPending
from .installation.layout import AGENT_ROOT, LEGACY_AGENT_ROOT, metadata_root
from .maintenance import _target
from .operation import installation_lock
from .runtime.package_identity import inventory_digest


PRESERVED = ('agent_compute_key', 'shared_oci_credential_key', 'apify_credential_key', 'compute_creation_operation')


def prepare(root, client, operation, plugin_scripts):
    if str(UUID(operation)) != operation:
        raise ValueError('A canonical replacement operation is required.')
    sys.path.insert(0, str(Path(plugin_scripts).resolve()))
    from aidp_agent_flow.native_lifecycle import NativeLifecycle
    from aidp_agent_flow.project import load_project

    state = client._read_installation(client.settings.aidp_workspace_key)
    if not state or state.get('layout_version') != 2:
        raise AidpProvisionConflict('Replacement requires the owned current installation.')
    journal = state.get('rebuild')
    if journal and journal.get('operation_id') != operation:
        raise AidpProvisionConflict('Resume the recorded replacement operation.')
    parent = Path(root).resolve() / '.aidp-local/market/rebuilds' / operation
    old_path, current_path = parent / 'previous', parent / 'current'
    if not journal:
        if state.get('phase') != 'ready' or metadata_root(state) != LEGACY_AGENT_ROOT:
            raise AidpProvisionConflict('Replacement is limited to the observed obsolete metadata binding.')
        pack, target = _target(client, state)
        if pack != state['pack_sha256']:
            raise AidpProvisionConflict('Freeze the current candidate before replacing its CODE object.')
        for folder, native_metadata, key in ((old_path, LEGACY_AGENT_ROOT, state['resources']['agent_key']),
                                             (current_path, AGENT_ROOT, None)):
            for name, raw in target.items():
                path = folder / 'candidate' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists() and path.read_bytes() != raw:
                    raise AidpProvisionConflict('The saved replacement candidate differs.')
                if not path.exists():
                    path.write_bytes(raw)
            fields = {'agent': {'factory': 'market_intake:build_graph', 'python_path': '.',
                      'entry': 'candidate/market_intelligence.py', 'dependencies': 'candidate/requirements.txt'},
                      'oci': {'config_file': str(Path(client.settings.oci_config_file).resolve()), 'profile': 'DEFAULT'},
                      'native': {'endpoint': client.base.split('/20260430/')[0],
                      'platform_id': client.settings.aidp_platform_id, 'workspace_key': state['workspace'],
                      'remote_root': AGENT_ROOT, 'metadata_root': native_metadata, 'display_name': MODULE_ID,
                      'compute_key': state['resources']['agent_compute_key']}}
            if key:
                fields['native']['agent_key'] = key
            config = folder / 'aidp-agent.toml'
            text = '\n'.join('[' + section + ']\n' + '\n'.join(k + ' = ' + json.dumps(v)
                            for k, v in values.items()) + '\n' for section, values in fields.items())
            if config.exists() and config.read_text(encoding='utf-8') != text:
                raise AidpProvisionConflict('The saved replacement configuration changed.')
            if not config.exists():
                config.write_text(text, encoding='utf-8', newline='\n')
    client.session.auth = client.signer
    return tuple(NativeLifecycle(path, load_project(path)[1], transport=client.session)
                 for path in (old_path, current_path))


def _journal(state, operation):
    journal = state.get('rebuild')
    if (not isinstance(journal, dict) or journal.get('schema_version') != 1
            or journal.get('operation_id') != operation or journal.get('phase') not in
            {'retire', 'create', 'prune', 'permissions', 'test_init', 'test_wait', 'verify', 'complete'}
            or any(state['resources'].get(k) != journal['preserved'].get(k) for k in PRESERVED)):
        raise AidpProvisionConflict('Replacement identity or journal changed.')
    return journal


def _bindings(client, state, previous, current):
    journal = state.get('rebuild') or {}
    before = journal.get('before_agent', state['resources']['agent_key'])
    for native, metadata in ((previous, LEGACY_AGENT_ROOT), (current, AGENT_ROOT)):
        if (native.base != client.base or native.workspace != state['workspace']
                or native.compute != state['resources']['agent_compute_key']
                or native.remote_root != AGENT_ROOT or native.metadata_root != metadata or native.name != MODULE_ID):
            raise AidpProvisionConflict('Replacement lifecycle target differs from the owned installation.')
    if previous.agent_key != before or current.config['native'].get('agent_key'):
        raise AidpProvisionConflict('Replacement requires the recorded old agent and a journal-owned new identity.')
    if current.agent_key:
        creates = [row for _, row in current._intents() if row.get('kind') == 'agent'
                   and (row.get('resource_key') == current.agent_key and row.get('state') == 'RECONCILED'
                        or journal.get('phase') == 'create' and row.get('resource_key') in (None, current.agent_key)
                        and row.get('state') in {'SUBMITTED', 'ACKNOWLEDGED'})]
        if (len(creates) != 1 or (journal.get('agent_key') and journal['agent_key'] != current.agent_key)):
            raise AidpProvisionConflict('The replacement agent has no exact owned creation receipt.')
    elif journal.get('agent_key'):
        raise AidpProvisionConflict('The recorded replacement identity disappeared from its journal.')


def _backup_obsolete(previous, current, hashes):
    from aidp_agent_flow.native_layout import inventory
    from aidp_agent_flow.native_lifecycle import _unlinked
    if not isinstance(hashes, dict) or not hashes:
        raise AidpProvisionConflict('The obsolete owned inventory is missing.')
    files, _ = inventory(previous, LEGACY_AGENT_ROOT)
    if files != set(hashes):
        raise AidpProvisionConflict('The obsolete tree differs from its owned inventory; no agent was retired.')
    for name in sorted(files):
        raw = previous._object(LEGACY_AGENT_ROOT + '/' + name)
        if raw is None or hashlib.sha256(raw).hexdigest() != hashes[name]:
            raise AidpProvisionConflict('Obsolete source changed before its local backup.')
        path = current.root.parent / 'before-source' / name
        _unlinked(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != raw:
            raise AidpProvisionConflict('The obsolete local backup already differs.')
        if not path.exists():
            with path.open('xb') as stream:
                stream.write(raw)


def advance(client, previous, current, operation, *, apply=False):
    """Advance one durable phase. Repeated calls reconcile the same operation."""
    from contextlib import nullcontext
    from aidp_agent_flow.native_layout import prune_tree

    if str(UUID(operation)) != operation:
        raise ValueError('A canonical replacement operation is required.')
    with installation_lock(client, operation) if apply else nullcontext():
        state = client._read_installation(client.settings.aidp_workspace_key)
        if not state or state.get('layout_version') != 2 or state.get('owner') != client.settings.admin_user_ocid:
            raise AidpProvisionConflict('Replacement requires the current owned installation.')
        _bindings(client, state, previous, current)
        journal = state.get('rebuild')
        if not journal:
            if (state.get('phase') != 'ready' or metadata_root(state) != LEGACY_AGENT_ROOT
                    or (state.get('maintenance') or {}).get('status') == 'running'):
                raise AidpProvisionConflict('Replacement requires the observed old metadata binding.')
            plan = previous.retire_agent()
            package, _ = current._package()
            current._verify_package(package)
            if not apply:
                return {'action': 'rebuild-development', 'applied': False, 'will_replace_agent_and_session': True,
                        'compute_and_credentials_preserved': True, 'legacy_tree_to_remove': LEGACY_AGENT_ROOT,
                        'native_test_accepted': False}
            verify_administrator(client)
            _backup_obsolete(previous, current, state['migration']['before_hashes'])
            before = copy.deepcopy(state)
            backup = current.root.parent / 'before-installer.json'
            raw = json.dumps(before, sort_keys=True).encode()
            if backup.exists() and backup.read_bytes() != raw:
                raise AidpProvisionConflict('Replacement backup already differs.')
            if not backup.exists():
                backup.write_bytes(raw)
            journal = state['rebuild'] = {'schema_version': 1, 'operation_id': operation, 'phase': 'retire',
                'status': 'running', 'before_sha256': hashlib.sha256(raw).hexdigest(),
                'before_agent': before['resources']['agent_key'],
                'before_session': before['resources'].get('test_session_key'),
                'preserved': {k: before['resources'].get(k) for k in PRESERVED},
                'legacy_hashes': before['migration']['before_hashes'], 'retirement_binding': plan['binding_sha256'],
                'package_sha256': package['package_sha256']}
            state.update(phase='rebuild', status='updating')
            client._save(state)
        journal = _journal(state, operation)
        if not apply:
            return {'action': 'rebuild-development', 'applied': False, 'phase': journal['phase']}
        verify_administrator(client)
        package, _ = current._package()
        if package['package_sha256'] != journal['package_sha256']:
            raise AidpProvisionConflict('Replacement candidate changed; preserve the submitted package.')
        phase = journal['phase']
        if phase == 'retire':
            previous.retire_agent(apply=True, expected_binding=journal['retirement_binding'])
            journal['phase'] = 'create'
        elif phase == 'create':
            result = current.ensure_agent(apply=True)
            journal['agent_key'] = result['agent_key']
            journal['phase'] = 'prune'
        elif phase == 'prune':
            if 'prune_hashes' not in journal:
                from aidp_agent_flow.native_layout import inventory
                files = inventory(current, LEGACY_AGENT_ROOT)[0] if current._object(LEGACY_AGENT_ROOT) is not None else set()
                if files - set(journal['legacy_hashes']):
                    raise AidpProvisionConflict('An unknown file appeared in the obsolete tree.')
                journal['prune_hashes'] = {name: journal['legacy_hashes'][name] for name in files}
                journal['retirement_removed_tree'] = current._object(LEGACY_AGENT_ROOT) is None
                client._save(state)
            if not journal['retirement_removed_tree']:
                prune_tree(current, LEGACY_AGENT_ROOT, journal['prune_hashes'], apply=True)
            if not current.inspect_layout()['layout_matches']:
                raise AidpProvisionConflict('Replacement source tree is still not exact.')
            journal['phase'] = 'permissions'
        elif phase == 'permissions':
            client._ensure_role_permission(f"/workspaces/{state['workspace']}/agents/{journal['agent_key']}",
                                          'assignAgentPermissionDetails', 'AI_DATA_PLATFORM_ADMIN', 'ADMIN')
            journal['phase'] = 'test_init'
        elif phase == 'test_init':
            current.status()
            if not current._deployments('TEST') and not any(row['kind'] == 'test-init' for _, row in current._intents()):
                current.test_init(apply=True)
            journal['phase'] = 'test_wait'
        elif phase == 'test_wait':
            current.status()
            rows = current._deployments('TEST')
            if any(row['lifecycleState'] == 'FAILED' for row in rows):
                raise AidpProvisionConflict('Replacement TEST failed; retain its recorded operation.')
            if len(rows) != 1 or rows[0]['lifecycleState'] != 'ACTIVE' or not current.state.get('test_receipt'):
                raise AidpProvisionPending('Waiting for the exact replacement TEST.', 'rebuild')
            resources = state['resources']
            for key in ('test_session_key', 'test_session_requested', 'test_verified', 'test_receipt'):
                resources.pop(key, None)
            resources.update(agent_key=journal['agent_key'], test_intent={'package_sha256': inventory_digest(resources['package_hashes'])})
            state['agent_metadata_root'] = AGENT_ROOT
            state['test_sessions'] = []
            journal['phase'] = 'verify'
        elif phase == 'verify':
            client._test_step(state, None)
            if not current.inspect_layout()['layout_matches']:
                raise AidpProvisionConflict('Source layout changed during native verification.')
            journal.update(phase='complete', status='completed', layout_verified=True,
                           session_key=state['resources']['test_session_key'])
            # The full prior journal is in the immutable local before-installer snapshot.
            state.pop('maintenance', None)
            state.update(phase='ready', status='installed')
        client._save(state)
        return {'action': 'rebuild-development', 'applied': True, 'phase': journal['phase'],
                'status': journal['status'], 'layout_matches': journal.get('layout_verified', False),
                'code_root': AGENT_ROOT, 'native_test_accepted': False, 'research_enabled': False}
