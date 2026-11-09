"""Install project secrets without modifying shared OCI identity."""
import hashlib
import hmac
from uuid import UUID

from pydantic import SecretStr
from ai_market_intelligence.runtime import APIFY_CREDENTIAL_NAME
from ai_market_intelligence.runtime.config import LLM_CREDENTIAL_NAME, LLM_SECRET_KEY, validate_api_key


APIFY_SECRET_KEY = 'token'


def _project_credential(client):
    rows = [row for row in client._list('/credentials', phase='control')
            if row.get('displayName') == APIFY_CREDENTIAL_NAME]
    if len(rows) > 1:
        raise RuntimeError('Duplicate project credentials; reconcile before continuing')
    return rows[0] if rows else None


def _credential_reference(existing, expected_key, description):
    """Validate only the returned metadata; never include secret input in errors."""
    if not existing:
        raise RuntimeError('Project credential is not visible yet; keep the same operation')
    key = str(existing.get('key') or existing.get('id') or '')
    if not key or expected_key and key != expected_key:
        raise RuntimeError('Project credential identity changed')
    if not expected_key and (existing.get('credentialDescription') or existing.get('description')) != description:
        raise RuntimeError('Refusing to adopt an unrelated credential with the same name')
    if (existing.get('type') or existing.get('credentialType')) != 'SECRET_TOKEN':
        raise RuntimeError('Unexpected project credential type')
    if (existing.get('lifecycleState') or existing.get('lifeCycleState') or existing.get('state')) != 'ACTIVE':
        raise RuntimeError('Project credential is not active yet; keep the same operation')
    return {'credential_key': key, 'credential_name': APIFY_CREDENTIAL_NAME, 'secret_key': APIFY_SECRET_KEY}


def existing_project_reference(existing, expected_key=''):
    description = (existing or {}).get('credentialDescription') or (existing or {}).get('description') or ''
    if not description.startswith('AI Market Intelligence'):
        raise RuntimeError('The existing Apify credential has unrelated ownership; it will not be changed.')
    return _credential_reference(existing, expected_key or existing.get('key') or existing.get('id'), description)


def created_by_operation(existing, state):
    description = (existing or {}).get('credentialDescription') or (existing or {}).get('description')
    return bool(state.get('resources', {}).get('apify_create_requested') and description ==
                'AI Market Intelligence Apify connection; operation=' + state['operation_id'])


def ensure_apify_credential(client, token: SecretStr, operation_id: str, *, expected_key=''):
    """Caller persists operation_id before this write; retries reuse its receipt."""
    if str(UUID(operation_id)) != operation_id or not isinstance(token, SecretStr):
        raise ValueError('A stable operation and redacted secret value are required')
    value = token.get_secret_value()
    if not 20 <= len(value) <= 512 or any(ord(c) <= 32 for c in value):
        raise ValueError('Invalid Apify secret format')
    description = 'AI Market Intelligence Apify connection; operation=' + operation_id

    existing = _project_credential(client)
    if not existing:
        if expected_key:
            raise RuntimeError('The recorded project credential is missing')
        payload = {'displayName': APIFY_CREDENTIAL_NAME, 'credentialDescription': description,
                   'type': 'SECRET_TOKEN', 'credentialDetails': {'credentialType': 'SECRET_TOKEN',
                   'secretTokenPair': [{'secretKey': APIFY_SECRET_KEY, 'secretValue': value}]}}
        try:
            client._request('POST', '/credentials', payload=payload, phase='control')
        except Exception:
            # An uncertain create is reconciled by the same operation, never rotated.
            raise RuntimeError('Project credential creation is incomplete; reconcile the same operation') from None
        existing = _project_credential(client)
    return _credential_reference(existing, expected_key, description)


def llm_credential_reference(client, state):
    rows = [row for row in client._list('/credentials', phase='control')
            if row.get('displayName') == LLM_CREDENTIAL_NAME]
    expected = state['resources'].get('llm_credential_key')
    if len(rows) != 1 or not expected:
        raise RuntimeError('The recorded LLM credential must be uniquely visible.')
    row = rows[0]
    if ((row.get('key') or row.get('id')) != expected
            or (row.get('type') or row.get('credentialType')) != 'SECRET_TOKEN'
            or (row.get('lifecycleState') or row.get('lifeCycleState') or row.get('state')) != 'ACTIVE'
            or (row.get('credentialDescription') or row.get('description')) !=
               'AI Market Intelligence OpenAI connection; operation=' + state['operation_id']):
        raise RuntimeError('The recorded LLM credential identity or ownership changed.')
    return {'credential_key': expected, 'credential_name': LLM_CREDENTIAL_NAME, 'secret_key': LLM_SECRET_KEY}


def validate_llm_secret(state, secret):
    if secret is None:
        return None, None
    if not isinstance(secret, SecretStr):
        raise ValueError('A protected OpenAI API key is required.')
    value = validate_api_key(secret.get_secret_value())
    fingerprint = hashlib.sha256(value.encode()).hexdigest()
    resources = state['resources']
    recorded = resources.get('llm_secret_sha256')
    existing = resources.get('llm_credential_key') or resources.get('llm_create_requested')
    if existing and (not isinstance(recorded, str) or not hmac.compare_digest(recorded, fingerprint)):
        raise ValueError('The supplied OpenAI key does not match the recorded credential; implicit rotation is not allowed.')
    return value, fingerprint


def ensure_llm_credential(client, state, secret=None):
    """Create once under the owner's lock; preserve the credential on rollback."""
    value, fingerprint = validate_llm_secret(state, secret)
    if state['resources'].get('llm_credential_key'):
        return llm_credential_reference(client, state)
    rows = [row for row in client._list('/credentials', phase='control')
            if row.get('displayName') == LLM_CREDENTIAL_NAME]
    description = 'AI Market Intelligence OpenAI connection; operation=' + state['operation_id']
    if rows:
        if len(rows) != 1 or not state['resources'].get('llm_create_requested') or (
                rows[0].get('credentialDescription') or rows[0].get('description')) != description:
            raise RuntimeError('An unrelated LLM credential exists; it will not be adopted or rotated.')
    else:
        if state['resources'].get('llm_create_requested'):
            raise RuntimeError('LLM credential creation is uncertain; reconcile the same operation.')
        if value is None:
            raise ValueError('A protected LLM API key is required for its first installation.')
        state['resources']['llm_create_requested'] = True
        state['resources']['llm_secret_sha256'] = fingerprint
        client._save(state)
        client._request('POST', '/credentials', payload={'displayName': LLM_CREDENTIAL_NAME,
            'credentialDescription': description, 'type': 'SECRET_TOKEN', 'credentialDetails': {
                'credentialType': 'SECRET_TOKEN',
                'secretTokenPair': [{'secretKey': LLM_SECRET_KEY, 'secretValue': value}]}}, phase='control')
        rows = [row for row in client._list('/credentials', phase='control')
                if row.get('displayName') == LLM_CREDENTIAL_NAME]
    if len(rows) != 1:
        raise RuntimeError('LLM credential creation is not uniquely visible; preserve its intent.')
    state['resources']['llm_credential_key'] = rows[0].get('key') or rows[0].get('id')
    reference = llm_credential_reference(client, state)
    client._save(state)
    return reference
