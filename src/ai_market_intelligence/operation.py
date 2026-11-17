"""Exclusive, durable installer ownership through conditional Object Storage writes."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from uuid import UUID, uuid4
from .errors import AidpProvisionConflict


@dataclass
class InstallationLease:
    operation_id: str
    retain_on_exit: bool = False


def lock_path(client):
    binding = client.settings.aidp_platform_id + ':' + client._workspace()['key']
    return '.control/ai_market_intelligence/locks/' + hashlib.sha256(binding.encode()).hexdigest() + '.json'


def _guard(client, args):
    response = client.object_storage.get_object(*args)
    raw = response.data.raw.read(16385)
    try:
        value = json.loads(raw) if len(raw) <= 16384 else None
    except (ValueError, TypeError):
        return None, response.headers.get('etag')
    binding = lock_path(client).rsplit('/', 1)[-1].removesuffix('.json')
    if (not isinstance(value, dict) or value.get('protocol') != 2
            or value.get('binding') != binding or value.get('state') not in {'held', 'idle'}):
        return None, response.headers.get('etag')
    return value, response.headers.get('etag')


def _idle(client):
    return {'protocol': 2, 'state': 'idle',
            'binding': lock_path(client).rsplit('/', 1)[-1].removesuffix('.json')}


@contextmanager
def installation_lock(client, operation_id):
    if str(UUID(operation_id)) != operation_id:
        raise ValueError('A canonical operation UUID is required')
    name = lock_path(client)
    storage = client.object_storage
    args = (client.settings.objectstorage_namespace, client.settings.artifacts_bucket_name, name)
    nonce = str(uuid4())
    body = json.dumps({'operation_id': operation_id, 'holder': nonce}, sort_keys=True).encode()
    persistent = False
    try:
        created = storage.put_object(*args, body, if_none_match='*', content_type='application/json')
    except client._oci.exceptions.ServiceError as exc:
        if exc.status in (409, 412):
            guard, previous = _guard(client, args)
            if guard is None or guard['state'] != 'idle' or not previous:
                raise AidpProvisionConflict('An installation lock exists. Reconcile the running hook before releasing its exact ETag.') from None
            body = json.dumps({**_idle(client), 'state': 'held', 'operation_id': operation_id, 'holder': nonce}, sort_keys=True).encode()
            try:
                created = storage.put_object(*args, body, if_match=previous, content_type='application/json')
            except client._oci.exceptions.ServiceError as failure:
                if failure.status in (409, 412):
                    raise AidpProvisionConflict('An installation lock exists. Another operator acquired its exact revision.') from None
                raise
            persistent = True
        else:
            raise
    etag = created.headers.get('etag')
    if not etag:
        raise AidpProvisionConflict('Lock write is uncertain. Preserve it and reconcile before another installer starts.')
    lease = InstallationLease(operation_id)
    try:
        yield lease
    finally:
        if not lease.retain_on_exit:
            if persistent or getattr(client, 'retain_installation_guard', False):
                storage.put_object(*args, json.dumps(_idle(client), sort_keys=True).encode(),
                                   if_match=etag, content_type='application/json')
            else:
                storage.delete_object(*args, if_match=etag)


def release_lock(client, *, expected_etag):
    """Explicit recovery only after the authorized operator confirms no hook is running."""
    if not expected_etag or any(ord(char) < 32 for char in expected_etag):
        raise ValueError('The exact observed lock ETag is required')
    args = (client.settings.objectstorage_namespace, client.settings.artifacts_bucket_name, lock_path(client))
    guard, _ = _guard(client, args)
    reader = getattr(client, '_read_installation', None)
    state = reader(str(client._workspace()['key'])) if callable(reader) else None
    migrated = bool(state and (state.get('migration') or state.get('migration_recovery') or state.get('agent_metadata_root')))
    if guard is not None or migrated:
        client.object_storage.put_object(*args, json.dumps(_idle(client), sort_keys=True).encode(),
                                        if_match=expected_etag, content_type='application/json')
    else:
        client.object_storage.delete_object(*args, if_match=expected_etag)
