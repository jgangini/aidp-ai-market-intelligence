"""Exclusive, durable installer ownership through conditional Object Storage writes."""
from contextlib import contextmanager
import hashlib
import json
from uuid import UUID, uuid4
from .errors import AidpProvisionConflict


def lock_path(client):
    binding = client.settings.aidp_platform_id + ':' + client._workspace()['key']
    return '.control/ai_market_intelligence/locks/' + hashlib.sha256(binding.encode()).hexdigest() + '.json'


@contextmanager
def installation_lock(client, operation_id):
    if str(UUID(operation_id)) != operation_id:
        raise ValueError('A canonical operation UUID is required')
    name = lock_path(client)
    storage = client.object_storage
    args = (client.settings.objectstorage_namespace, client.settings.artifacts_bucket_name, name)
    nonce = str(uuid4())
    body = json.dumps({'operation_id': operation_id, 'holder': nonce}, sort_keys=True).encode()
    try:
        created = storage.put_object(*args, body, if_none_match='*', content_type='application/json')
    except client._oci.exceptions.ServiceError as exc:
        if exc.status in (409, 412):
            raise AidpProvisionConflict('An installation lock exists. Reconcile the running hook before releasing its exact ETag.') from None
        raise
    etag = created.headers.get('etag')
    if not etag:
        raise AidpProvisionConflict('Lock write is uncertain. Preserve it and reconcile before another installer starts.')
    try:
        yield
    finally:
        storage.delete_object(*args, if_match=etag)


def release_lock(client, *, expected_etag):
    """Explicit recovery only after the authorized operator confirms no hook is running."""
    if not expected_etag or any(ord(char) < 32 for char in expected_etag):
        raise ValueError('The exact observed lock ETag is required')
    client.object_storage.delete_object(client.settings.objectstorage_namespace,
        client.settings.artifacts_bucket_name, lock_path(client), if_match=expected_etag)
