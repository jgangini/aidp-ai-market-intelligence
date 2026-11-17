"""Active TEST targets reuse the existing maintenance transaction and public contract."""
import copy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from ai_market_intelligence import maintenance as work, research_activation as activation
from ai_market_intelligence.client import MarketAidpClient, market_probe_receipt, public_status
from ai_market_intelligence.errors import AidpProvisionConflict, AidpProvisionError, AidpProvisionPending
from ai_market_intelligence.runtime.runtime_versions import RUNTIME_DISTRIBUTIONS, TARGET_GRAPH_PACKAGES
from test_maintenance import client, run, OP, OTHER, INSTALL, AGENT_ROOT, OLD, encoded, assets_for, assert_resource_identity_preserved
from test_cli_maintenance import command
from pydantic import SecretStr


PACK = 'a' * 64
PROFILE = {'python': '3.11.13', 'packages': {
    name: TARGET_GRAPH_PACKAGES.get(name, 'fixture-version') for name in RUNTIME_DISTRIBUTIONS}}


@pytest.fixture
def active_target(client, monkeypatch):
    client._oci_config = {'tenancy': 'tenancy', 'user': 'operator', 'fingerprint': 'fingerprint'}
    client.settings.objectstorage_namespace = 'namespace'
    client.settings.artifacts_bucket_name = 'artifacts'
    client.saved['resources']['test_verified']['versions'] = copy.deepcopy(PROFILE)
    monkeypatch.setattr(work, 'load_package', lambda *a, **kw: NS(pack_sha256=PACK))
    monkeypatch.setattr(activation, 'load_package', lambda *a, **kw: NS(pack_sha256=PACK))
    monkeypatch.setattr(work, 'agent_package', lambda config: (encoded(config), assets_for(encoded(config))))
    client.probe_modes = []
    client.reject_active_probe = False

    def probe(endpoint, session, source_hash, *, research_enabled=False, runtime_profile=None):
        client.probe_modes.append(research_enabled)
        source = client.files[AGENT_ROOT + '/market_intelligence.py']
        actual = False if source == OLD else json.loads(source)['research_enabled']
        if client.reject_active_probe and actual:
            actual = False
        observed = copy.deepcopy(PROFILE)
        if research_enabled and getattr(client, 'drift_active_runtime', False):
            observed['packages']['oci'] = 'different-version'
        return market_probe_receipt({'source_sha256': source_hash, 'schema_version': 1,
            'research_enabled': actual, 'business_tables_required': False,
            'authenticated_session_bound': True, 'model_calls': 0, 'apify_starts': 0,
            'versions': observed}, source_hash, research_enabled=research_enabled, runtime_profile=runtime_profile)

    client._probe_market = probe
    checksum = hashlib.sha256(json.dumps(['tenancy', 'operator', 'fingerprint'], separators=(',', ':')).encode()).hexdigest()
    config = {'schema_version': 1, 'research_enabled': True, 'services': {
        'credential_name': 'AidpRuntime', 'identity_sha256': checksum, 'region': 'us-chicago-1',
        'model_endpoint': 'https://inference.generativeai.us-chicago-1.oci.oraclecloud.com',
        'model_id': 'selected-model', 'compartment_id': 'compartment', 'guardrails_config': {},
        'tenant_id': 'tenancy', 'generation_id': INSTALL, 'namespace': 'namespace', 'bucket': 'artifacts',
        'enabled_sources': ['x'], 'search_context': {'country_code': 'CO', 'language_code': 'es'},
        'candidate_revision': PACK, 'runtime_profile': copy.deepcopy(PROFILE), 'spending_mode': 'unlimited'}}
    return config


def test_initial_install_still_selects_paused_mode(client):
    assert MarketAidpClient._selected_runtime_config(client)['research_enabled'] is False


def test_paused_api_key_binding_is_explicit_schema_two(client):
    client.settings.llm_auth_mode = 'openai'
    config = MarketAidpClient._selected_runtime_config(client)
    assert config == {'schema_version': 2, 'research_enabled': False, 'services': {
        'model_id': 'selected-model', 'region': 'us-chicago-1', 'compartment_id': 'compartment',
        'llm_auth_mode': 'openai'}}


