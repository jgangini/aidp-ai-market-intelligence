"""Move control children into an existing folder, handing off the receipt last."""
import hashlib
import json
from pathlib import PurePosixPath
import re

from ..errors import AidpProvisionConflict, AidpProvisionPending, AidpRequestError


def _relative(name):
    return (isinstance(name, str) and bool(re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', name))
            and all(p not in {'.', '..'} for p in name.split('/')))


def _children(client, workspace, root):
    rows = client._list(f'/workspaces/{workspace}/objects', params={'path': root})
    if len(rows) > 1000:
        raise AidpProvisionConflict('Control inventory exceeds its bounded size.')
    result = {}
    for row in rows:
        path, kind = row.get('path'), row.get('type')
        if (not isinstance(path, str) or str(PurePosixPath(path).parent) != root
                or kind not in {'FILE', 'FOLDER'} or not _relative(PurePosixPath(path).name)):
            raise AidpProvisionConflict('Control listing includes an unsupported or foreign object.')
        name = PurePosixPath(path).name
        if name in result:
            raise AidpProvisionConflict('Control listing contains duplicate paths.')
        result[name] = kind
    return result


def _snapshot(client, state, root, read):
    hashes, stack, roots, visited = {}, [root], {}, set()
    while stack:
        folder = stack.pop()
        if folder in visited or len(visited) > 1000 or len(PurePosixPath(folder).parts) > 30:
            raise AidpProvisionConflict('Control tree is cyclic or exceeds its bounded depth.')
        visited.add(folder)
        for name, kind in _children(client, state['workspace'], folder).items():
            path = folder + '/' + name
            relative = path.removeprefix(root + '/')
            if folder == root:
                if name == 'installer.json':
                    continue
                roots[name] = kind
            if kind == 'FOLDER':
                stack.append(path)
            else:
                raw = read(client, state, path)
                if raw is None or len(hashes) >= 1000:
                    raise AidpProvisionConflict('Control file disappeared or inventory is too large.')
                hashes[relative] = hashlib.sha256(raw).hexdigest()
    return {'schema_version': 1, 'roots': roots, 'hashes': hashes, 'completed': [], 'pending': None}


def _validate(move):
    if (not isinstance(move, dict) or move.get('schema_version') != 1
            or not isinstance(move.get('roots'), dict) or not isinstance(move.get('hashes'), dict)
            or len(move['hashes']) > 1000 or len(move['roots']) > 1000
            or any(not _relative(name) or '/' in name or name == 'installer.json' or kind not in {'FILE', 'FOLDER'}
                   for name, kind in move['roots'].items())
            or any(not _relative(name) or name.split('/')[0] not in move['roots']
                   or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)
                   for name, digest in move['hashes'].items())
            or not isinstance(move.get('completed'), list)
            or any(name not in move['roots'] for name in move['completed'])
            or len(set(move['completed'])) != len(move['completed'])
            or move.get('pending') not in {None, 'installer.json', *move['roots']}):
        raise AidpProvisionConflict('The control handoff journal is invalid.')


def _at(client, state, path):
    body, headers = client._workspace_object(state['workspace'], path, phase='migration')
    return client._workspace_object_exists(body, headers), client._workspace_object_type(body, headers)


def _verify_child(client, state, destination, name, move, read):
    exists, kind = _at(client, state, destination + '/' + name)
    if not exists or kind not in {'', move['roots'][name]}:
        raise AidpProvisionPending('Waiting for the submitted control object; move was not repeated.', 'migration')
    for relative, digest in move['hashes'].items():
        if relative == name or relative.startswith(name + '/'):
            raw = read(client, state, destination + '/' + relative)
            if raw is None:
                raise AidpProvisionPending('Waiting for moved control content.', 'migration')
            if hashlib.sha256(raw).hexdigest() != digest:
                raise AidpProvisionConflict('Moved control bytes differ from the pinned inventory.')


def advance(client, state, source, destination, *, read, save):
    """Return true only after all pinned children and the receipt have moved."""
    journal = state['migration']
    workspace = state['workspace']
    move = journal.get('control_move')
    if move is None:
        rejection = journal.get('move_rejection', {})
        if journal['phase'] == 'move_wait' and rejection.get('status_code') != 400:
            raise AidpProvisionPending('Reconcile the earlier control move before changing its protocol.', 'migration')
        if read(client, state, destination + '/installer.json') is not None:
            raise AidpProvisionConflict('Another receipt exists at the control destination.')
        client._ensure_folder(workspace, destination)
        journal['control_move'] = move = _snapshot(client, state, source, read)
        journal['phase'] = 'move_control'
        save(client, state)
    _validate(move)
    name = move['pending']
    if name is None:
        remaining = sorted(set(move['roots']) - set(move['completed']))
        name = remaining[0] if remaining else 'installer.json'
        if name != 'installer.json':
            exists, _ = _at(client, state, destination + '/' + name)
            if exists:
                raise AidpProvisionConflict('Control destination already contains an unsubmitted child.')
            # Recheck the pinned source before handing it to the service.
            _verify_child(client, state, source, name, move, read)
        else:
            if read(client, state, destination + '/installer.json') is not None:
                raise AidpProvisionConflict('The installer handoff destination changed.')
        move['pending'] = name
        journal['phase'] = 'move_wait'
        save(client, state)
        try:
            client._request('POST', f'/workspaces/{workspace}/actions/moveObject',
                payload={'fromPath': source + '/' + name, 'toPath': destination + '/'}, phase='migration',
                retry_scope='market-migration-control:' + journal['operation_id'] + ':' + name)
        except AidpRequestError as error:
            journal['control_move_rejection'] = error.diagnostic
            save(client, state)
            raise
    if journal.get('control_move_rejection'):
        raise AidpProvisionConflict('A control child move was rejected; preserve its diagnostic before changing the request.')
    source_exists, _ = _at(client, state, source + '/' + name)
    if source_exists:
        raise AidpProvisionPending('Waiting for the submitted control move; it was not repeated.', 'migration')
    if name == 'installer.json':
        raw = read(client, state, destination + '/installer.json')
        if raw is None or json.loads(raw) != state:
            raise AidpProvisionConflict('Moved installer receipt differs from the recorded handoff.')
        client.control_root = destination
        move['pending'] = None
        return True
    _verify_child(client, state, destination, name, move, read)
    move['completed'].append(name)
    move['pending'] = None
    journal['phase'] = 'move_control'
    save(client, state)
    return False
