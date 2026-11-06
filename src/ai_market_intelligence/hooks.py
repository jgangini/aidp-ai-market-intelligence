"""Deploy Studio schema-1 file contracts; native calls occur only on explicit invocation."""
import asyncio
import json
import os
import re
import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid5, NAMESPACE_URL
import oci
from pydantic import SecretStr
from .apify_preflight import check_apify
from .authorization import resolve_domain, verify_administrator
from .client import MarketAidpClient
from .errors import AidpProvisionConflict
from .lifecycle import install
from .settings import Settings


def _outputs(context):
    return {key: row['value'] if isinstance(row, dict) and 'value' in row else row
            for key, row in context.get('terraform_outputs', {}).items()}


def _settings(context, config_file, *, post_apply):
    fields = context['inputs']
    outputs = _outputs(context) if post_apply else {}
    def binding(name, default=''):
        return outputs.get(name, fields.get(name, default))
    compartment = context.get('compartment', {})
    if not isinstance(compartment, dict):
        raise ValueError('Resolve the preflight compartment from signed OCI metadata first')
    return Settings(oci_config_file=config_file, aidp_region=context['region'],
        aidp_platform_id=binding('ai_data_platform_ocid', fields.get('existing_ai_data_platform_ocid', '')),
        aidp_workspace_key=binding('workspace_key', fields.get('existing_workspace_key', '')),
        aidp_workspace_name=binding('workspace_name'),
        compartment_id=binding('compartment_id', context.get('compartment', {}).get('id', '')),
        objectstorage_namespace=binding('objectstorage_namespace'),
        artifacts_bucket_name=binding('artifacts_bucket_name'), agent_model_id=binding('agent_model_id'),
        identity_domain_id=binding('identity_domain_id'), admin_user_ocid=binding('admin_user_ocid'))


def _token(secrets):
    value = secrets.get('inputs', {}).get('apify_token')
    if not isinstance(value, str):
        raise ValueError('Protected Apify input is required')
    return SecretStr(value)


def _platform(client, *, newly_owned=False, deployment_binding=''):
    platform = oci.ai_data_platform.AiDataPlatformClient(
        client._oci_config, signer=client.signer).get_ai_data_platform(client.settings.aidp_platform_id).data
    if platform.id != client.settings.aidp_platform_id or platform.compartment_id != client.settings.compartment_id:
        raise AidpProvisionConflict('Selected AIDP does not match the exact platform/compartment binding.')
    if platform.lifecycle_state != 'ACTIVE':
        raise AidpProvisionConflict('Selected AIDP is not ACTIVE. Preserve the deployment and resume after readiness.')
    if newly_owned and (platform.freeform_tags or {}).get('deployment_binding') != deployment_binding:
        raise AidpProvisionConflict('New-platform ownership differs from this exact deployment. No administrator was assigned.')


