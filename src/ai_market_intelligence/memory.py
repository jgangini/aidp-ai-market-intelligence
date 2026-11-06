"""Bind Agent Flow to Oracle-managed memory without replacing or clearing it."""
import hashlib
import json
import re
from uuid import NAMESPACE_URL, uuid5

import oci
from pydantic import SecretStr

from .errors import AidpProvisionConflict, AidpProvisionPending


def _password(secrets, *, required):
    value = secrets.get('inputs', {}).get('autonomous_database_admin_password', '')
    if not value and not required:
        return None
    if (not isinstance(value, str) or not 12 <= len(value) <= 30
            or not re.search('[A-Z]', value) or not re.search('[a-z]', value)
            or not re.search('[0-9]', value) or 'admin' in value.lower()
            or '"' in value or "'" in value):
        raise AidpProvisionConflict('A valid protected ADMIN password is required to create storage or enable AI.')
    return SecretStr(value)


def platform_details(config, signer, platform_id):
    # The typed SDK 2.189 model discards vectorDbId. Preserve signed JSON from the
    # same supported GET; an absent association must not become a guessed binding.
    sdk = oci.ai_data_platform.AiDataPlatformClient(config, signer=signer, skip_deserialization=True)
    response = sdk.get_ai_data_platform(platform_id)
    value = response.data
    if (not isinstance(value, dict) or value.get('id') != platform_id
            or value.get('lifecycleState') != 'ACTIVE'):
        raise AidpProvisionConflict('The exact ACTIVE AIDP memory configuration could not be read.')
    return value, next((v for k, v in response.headers.items() if k.lower() == 'etag'), None)


def _association(platform, database_id):
    status = platform.get('aiFeatureStatus')
    if status in ('READY', 'ENABLING'):
        if not platform.get('vectorDbId') or platform['vectorDbId'] != database_id:
            raise AidpProvisionConflict('AIDP memory association is missing or differs from the selected database; preserve it.')
    elif status != 'DISABLED':
        raise AidpProvisionConflict('AIDP AI feature status is unavailable or failed; reconcile it before installation.')
    return status


def _database(config, signer, database_id):
    database = oci.database.DatabaseClient(config, signer=signer).get_autonomous_database(database_id).data
    if (database.id != database_id or database.lifecycle_state != 'AVAILABLE'
            or database.db_version != '26ai' or database.db_workload != 'DW'):
        raise AidpProvisionConflict('Memory requires the exact AVAILABLE Autonomous AI Lakehouse 26ai DW instance.')
    return database


def _etag(response):
    value = next((v for k, v in response.headers.items() if k.lower() == 'etag'), None)
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise AidpProvisionConflict('A memory intent cannot be changed without its exact ETag.')
    return value


def _reserve_intent(client, key, intent):
    storage = client.object_storage
    namespace, bucket = client.settings.objectstorage_namespace, client.settings.artifacts_bucket_name
    body = json.dumps({**intent, 'status': 'submitted', 'attempt': 1}, sort_keys=True, separators=(',', ':')).encode()
    try:
        return _etag(storage.put_object(namespace, bucket, key, body, if_none_match='*', content_type='application/json')), 1
    except oci.exceptions.ServiceError as exc:
        if exc.status != 412:
            raise
    saved = storage.get_object(namespace, bucket, key)
    content = saved.data.content
    if not isinstance(content, bytes) or len(content) > 4096:
        raise AidpProvisionConflict('The existing memory intent cannot be safely read.')
    try:
        previous = json.loads(content)
    except ValueError:
        raise AidpProvisionConflict('The existing memory intent requires reconciliation.') from None
    if (not isinstance(previous, dict) or set(previous) != {*intent, 'status', 'rejection_status', 'attempt'}
            or any(previous.get(k) != v for k, v in intent.items())
            or previous.get('status') != 'rejected' or previous.get('rejection_status') not in (400, 401, 403, 404, 412)
            or type(previous.get('attempt')) is not int or not 1 <= previous['attempt'] < 1000):
        raise AidpProvisionConflict('A memory enablement intent already exists. Reconcile it; no duplicate request was sent.')
    attempt = previous['attempt'] + 1
    body = json.dumps({**intent, 'status': 'submitted', 'attempt': attempt}, sort_keys=True, separators=(',', ':')).encode()
    return _etag(storage.put_object(namespace, bucket, key, body, if_match=_etag(saved), content_type='application/json')), attempt