def test_update_switches_auth_under_lock_and_rollback_preserves_credential(client, active_target, monkeypatch):
    active_target['services']['guardrails_config'] = {'policies': []}
    run(client, runtime_config=active_target)
    client.settings.llm_auth_mode = 'openai'
    calls = []
    def ensure(selected, state, key):
        calls.append(key)
        state['resources']['llm_credential_key'] = 'llm'
        selected._save(state)
    monkeypatch.setattr(work, 'ensure_llm_credential', ensure)
    monkeypatch.setattr(work, 'llm_credential_reference', lambda *a: {'credential_key': 'llm'})
    monkeypatch.setattr(activation, 'llm_credential_reference', lambda *a: {'credential_key': 'llm'})
    result = run(client, operation=OTHER, change_model=True, llm_api_key=SecretStr('a' * 32))
    assert result['status'] == 'completed'
    assert client.saved['runtime_config']['schema_version'] == 2
    assert client.saved['runtime_config']['services']['llm_auth_mode'] == 'openai'
    assert len(calls) == 1
    result = run(client, action='rollback', operation=OTHER)
    assert result['status'] == 'rolled_back'
    assert client.saved['runtime_config']['schema_version'] == 1
    assert client.saved['resources']['llm_credential_key'] == 'llm'


def test_maintenance_resume_rejects_changed_openai_key_before_reusing_frozen_target(client, monkeypatch):
    original = 'original-fixture-openai-key-000000000'
    client.saved['resources'].update(llm_credential_key='llm',
        llm_secret_sha256=hashlib.sha256(original.encode()).hexdigest())
    monkeypatch.setattr(work, 'llm_credential_reference', lambda *args: {'credential_key': 'llm'})
    run(client)
    previous = copy.deepcopy(client.saved)
    with pytest.raises(ValueError, match='does not match'):
        run(client, llm_api_key=SecretStr('different-fixture-openai-key-000000'))
    assert client.saved == previous


def test_explicit_activation_commits_same_resources_and_active_probe(client, active_target):
    result = run(client, runtime_config=active_target)
    assert result['status'] == 'completed' and result['research_enabled'] is True
    assert client.saved['runtime_config'] == active_target
    assert client.probe_modes == [True]
    assert_resource_identity_preserved(client)
    status = public_status(client.saved)
    assert status['enabled'] is True and status['research_enabled'] is True


@pytest.mark.parametrize('key,value', [
    ('credential_name', 'different-credential'), ('identity_sha256', 'b' * 64),
    ('tenant_id', 'different-tenant'), ('generation_id', OTHER), ('bucket', 'different-bucket'),
    ('namespace', 'different-namespace'), ('model_id', 'different-model'),
    ('model_endpoint', 'https://example.com'), ('candidate_revision', 'b' * 64),
    ('runtime_profile', {'python': '3.11.99', 'packages': PROFILE['packages']})])
def test_foreign_or_stale_binding_fails_before_backup_writes(client, active_target, key, value):
    active_target['services'][key] = value
    with pytest.raises((AidpProvisionConflict, ValueError)):
        run(client, runtime_config=active_target)
    assert not client.uploads and not client.refreshes


def test_incomplete_activation_does_not_change_installer(client, active_target):
    del active_target['services']['runtime_profile']
    with pytest.raises(ValueError):
        run(client, runtime_config=active_target)
    assert not client.uploads and not client.refreshes


def test_rejected_active_probe_restores_paused_bytes_and_configuration(client, active_target):
    client.reject_active_probe = True
    result = run(client, runtime_config=active_target)
    assert result['status'] == 'rolled_back' and result['research_enabled'] is False
    assert client.saved['runtime_config']['research_enabled'] is False
    assert client.probe_modes == [True, False]
    assert all(client.files[path] == raw for path, raw in client.original_files.items())
    assert_resource_identity_preserved(client)


def test_uncertain_refresh_keeps_saved_target_and_does_not_report_active(client, active_target):
    client.lose_refresh = True
    with pytest.raises(AidpProvisionPending):
        run(client, runtime_config=active_target, max_steps=3)
    assert client.saved['maintenance']['phase'] == 'target_wait'
    assert not public_status(client.saved)['enabled']
    assert client.saved['maintenance']['target_runtime_config'] == active_target
    assert len(client.refreshes) == 1
    client.publish_revision()
    result = run(client, runtime_config=active_target)
    assert result['research_enabled'] is True and len(client.refreshes) == 1


def test_resume_rejects_changed_binding_without_a_second_refresh(client, active_target):
    with pytest.raises(AidpProvisionPending):
        run(client, runtime_config=active_target, max_steps=1)
    changed = copy.deepcopy(active_target)
    changed['services']['enabled_sources'] = ['web']
    writes = len(client.uploads)
    with pytest.raises(AidpProvisionConflict, match='exact saved research binding'):
        run(client, runtime_config=changed)
    assert len(client.uploads) == writes and not client.refreshes


def test_pending_update_from_active_does_not_report_enabled(client, active_target):
    run(client, runtime_config=active_target)
    client.lose_refresh = True
    with pytest.raises(AidpProvisionPending):
        run(client, operation=OTHER, max_steps=3)
    status = public_status(client.saved)
    assert status['research_enabled'] is True
    assert status['status'] == 'updating' and status['enabled'] is False
    client.saved['maintenance']['phase'] = 'rollback_wait'
    assert public_status(client.saved)['enabled'] is False