def preflight(context, secrets, config_file):
    fields = context['inputs']
    recovery = fields.get('lifecycle_action') == 'rollback'
    if fields['aidp_mode'] not in ('new', 'existing'):
        raise ValueError('Invalid deployment mode')
    config = oci.config.from_file(config_file, 'DEFAULT')
    signer = oci.signer.Signer(tenancy=config['tenancy'], user=config['user'], fingerprint=config['fingerprint'],
        private_key_file_location=config['key_file'], pass_phrase=config.get('pass_phrase'))
    # Domain endpoint is resolved from signed control-plane metadata, never from an input URL.
    identity_context = SimpleNamespace(_oci_config=config, signer=signer,
        settings=SimpleNamespace(identity_domain_id=fields['identity_domain_id'], admin_user_ocid=fields['admin_user_ocid']))
    domain_url = resolve_domain(identity_context)
    verify_administrator(identity_context, roles_required=False)
    iam = oci.identity.IdentityClient(config, signer=signer)
    subscriptions = iam.list_region_subscriptions(config['tenancy']).data
    home = [row.region_name for row in subscriptions if row.is_home_region]
    if len(home) != 1:
        raise AidpProvisionConflict('Unable to verify the tenancy home region.')
    storage = oci.object_storage.ObjectStorageClient({**config, 'region': context['region']}, signer=signer)
    namespace = storage.get_namespace().data
    if not namespace or fields.get('objectstorage_namespace', namespace) != namespace:
        raise AidpProvisionConflict('Object Storage namespace differs from the operator tenancy.')
    if fields['aidp_mode'] == 'existing':
        platform = oci.ai_data_platform.AiDataPlatformClient({**config, 'region': context['region']}, signer=signer).get_ai_data_platform(
            fields['existing_ai_data_platform_ocid']).data
        if context.get('compartment_mode') != 'existing':
            raise AidpProvisionConflict('Existing AIDP requires selecting its existing compartment; no platform will be moved.')
        selected = context['compartment']
        compartment = iam.get_compartment(platform.compartment_id).data
        if compartment.lifecycle_state != 'ACTIVE' or selected not in (compartment.id, compartment.name):
            raise AidpProvisionConflict('The selected compartment differs from the existing platform compartment.')
        settings = _settings({**context, 'compartment': {'id': compartment.id},
            'inputs': {**fields, 'objectstorage_namespace': namespace}}, config_file, post_apply=False)
        client = MarketAidpClient(settings)
        try:
            _platform(client)
            client._workspace()
            verify_administrator(client)
        finally:
            asyncio.run(client.close())
    elif fields.get('existing_ai_data_platform_ocid') or fields.get('existing_workspace_key'):
        raise ValueError('New platform cannot carry an existing destination selection')
    if fields['artifacts_bucket_mode'] == 'existing':
        storage.head_bucket(namespace, fields['artifacts_bucket_name'])
    events = [{'name': 'Identity', 'status': 'passed',
               'message': 'Active selected administrator verified; new AIDP roles are assigned and revalidated after bootstrap.'}]
    if recovery:
        events.append({'name': 'Recovery', 'status': 'warning',
            'message': 'Rollback restores recorded paused artifacts only; no Apify or model call is required or started.'})
        return {'inputs': {'home_region': home[0], 'identity_domain_url': domain_url,
                           'objectstorage_namespace': namespace}, 'events': events}
    model_config = {**config, 'region': context['region']}
    model = oci.generative_ai.GenerativeAiClient(model_config, signer=signer).get_model(fields['agent_model_id']).data
    if model.lifecycle_state != 'ACTIVE':
        raise AidpProvisionConflict('Selected OCI model is not ACTIVE.')
    apify = check_apify(_token(secrets))
    events.append({'name': 'Apify account', 'status': 'passed' if apify['account']['status'] == 'authenticated' else 'failed',
                   'message': apify['account'].get('message', 'Authenticated account read verified.')})
    for actor in apify['actors']:
        if actor['status'] == 'suspended':
            events.append({'name': 'Apify ' + actor['profile'], 'status': 'warning',
                'message': 'Profile remains suspended; no actor access or execution was verified for it.'})
            continue
        passed = actor['status'] in ('visible', 'suspended')
        events.append({'name': 'Apify ' + actor['profile'], 'status': 'passed' if passed else 'failed',
            'message': ('Actor/build visibility checked; execution permission and entitlement not verified.' if passed
                        else actor.get('message', 'Required actor/build read failed; verify access and pinned build.'))})
    events.append({'name': 'Apify execution', 'status': 'warning',
                   'message': 'Execution permission/entitlement not verified without starting runs. Research stays paused.'})
    return {'inputs': {'home_region': home[0], 'identity_domain_url': domain_url,
                       'objectstorage_namespace': namespace}, 'events': events}


def operation_id(deployment_id):
    if not isinstance(deployment_id, str) or not re.fullmatch(r'dep_[0-9a-f]{16}', deployment_id):
        raise ValueError('The durable Deploy Studio deployment ID is required; a preflight ID cannot start installation')
    return str(uuid5(NAMESPACE_URL, 'https://github.com/jgangini/aidp-ai_market-intelligence/deployments/' + deployment_id))


def maintenance_operation_id(context, action, package_sha256):
    operation_id(context['deployment_id'])
    if action not in ('update', 'redeploy'):
        raise ValueError('Rollback requires the exact recorded maintenance operation')
    requested = context['inputs'].get('operation_request_id', '')
    if requested:
        if str(UUID(requested)) != requested:
            raise ValueError('A canonical operation UUID is required')
        return requested
    return str(uuid5(NAMESPACE_URL, 'https://github.com/jgangini/aidp-ai_market-intelligence/deployments/'
        + context['deployment_id'] + '/' + action + '/' + package_sha256))


