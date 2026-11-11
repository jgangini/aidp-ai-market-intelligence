"""Explicit nonsecret TEST research targets bound to an existing installation."""
import copy
import hashlib
import json

from .client import MODULE_ID, validated_runtime_config
from .errors import AidpProvisionConflict
from .package import load_package
from .transport import GOVERNANCE_CREDENTIAL_NAME
from .runtime.config import OPENAI_MODE, LLM_CREDENTIAL_NAME, OPENAI_ENDPOINT, llm_auth_mode
from .installation.credentials import llm_credential_reference


def read_research_binding(path):
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('Research binding exceeds its declared bound')
    config = validated_runtime_config(json.loads(raw))
    if not config['research_enabled']:
        raise ValueError('Explicit research activation requires a complete active binding')
    return config


def validate_research_target(client, state, config, pack, *, preserve_installed_model=False):
    config = validated_runtime_config(config)
    services = config.get('services', {})
    identity = client._oci_config
    checksum = hashlib.sha256(json.dumps([identity[key] for key in ('tenancy', 'user', 'fingerprint')],
                                        separators=(',', ':')).encode()).hexdigest()
    installed = client._runtime_config(state).get('services', {})
    model_id = installed['model_id'] if preserve_installed_model else client.settings.agent_model_id
    expected = {'credential_name': GOVERNANCE_CREDENTIAL_NAME, 'identity_sha256': checksum,
                'tenant_id': identity['tenancy'], 'generation_id': state['operation_id'],
                'region': client.settings.aidp_region, 'model_id': model_id,
                'compartment_id': client.settings.compartment_id,
                'namespace': client.settings.objectstorage_namespace, 'bucket': client.settings.artifacts_bucket_name,
                'candidate_revision': pack}
    if not config['research_enabled'] or any(services.get(key) != value for key, value in expected.items()):
        raise AidpProvisionConflict('Research binding differs from this installation, identity, model or storage.')
    mode = llm_auth_mode(services)
    expected_mode = llm_auth_mode(installed) if preserve_installed_model else getattr(client.settings, 'llm_auth_mode', 'oci_iam')
    if mode != expected_mode:
        raise AidpProvisionConflict('Research binding differs from the selected LLM authentication mode.')
    endpoint = (OPENAI_ENDPOINT if mode == OPENAI_MODE else
                'https://inference.generativeai.' + services['region'] + '.oci.oraclecloud.com')
    if services['model_endpoint'].rstrip('/') != endpoint:
        raise AidpProvisionConflict('Research model endpoint differs from the selected provider binding.')
    if mode == OPENAI_MODE:
        llm_credential_reference(client, state)
    observed = state['resources'].get('test_verified', {}).get('versions')
    if not observed or services['runtime_profile'] != observed:
        raise AidpProvisionConflict('Research requires the exact runtime profile observed in this installation TEST.')
    return config


def target_configuration(client, state, requested=None, *, change_model=False):
    pack = load_package(MODULE_ID, require_available=False).pack_sha256
    if requested is not None:
        return validate_research_target(client, state, requested, pack)
    current = client._runtime_config(state)
    if not current['research_enabled']:
        return client._selected_runtime_config() if change_model else current
    target = copy.deepcopy(current)
    services = target['services']
    services['candidate_revision'] = pack
    if not change_model:
        return validate_research_target(client, state, target, pack, preserve_installed_model=True)
    services.update(candidate_revision=pack, model_id=client.settings.agent_model_id)
    if getattr(client.settings, 'llm_auth_mode', 'oci_iam') == OPENAI_MODE:
        target['schema_version'] = 2
        services.update(llm_auth_mode=OPENAI_MODE, llm_credential_name=LLM_CREDENTIAL_NAME,
                        model_endpoint=OPENAI_ENDPOINT)
    else:
        target['schema_version'] = 1
        services.pop('llm_auth_mode', None)
        services.pop('llm_credential_name', None)
        services['model_endpoint'] = 'https://inference.generativeai.' + services['region'] + '.oci.oraclecloud.com'
    return validate_research_target(client, state, target, pack)