def test_changed_runtime_with_same_langgraph_restores_paused_candidate(client, active_target):
    client.drift_active_runtime = True
    result = run(client, runtime_config=active_target)
    assert result['status'] == 'rolled_back' and result['research_enabled'] is False
    assert client.saved['runtime_config']['research_enabled'] is False
    assert client.probe_modes == [True, False]
    assert all(client.files[path] == raw for path, raw in client.original_files.items())


def test_following_update_preserves_active_mode_and_advances_code_revision(client, active_target, monkeypatch):
    run(client, runtime_config=active_target)
    next_pack = 'b' * 64
    for module in (work, activation):
        monkeypatch.setattr(module, 'load_package', lambda *a, **kw: NS(pack_sha256=next_pack))
    result = run(client, operation=OTHER)
    assert result['research_enabled'] is True
    assert client.saved['runtime_config']['services']['candidate_revision'] == next_pack
    assert client.saved['runtime_config']['services']['credential_name'] == 'AidpRuntime'
    assert_resource_identity_preserved(client)


def test_paused_probe_rejects_unrequested_active_runtime():
    with pytest.raises(AidpProvisionError, match='paused'):
        market_probe_receipt({'source_sha256': PACK, 'schema_version': 1, 'research_enabled': True,
            'business_tables_required': False, 'authenticated_session_bound': True,
            'model_calls': 0, 'apify_starts': 0, 'versions': PROFILE}, PACK)


@pytest.mark.parametrize('status,code', [('completed', 0), ('rolled_back', 1)])
def test_activation_cli_uses_existing_maintenance_without_exporting_tokens(command, active_target, tmp_path, capsys, status, code):
    from ai_market_intelligence.cli import main
    path = tmp_path / 'runtime-binding.json'
    path.write_text(json.dumps(active_target))
    command.maintain.return_value.update(status=status, research_enabled=status == 'completed')
    result = main(['activate-test', *command.base, '--research-binding', str(path), '--apply'])
    assert result == code
    call = command.maintain.call_args
    assert call.args == (command.native, 'update', command.base[command.base.index('--operation-id') + 1])
    assert call.kwargs['runtime_config'] == active_target
    command.preflight.assert_not_called()
    assert json.loads(capsys.readouterr().out)['research_enabled'] == (status == 'completed')
    assert command.closed == [True]


def test_activation_cli_requires_binding_before_opening_settings():
    from ai_market_intelligence.cli import main
    with pytest.raises(SystemExit) as error:
        main(['activate-test', '--settings', 'missing', '--operation-id', OP, '--apply'])
    assert error.value.code == 2


def test_oversize_binding_fails_before_maintenance(command, tmp_path, capsys):
    from ai_market_intelligence.cli import main
    path = tmp_path / 'runtime-binding.json'
    path.write_bytes(b' ' * 65537)
    assert main(['activate-test', *command.base, '--research-binding', str(path), '--apply']) == 1
    command.maintain.assert_not_called()
    assert json.loads(capsys.readouterr().out)['status'] == 'blocked'
    assert command.closed == [True]


@pytest.mark.parametrize('installed_mode', ['openai', 'oci_iam'])
def test_code_update_preserves_deploy_studio_provider_and_model(client, active_target, monkeypatch, installed_mode):
    client.settings.llm_auth_mode = installed_mode
    if installed_mode == 'openai':
        active_target['schema_version'] = 2
        active_target['services'].update(llm_auth_mode='openai', llm_credential_name='AiMarketIntelligenceOpenAI', guardrails_config={'policies': []},
            model_endpoint='https://api.openai.com/v1')
        client.saved['resources']['llm_credential_key'] = 'llm'
        monkeypatch.setattr(work, 'ensure_llm_credential', lambda *a: None)
        monkeypatch.setattr(work, 'llm_credential_reference', lambda *a: {'credential_key': 'llm'})
        monkeypatch.setattr(activation, 'llm_credential_reference', lambda *a: {'credential_key': 'llm'})
    run(client, runtime_config=active_target)
    original = copy.deepcopy(client.saved['runtime_config'])
    client.settings.llm_auth_mode = 'oci_iam' if installed_mode == 'openai' else 'openai'
    client.settings.agent_model_id = 'stale-settings-model'
    monkeypatch.setattr(work, 'ensure_llm_credential', lambda *a: pytest.fail('Code update must not select or rotate a credential'))
    run(client, operation=OTHER)
    assert client.saved['runtime_config'] == original