def post_apply(context, secrets, config_file):
    outputs = _outputs(context)
    operation = operation_id(context['deployment_id'])
    action = context['inputs'].get('lifecycle_action', 'install')
    if action not in ('install', 'update', 'redeploy', 'rollback'):
        raise ValueError('Unknown lifecycle action')
    if (outputs.get('region') != context['region']
            or outputs.get('owned_ai_data_platform') is not (context['inputs']['aidp_mode'] == 'new')
            or outputs.get('owned_artifacts_bucket') is not (context['inputs']['artifacts_bucket_mode'] == 'new')):
        raise AidpProvisionConflict('Terraform destination or ownership mode changed before installation.')
    for name in ('admin_user_ocid', 'identity_domain_id', 'agent_model_id', 'artifacts_bucket_name'):
        if outputs.get(name) != context['inputs'].get(name):
            raise AidpProvisionConflict('Terraform selected bindings changed before installation.')
    settings = _settings(context, config_file, post_apply=True)
    client = MarketAidpClient(settings)
    try:
        suffix = ''.join(char for char in context['deployment_id'].lower() if char.isalnum())[-8:]
        expected_binding = hashlib.sha256(':'.join([client._oci_config['tenancy'], context['region'], settings.compartment_id,
            suffix, settings.identity_domain_id, settings.admin_user_ocid]).encode()).hexdigest()
        if outputs.get('deployment_binding') != expected_binding:
            raise AidpProvisionConflict('Terraform ownership binding differs from this exact deployment and selected identities.')
        new = outputs.get('owned_ai_data_platform') is True
        _platform(client, newly_owned=new, deployment_binding=expected_binding)
        bucket = client.object_storage.get_bucket(settings.objectstorage_namespace, settings.artifacts_bucket_name).data
        if bucket.name != settings.artifacts_bucket_name or bucket.namespace != settings.objectstorage_namespace:
            raise AidpProvisionConflict('Artifacts bucket identity differs from the deployment.')
        if outputs.get('owned_artifacts_bucket') is True and (
                bucket.compartment_id != settings.compartment_id or (bucket.freeform_tags or {}).get('deployment_binding') != expected_binding):
            raise AidpProvisionConflict('New artifacts bucket is not owned by this deployment.')
        if action != 'rollback':
            apify = check_apify(_token(secrets))
            if not apify['reads_verified']:
                raise AidpProvisionConflict('Apify account/actor/build reads failed; no installation was started.')
        # The default workspace of a new platform is matched exactly once, then bound by key.
        workspace = client._workspace()
        client.settings = settings.model_copy(update={'aidp_workspace_key': workspace['key']})
        session = context['inputs'].get('test_session_id') or None
        if action == 'install':
            status = install(client, operation, token=_token(secrets), new_platform=new, test_session_id=session)
        else:
            from .maintenance import maintain
            from .package import load_package
            if action == 'rollback':
                saved = client._read_installation(workspace['key'])
                recorded = (saved or {}).get('maintenance', {}).get('operation_id')
                operation = context['inputs'].get('operation_request_id') or recorded
                if not operation or operation != recorded:
                    raise AidpProvisionConflict('Rollback must reference the recorded maintenance operation.')
            else:
                operation = maintenance_operation_id(context, action,
                    load_package('ai_market_intelligence', require_available=False).pack_sha256)
            status = maintain(client, action, operation, test_session_id=session)
        recovered_failure = status['status'] == 'rolled_back' and action != 'rollback'
        return {'outputs': {'installation_status': status['status'], 'installation_phase': status['phase'],
            'operation_id': status['operation_id'], 'deployment_surface': 'TEST', 'research_enabled': False,
            'apify_execution_permission': 'not_verified', 'workspace_key': workspace['key'], 'maintenance_action': action},
            'events': [{'level': 'error' if recovered_failure else 'success',
                'message': ('Candidate update failed; the previous TEST candidate was restored and verified.' if recovered_failure
                            else 'Market lifecycle operation verified in TEST with research paused.')}]}
    finally:
        asyncio.run(client.close())


def run_hook(kind):
    result_name = 'DEPLOY_STUDIO_RESULT' if kind == 'preflight' else 'DEPLOY_STUDIO_OUTPUT'
    destination = Path(os.environ[result_name])
    try:
        context = json.loads(Path(os.environ['DEPLOY_STUDIO_CONTEXT']).read_text(encoding='utf-8'))
        secrets = json.loads(Path(os.environ['DEPLOY_STUDIO_SECRETS']).read_text(encoding='utf-8'))
        if context.get('schema_version') != 1 or context.get('project_id') != 'aidp-ai-market-intelligence':
            raise ValueError('Unsupported hook context')
        action = preflight if kind == 'preflight' else post_apply
        result = action(context, secrets, os.environ['DEPLOY_STUDIO_OCI_CONFIG'])
        destination.write_text(json.dumps(result), encoding='utf-8')
        return 1 if any(event.get('level') == 'error' for event in result.get('events', [])) else 0
    except Exception:
        # SDK and validation errors can contain request/credential bodies. Fail closed without echo.
        if kind == 'preflight':
            result = {'inputs': {}, 'events': [{'name': 'Market preflight', 'status': 'failed',
                'message': 'Identity, destination or service access could not be verified. Preserve the operation; no paid runs started.'}]}
        else:
            result = {'outputs': {}, 'events': [{'level': 'error',
                'message': 'Market installation incomplete. Preserve this deployment and resume the same operation after reconciliation.'}]}
        destination.write_text(json.dumps(result), encoding='utf-8')
        return 1
