"""Correlate a selected memory database with its accepted native operation."""
import hashlib
import json
import re

import oci

from .errors import AidpMemoryAssociationConflict


def intent_key(platform_id):
    return 'control/ai_market_intelligence/memory/' + hashlib.sha256(platform_id.encode()).hexdigest() + '.json'


def response_etag(response):
    value = next((v for k, v in response.headers.items() if k.lower() == 'etag'), None)
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise AidpMemoryAssociationConflict('Memory verification requires the exact resource ETag.')
    return value


def save_receipt(client, key, record, etag):
    response = client.object_storage.put_object(client.settings.objectstorage_namespace,
        client.settings.artifacts_bucket_name, key,
        json.dumps(record, sort_keys=True, separators=(',', ':')).encode(),
        if_match=etag, content_type='application/json')
    return record, response_etag(response)


def accepted_receipt(client, intent, intent_etag, attempt, response):
    request_id = next((v for k, v in response.headers.items() if k.lower() == 'opc-work-request-id'), None)
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,1024}', request_id):
        raise AidpMemoryAssociationConflict('The accepted memory request has no verifiable work request identifier.')
    record = {**intent, 'status': 'accepted', 'attempt': attempt, 'work_request_id': request_id}
    return save_receipt(client, intent_key(intent['platform_id']), record, intent_etag)


def read_receipt(client, intent):
    try:
        response = client.object_storage.get_object(client.settings.objectstorage_namespace,
            client.settings.artifacts_bucket_name, intent_key(intent['platform_id']))
    except oci.exceptions.ServiceError as exc:
        if exc.status == 404:
            return None
        raise
    content = response.data.content
    try:
        record = json.loads(content) if isinstance(content, bytes) and len(content) <= 4096 else None
    except ValueError:
        record = None
    required = {*intent, 'status', 'attempt', 'work_request_id'}
    if (not isinstance(record, dict) or record.get('status') not in ('accepted', 'ready')
            or set(record) != required | ({'platform_etag'} if record.get('status') == 'ready' else set())
            or any(record.get(k) != v for k, v in intent.items())
            or type(record.get('attempt')) is not int or not 1 <= record['attempt'] < 1000
            or not isinstance(record.get('work_request_id'), str)
            or not re.fullmatch(r'[A-Za-z0-9._-]{1,1024}', record['work_request_id'])):
        raise AidpMemoryAssociationConflict('The memory association has no matching accepted request receipt.')
    return record, response_etag(response)


def verify_request(sdk, record, platform_id):
    request = sdk.get_work_request(record['work_request_id']).data
    resources = request.resources or []
    if (request.id != record['work_request_id'] or request.operation_type != 'ENABLE_AI_FEATURES'
            or len(resources) != 1 or resources[0].identifier != platform_id
            or str((resources[0].metadata or {}).get('is_dry_run')).lower() != 'false'):
        raise AidpMemoryAssociationConflict('The native memory work request does not prove this enablement.')
    if request.status not in ('ACCEPTED', 'IN_PROGRESS', 'WAITING', 'SUCCEEDED'):
        raise AidpMemoryAssociationConflict('The accepted memory work request did not succeed.')
    return request.status == 'SUCCEEDED'


def verify_ready(client, sdk, intent, receipt, platform_etag, *, fresh_request=False):
    record, etag = receipt
    if record['status'] == 'accepted' and not fresh_request:
        raise AidpMemoryAssociationConflict('An unsealed memory request requires native reconciliation before resuming.')
    if not platform_etag or not verify_request(sdk, record, intent['platform_id']):
        return False
    if record['status'] == 'ready':
        if record['platform_etag'] != platform_etag:
            raise AidpMemoryAssociationConflict('Platform configuration changed after memory verification; reconcile it.')
    else:
        save_receipt(client, intent_key(intent['platform_id']),
            {**record, 'status': 'ready', 'platform_etag': platform_etag}, etag)
    return True