def preflight_memory(context, secrets, config, signer):
    fields = context['inputs']
    mode = fields.get('autonomous_database_mode')
    if mode not in ('new', 'existing'):
        raise AidpProvisionConflict('Choose new or existing Autonomous memory storage.')
    if fields.get('lifecycle_action', 'install') != 'install' and mode != 'existing':
        raise AidpProvisionConflict('Maintenance cannot create or replace memory storage.')
    database_id = fields.get('existing_autonomous_database_ocid', '')
    if mode == 'existing':
        if not isinstance(database_id, str) or not re.fullmatch(r'ocid1\.autonomousdatabase\.[^\s]+', database_id):
            raise AidpProvisionConflict('Select the exact existing Autonomous database.')
        _database(config, signer, database_id)
    elif database_id:
        raise AidpProvisionConflict('New memory storage cannot carry an existing database selection.')
    elif fields.get('autonomous_database_version') != '26ai' or fields.get('autonomous_database_workload') != 'DW':
        raise AidpProvisionConflict('New Agent Flow memory requires the 26ai DW profile.')
    status = 'DISABLED'
    if fields['aidp_mode'] == 'existing':
        platform, _ = platform_details(config, signer, fields['existing_ai_data_platform_ocid'])
        status = _association(platform, database_id)
    _password(secrets, required=mode == 'new' or status == 'DISABLED')
    return {'name': 'Agent Flow memory', 'status': 'passed',
            'message': ('Existing native memory association verified; no replacement or SQL wallet required.'
                        if status == 'READY' else 'Memory destination validated; native AI enablement is checked after apply.')}


def ensure_memory(client, context, secrets, outputs, operation):
    fields = context['inputs']
    database_id = outputs.get('autonomous_database_id', '')
    owned = fields['autonomous_database_mode'] == 'new'
    if (not isinstance(database_id, str) or not re.fullmatch(r'ocid1\.autonomousdatabase\.[^\s]+', database_id)
            or outputs.get('owned_autonomous_database') is not owned
            or (not owned and database_id != fields.get('existing_autonomous_database_ocid'))):
        raise AidpProvisionConflict('Terraform memory identity or ownership changed before installation.')
    config = {**client._oci_config, 'region': context['region']}
    database = _database(config, client.signer, database_id)
    if owned and (database.compartment_id != client.settings.compartment_id
                  or (database.freeform_tags or {}).get('deployment_binding') != outputs['deployment_binding']):
        raise AidpProvisionConflict('New memory database does not belong to this deployment.')
    platform_id = client.settings.aidp_platform_id
    platform, etag = platform_details(config, client.signer, platform_id)
    status = _association(platform, database_id)
    if status == 'READY':
        return database_id
    if status == 'ENABLING':
        raise AidpProvisionPending('AI memory enablement is still running; resume the same deployment.', 'memory')
    if fields.get('lifecycle_action', 'install') != 'install':
        raise AidpProvisionConflict('Maintenance cannot enable or change platform memory.')
    if not etag:
        raise AidpProvisionConflict('AIDP memory enablement requires the current platform ETag.')
    credential = _password(secrets, required=True)
    key = 'control/ai_market_intelligence/memory/' + hashlib.sha256(platform_id.encode()).hexdigest() + '.json'
    intent = {'schema_version': 1, 'platform_id': platform_id, 'database_id': database_id,
              'operation_id': operation, 'deployment_binding': outputs['deployment_binding']}
    # Record intent before a call that may time out after being accepted. A lost
    # response is reconciled through READY + exact association, never another POST.
    intent_etag, attempt = _reserve_intent(client, key, intent)
    sdk = oci.ai_data_platform.AiDataPlatformClient(config, signer=client.signer)
    try:
        sdk.enable_ai_feature(platform_id, oci.ai_data_platform.models.EnableAiFeatureDetails(
            vector_db_id=database_id, vector_db_admin_cred=credential.get_secret_value()),
            if_match=etag, opc_retry_token=str(uuid5(NAMESPACE_URL, 'aidp-market-memory:' + operation + ':' + database_id + ':' + str(attempt))),
            retry_strategy=oci.retry.NoneRetryStrategy())
    except oci.exceptions.ServiceError as exc:
        if exc.status in (400, 401, 403, 404, 412):
            rejected = {**intent, 'status': 'rejected', 'rejection_status': exc.status, 'attempt': attempt}
            client.object_storage.put_object(client.settings.objectstorage_namespace,
                client.settings.artifacts_bucket_name, key,
                json.dumps(rejected, sort_keys=True, separators=(',', ':')).encode(),
                if_match=intent_etag, content_type='application/json')
            raise
        if exc.status != 409:
            raise
    platform, _ = platform_details(config, client.signer, platform_id)
    if _association(platform, database_id) != 'READY':
        raise AidpProvisionPending('Memory enablement has not reached verified READY; preserve the deployment and resume.', 'memory')
    return database_id
