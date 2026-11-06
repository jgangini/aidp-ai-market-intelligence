"""Native memory control-plane contract; all clients are local fakes."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import oci
import pytest

from ai_market_intelligence import memory
from ai_market_intelligence.errors import AidpProvisionConflict, AidpProvisionPending

PLATFORM = 'ocid1.aidataplatform.oc1..fixture'
DATABASE = 'ocid1.autonomousdatabase.oc1..fixture'
OPERATION = '00000000-0000-4000-8000-000000000001'
PASSWORD = 'FixtureMemory123'


@pytest.fixture
def native(monkeypatch):
    value = {'id': PLATFORM, 'lifecycleState': 'ACTIVE', 'aiFeatureStatus': 'DISABLED'}
    database = NS(id=DATABASE, lifecycle_state='AVAILABLE', db_version='26ai', db_workload='DW',
                  compartment_id='compartment', freeform_tags={'deployment_binding': 'binding'})
    reader = Mock()
    reader.get_ai_data_platform.side_effect = lambda *_: NS(data=dict(value), headers={'etag': 'platform-etag'})
    writer = Mock()
    def enable(*args, **kwargs):
        value.update(aiFeatureStatus='READY', vectorDbId=DATABASE)
    writer.enable_ai_feature.side_effect = enable
    factory = Mock(side_effect=lambda *args, **kwargs: reader if kwargs.get('skip_deserialization') else writer)
    monkeypatch.setattr(memory.oci.ai_data_platform, 'AiDataPlatformClient', factory)
    database_client = Mock()
    database_client.get_autonomous_database.return_value.data = database
    monkeypatch.setattr(memory.oci.database, 'DatabaseClient', Mock(return_value=database_client))
    client = NS(_oci_config={'region': 'wrong-region'}, signer='operator', object_storage=Mock(),
                settings=NS(aidp_platform_id=PLATFORM, compartment_id='compartment',
                            objectstorage_namespace='namespace', artifacts_bucket_name='artifacts'))
    client.object_storage.put_object.return_value = NS(headers={'etag': 'intent-etag'})
    context = {'region': 'us-chicago-1', 'inputs': {'aidp_mode': 'existing',
        'existing_ai_data_platform_ocid': PLATFORM, 'autonomous_database_mode': 'existing',
        'existing_autonomous_database_ocid': DATABASE}}
    outputs = {'autonomous_database_id': DATABASE, 'owned_autonomous_database': False,
               'deployment_binding': 'binding'}
    secrets = {'inputs': {'autonomous_database_admin_password': PASSWORD}}
    return NS(value=value, database=database, factory=factory, reader=reader, writer=writer,
              database_client=database_client, client=client, context=context, outputs=outputs, secrets=secrets)


def test_enable_uses_protected_password_etag_and_durable_intent(native):
    assert memory.ensure_memory(native.client, native.context, native.secrets, native.outputs, OPERATION) == DATABASE
    call = native.writer.enable_ai_feature.call_args
    assert call.args[0] == PLATFORM
    assert call.args[1].vector_db_id == DATABASE
    assert call.args[1].vector_db_admin_cred == PASSWORD
    assert call.kwargs['if_match'] == 'platform-etag'
    assert isinstance(call.kwargs['retry_strategy'], oci.retry.NoneRetryStrategy)
    put = native.client.object_storage.put_object.call_args
    assert put.kwargs['if_none_match'] == '*'
    intent = json.loads(put.args[3])
    assert intent['operation_id'] == OPERATION and intent['database_id'] == DATABASE
    assert PASSWORD not in put.args[3].decode()
    assert native.factory.call_args_list[0].kwargs['skip_deserialization'] is True
    assert native.factory.call_args_list[0].args[0]['region'] == 'us-chicago-1'


def test_resume_ready_exact_association_does_not_need_password_or_write(native):
    native.value.update(aiFeatureStatus='READY', vectorDbId=DATABASE)
    assert memory.ensure_memory(native.client, native.context, {}, native.outputs, OPERATION) == DATABASE
    native.writer.enable_ai_feature.assert_not_called()
    native.client.object_storage.put_object.assert_not_called()


@pytest.mark.parametrize('association', [None, 'ocid1.autonomousdatabase.oc1..foreign'])
def test_ready_or_conflict_is_not_proof_of_selected_database(native, association):
    native.value.update(aiFeatureStatus='READY', vectorDbId=association)
    with pytest.raises(AidpProvisionConflict, match='association'):
        memory.ensure_memory(native.client, native.context, native.secrets, native.outputs, OPERATION)
    native.writer.enable_ai_feature.assert_not_called()


@pytest.mark.parametrize('status', ['ENABLING', 'FAILED', None])
def test_pending_failed_and_unknown_states_never_resubmit(native, status):
    native.value.update(aiFeatureStatus=status, vectorDbId=DATABASE)
    with pytest.raises((AidpProvisionPending, AidpProvisionConflict)):
        memory.ensure_memory(native.client, native.context, native.secrets, native.outputs, OPERATION)
    native.writer.enable_ai_feature.assert_not_called()


def test_existing_intent_blocks_duplicate_enablement(native):
    native.client.object_storage.put_object.side_effect = oci.exceptions.ServiceError(412, 'PreconditionFailed', {}, 'fixture')
    native.client.object_storage.get_object.return_value = NS(headers={'etag':'intent-etag'}, data=NS(content=json.dumps({
        'schema_version':1,'platform_id':PLATFORM,'database_id':DATABASE,'operation_id':OPERATION,
        'deployment_binding':'binding','status':'submitted'}).encode()))
    with pytest.raises(AidpProvisionConflict, match='intent already exists'):
        memory.ensure_memory(native.client, native.context, native.secrets, native.outputs, OPERATION)
    native.writer.enable_ai_feature.assert_not_called()


def test_confirmed_rejection_can_retry_same_operation_with_corrected_secret(native):
    def reject(*args, **kwargs):
        raise oci.exceptions.ServiceError(400, 'InvalidParameter', {}, 'password rejected')
    native.writer.enable_ai_feature.side_effect = reject
    with pytest.raises(oci.exceptions.ServiceError):
        memory.ensure_memory(native.client, native.context, native.secrets, native.outputs, OPERATION)
    rejected = native.client.object_storage.put_object.call_args.args[3]
    rejected_token = native.writer.enable_ai_feature.call_args.kwargs['opc_retry_token']
    assert json.loads(rejected)['status'] == 'rejected'
    native.client.object_storage.put_object.side_effect = [oci.exceptions.ServiceError(412, 'PreconditionFailed', {}, 'fixture'),
                                                         NS(headers={'etag':'intent-next'})]
    native.client.object_storage.get_object.return_value = NS(headers={'etag':'intent-etag'}, data=NS(content=rejected))
    def succeed(*args, **kwargs):
        native.value.update(aiFeatureStatus='READY', vectorDbId=DATABASE)
    native.writer.enable_ai_feature.side_effect = succeed
    corrected = {'inputs': {'autonomous_database_admin_password':'CorrectedMemory456'}}
    assert memory.ensure_memory(native.client, native.context, corrected, native.outputs, OPERATION) == DATABASE
    assert native.client.object_storage.put_object.call_args.kwargs['if_match'] == 'intent-etag'
    assert native.writer.enable_ai_feature.call_args.args[1].vector_db_admin_cred == 'CorrectedMemory456'
    assert native.writer.enable_ai_feature.call_args.kwargs['opc_retry_token'] != rejected_token


def test_http_409_requires_fresh_exact_ready_readback(native):
    native.writer.enable_ai_feature.side_effect = oci.exceptions.ServiceError(409, 'Conflict', {}, 'fixture')
    with pytest.raises(AidpProvisionPending):
        memory.ensure_memory(native.client, native.context, native.secrets, native.outputs, OPERATION)


def test_enablement_timeout_preserves_intent_and_does_not_retry(native):
    native.writer.enable_ai_feature.side_effect = TimeoutError('uncertain request')
    with pytest.raises(TimeoutError):
        memory.ensure_memory(native.client, native.context, native.secrets, native.outputs, OPERATION)
    native.client.object_storage.put_object.assert_called_once()
    native.writer.enable_ai_feature.assert_called_once()


@pytest.mark.parametrize('field,value', [('id','foreign'), ('db_version','19c'), ('db_workload','OLTP'), ('lifecycle_state','STOPPED')])
def test_invalid_database_is_rejected_before_enable(native, field, value):
    setattr(native.database, field, value)
    with pytest.raises(AidpProvisionConflict):
        memory.ensure_memory(native.client, native.context, native.secrets, native.outputs, OPERATION)
    native.writer.enable_ai_feature.assert_not_called()


@pytest.mark.parametrize('field', ['deployment_binding', 'compartment_id'])
def test_new_database_requires_exact_owner(native, field):
    native.context['inputs'].update(autonomous_database_mode='new', existing_autonomous_database_ocid='',
                                     autonomous_database_version='26ai', autonomous_database_workload='DW')
    native.outputs['owned_autonomous_database'] = True
    if field == 'deployment_binding': native.database.freeform_tags[field] = 'foreign'
    else: native.database.compartment_id = 'foreign'
    with pytest.raises(AidpProvisionConflict, match='belong'):
        memory.ensure_memory(native.client, native.context, native.secrets, native.outputs, OPERATION)
    native.writer.enable_ai_feature.assert_not_called()


def test_preflight_reuses_exact_ready_memory_without_admin_secret(native):
    native.value.update(aiFeatureStatus='READY', vectorDbId=DATABASE)
    result = memory.preflight_memory(native.context, {}, {}, 'operator')
    assert result['status'] == 'passed'
    native.writer.enable_ai_feature.assert_not_called()


def test_preflight_rejects_new_database_on_already_configured_platform(native):
    native.value.update(aiFeatureStatus='READY', vectorDbId=DATABASE)
    native.context['inputs'].update(autonomous_database_mode='new', existing_autonomous_database_ocid='',
                                     autonomous_database_version='26ai', autonomous_database_workload='DW')
    with pytest.raises(AidpProvisionConflict, match='association'):
        memory.preflight_memory(native.context, native.secrets, {}, 'operator')
    native.database_client.get_autonomous_database.assert_not_called()


@pytest.mark.parametrize('password', ['', 'short', 'OnlyLettersNoDigit', 'FixtureAdmin123'])
def test_disabled_platform_needs_valid_protected_admin_password(native, password):
    with pytest.raises(AidpProvisionConflict, match='protected ADMIN'):
        memory.preflight_memory(native.context, {'inputs': {'autonomous_database_admin_password':password}}, {}, 'operator')
    native.writer.enable_ai_feature.assert_not_called()


@pytest.mark.parametrize('action', ['update', 'redeploy', 'rollback'])
def test_maintenance_cannot_enable_platform_memory(native, action):
    native.context['inputs']['lifecycle_action'] = action
    with pytest.raises(AidpProvisionConflict, match='Maintenance'):
        memory.ensure_memory(native.client, native.context, native.secrets, native.outputs, OPERATION)
    native.writer.enable_ai_feature.assert_not_called()
