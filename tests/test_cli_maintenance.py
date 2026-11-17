"""CLI dispatch preserves explicit maintenance authority and read-only admission."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from ai_market_intelligence import cli

OP = '11111111-1111-4111-8111-111111111111'
SESSION = '22222222-2222-4222-8222-222222222222'
SECRET = 'apify_api_private_cli_value_123456'


@pytest.fixture
def command(monkeypatch, tmp_path):
    from ai_market_intelligence import settings, client, authorization, lifecycle, maintenance, apify_preflight
    settings_file = tmp_path / 'settings.json'
    settings_file.write_text('{}')
    token_file = tmp_path / 'token.txt'
    token_file.write_text(SECRET)
    monkeypatch.setattr(settings.Settings, 'model_validate_json', lambda raw: NS())
    closed = []
    class Client:
        async def close(self): closed.append(True)
    native = Client()
    monkeypatch.setattr(client, 'MarketAidpClient', lambda *a: native)
    verify = Mock()
    monkeypatch.setattr(authorization, 'verify_administrator', verify)
    install = Mock(side_effect=AssertionError('Maintenance must never enter installer'))
    monkeypatch.setattr(lifecycle, 'install', install)
    bootstrap = Mock(side_effect=AssertionError('Maintenance must never grant roles'))
    monkeypatch.setattr(lifecycle, 'bind_new_administrator', bootstrap)
    preflight = Mock(return_value={'reads_verified': True, 'apify_starts': 0})
    monkeypatch.setattr(apify_preflight, 'check_apify', preflight)
    maintain = Mock(return_value={'status':'completed','phase':'ready','operation_id':OP,
        'action':'update','research_enabled':False,'deployment_surface':'TEST'})
    monkeypatch.setattr(maintenance, 'maintain', maintain)
    base = ['--settings',str(settings_file),'--token-file',str(token_file),'--operation-id',OP]
    return NS(base=base, native=native, maintain=maintain, preflight=preflight, verify=verify,
              install=install, bootstrap=bootstrap, closed=closed)


@pytest.mark.parametrize('action', ['update','redeploy','rollback'])
def test_mutation_requires_apply_before_reading_any_files(action):
    with pytest.raises(SystemExit) as error:
        cli.main([action,'--settings','does-not-exist'])
    assert error.value.code == 2


def test_preserving_production_requires_the_explicit_plugin_driver():
    with pytest.raises(SystemExit) as error:
        cli.main(['update','--preserve-prod','--apply','--settings','missing'])
    assert error.value.code==2


def test_cli_routes_explicit_snapshot_preservation_to_the_selected_plugin(command,monkeypatch,tmp_path):
    from ai_market_intelligence.installation import plugin_test
    driver=Mock()
    monkeypatch.setattr(plugin_test,'PluginTestDeployment',driver)
    assert cli.main(['update',*command.base,'--apply','--preserve-prod',
                     '--project',str(tmp_path),'--plugin-scripts',str(tmp_path/'plugin')])==0
    call=command.maintain.call_args
    assert call.kwargs['preserve_production'] is True
    call.kwargs['native_factory']('selected-settings')
    driver.assert_called_once_with('selected-settings',project=tmp_path,
        plugin_scripts=tmp_path/'plugin',operation_id=OP)


@pytest.mark.parametrize('action', ['update','redeploy','rollback'])
def test_exact_operation_and_session_reach_maintenance_after_read_admission(command,capsys,action):
    assert cli.main([action,*command.base,'--test-session-id',SESSION,'--apply']) == 0
    call=command.maintain.call_args
    assert call.args == (command.native,action,OP)
    assert call.kwargs.get('test_session_id') == SESSION
    if action == 'rollback':
        command.preflight.assert_not_called()
    else:
        assert command.preflight.call_args.args[0].get_secret_value() == SECRET
    command.verify.assert_called_once_with(command.native)
    command.install.assert_not_called()
    command.bootstrap.assert_not_called()
    result=json.loads(capsys.readouterr().out)
    assert result['research_enabled'] is False and result['deployment_surface']=='TEST'
    assert SECRET not in json.dumps(result) and command.closed==[True]


@pytest.mark.parametrize('action', ['update','redeploy'])
def test_failed_apify_read_cannot_start_maintenance_or_install(command,capsys,action):
    command.preflight.return_value={'reads_verified':False,'apify_starts':0}
    assert cli.main([action,*command.base,'--apply']) == 1
    command.maintain.assert_not_called()
    command.install.assert_not_called()
    command.bootstrap.assert_not_called()
    assert json.loads(capsys.readouterr().out)['status']=='blocked'
    assert command.closed==[True]


@pytest.mark.parametrize('operation', ['bad','AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA', '00000000-0000-0000-0000-00000000000Z'])
def test_invalid_uuid_is_rejected_before_any_maintenance_mutation(command,capsys,operation):
    args=list(command.base)
    args[args.index(OP)]=operation
    with pytest.raises(SystemExit) as error:
        cli.main(['update',*args,'--apply'])
    assert error.value.code == 2
    command.maintain.assert_not_called()
    command.install.assert_not_called()
    assert SECRET not in capsys.readouterr().out


def test_api_key_file_reaches_owner_without_printing_the_secret(command, monkeypatch, tmp_path, capsys):
    from ai_market_intelligence import settings
    monkeypatch.setattr(settings.Settings, 'model_validate_json', lambda raw: NS(llm_auth_mode='openai'))
    secret = 'fixture_llm_key_' + 'a' * 32
    key_file = tmp_path / 'llm-key.txt'
    key_file.write_text(secret + '\n', encoding='utf-8')
    assert cli.main(['update', *command.base, '--apply', '--change-model', '--llm-api-key-file', str(key_file)]) == 0
    assert command.maintain.call_args.kwargs['llm_api_key'].get_secret_value() == secret
    assert secret not in capsys.readouterr().out


def test_llm_secret_file_is_rejected_for_iam_without_reading_it(command, monkeypatch, tmp_path, capsys):
    from ai_market_intelligence import settings
    monkeypatch.setattr(settings.Settings, 'model_validate_json', lambda raw: NS(llm_auth_mode='oci_iam'))
    assert cli.main(['update', *command.base, '--apply', '--llm-api-key-file', str(tmp_path / 'missing')]) == 1
    command.maintain.assert_not_called()
    command.install.assert_not_called()
    assert SECRET not in capsys.readouterr().out


def test_rollback_status_is_returned_truthfully(command,capsys):
    command.maintain.return_value.update(status='rolled_back',action='rollback')
    assert cli.main(['rollback',*command.base,'--apply']) == 0
    assert json.loads(capsys.readouterr().out)['status']=='rolled_back'


@pytest.mark.parametrize('pending_phase', [None, 'test', 'rebuild'])
def test_rebuild_exhausted_wait_is_not_reported_as_completed(command, monkeypatch, tmp_path, capsys, pending_phase):
    from ai_market_intelligence import rebuild
    from ai_market_intelligence.errors import AidpProvisionPending
    plugin = tmp_path / 'plugin'
    (plugin / 'aidp_agent_flow').mkdir(parents=True)
    (plugin / 'aidp_agent_flow/native_layout.py').touch()
    prepare = Mock(return_value=('previous', 'current'))
    advance = Mock(side_effect=AidpProvisionPending('Still waiting', pending_phase) if pending_phase else None,
                   return_value={'status': 'running', 'phase': 'test_wait'})
    monkeypatch.setattr(rebuild, 'prepare', prepare)
    monkeypatch.setattr(rebuild, 'advance', advance)
    monkeypatch.setattr(cli.time, 'sleep', lambda _: None)
    code = cli.main(['rebuild-development', *command.base, '--plugin-scripts', str(plugin), '--apply'])
    assert code == 2
    assert json.loads(capsys.readouterr().out)['status'] == ('pending' if pending_phase else 'running')
    assert advance.call_count == 120
    assert all(call.args == (command.native, 'previous', 'current', OP) for call in advance.call_args_list)
    assert command.closed == [True]


@pytest.mark.parametrize('apply', [False, True])
def test_rebuild_completed_or_read_only_plan_returns_success(command, monkeypatch, tmp_path, capsys, apply):
    from ai_market_intelligence import rebuild
    plugin = tmp_path / 'plugin'
    (plugin / 'aidp_agent_flow').mkdir(parents=True)
    (plugin / 'aidp_agent_flow/native_layout.py').touch()
    result = {'status': 'completed'} if apply else {'applied': False, 'action': 'rebuild-development'}
    advance = Mock(return_value=result)
    monkeypatch.setattr(rebuild, 'prepare', Mock(return_value=('previous', 'current')))
    monkeypatch.setattr(rebuild, 'advance', advance)
    code = cli.main(['rebuild-development', *command.base, '--plugin-scripts', str(plugin),
                     *(['--apply'] if apply else [])])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == result
    advance.assert_called_once_with(command.native, 'previous', 'current', OP, apply=apply)
    assert command.closed == [True]


def test_maintenance_exception_is_redacted_and_client_closed(command,capsys):
    command.maintain.side_effect=RuntimeError(SECRET)
    assert cli.main(['update',*command.base,'--apply'])==1
    text=capsys.readouterr().out
    assert SECRET not in text and json.loads(text)['status']=='blocked'
    assert command.closed==[True]


@pytest.mark.parametrize('action',['update','redeploy'])
def test_failed_candidate_is_not_reported_success_after_restoring_previous(command,capsys,action):
    command.maintain.return_value.update(status='rolled_back',action=action)
    assert cli.main([action,*command.base,'--apply']) == 1
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='rolled_back' and result['research_enabled'] is False


@pytest.mark.parametrize('flag',['--operation-id','--token-file'])
def test_required_operation_and_protected_token_inputs_cannot_be_omitted(command,flag):
    args=list(command.base)
    index=args.index(flag)
    del args[index:index+2]
    with pytest.raises(SystemExit) as error:
        cli.main(['update',*args,'--apply'])
    assert error.value.code==2
    command.preflight.assert_not_called()
    command.maintain.assert_not_called()
    command.install.assert_not_called()
    assert command.closed == ([] if flag=='--operation-id' else [True])


@pytest.mark.parametrize('token_selection',['omitted','missing-file'])
def test_explicit_rollback_needs_no_apify_or_token_file(command,capsys,token_selection):
    args=list(command.base)
    index=args.index('--token-file')
    del args[index:index+2]
    if token_selection=='missing-file':
        args.extend(['--token-file','a-token-file-that-does-not-exist'])
    command.preflight.side_effect=AssertionError('Apify must not gate restoration')
    command.maintain.return_value.update(status='rolled_back',action='rollback')
    assert cli.main(['rollback',*args,'--apply'])==0
    command.preflight.assert_not_called()
    command.verify.assert_called_once_with(command.native)
    assert command.maintain.call_args.args==(command.native,'rollback',OP)
    assert json.loads(capsys.readouterr().out)['status']=='rolled_back'
    assert command.closed==[True]


def test_rollback_cannot_bypass_revoked_administrator(command,capsys):
    from ai_market_intelligence.errors import AidpProvisionConflict
    command.verify.side_effect=AidpProvisionConflict('administrator revoked')
    assert cli.main(['rollback',*command.base,'--apply'])==1
    command.preflight.assert_not_called()
    command.maintain.assert_not_called()
    command.install.assert_not_called()
    assert json.loads(capsys.readouterr().out)['status']=='blocked'
    assert command.closed==[True]


def test_explicit_provider_model_selection_is_forwarded(command):
    assert cli.main(['update', *command.base, '--apply', '--change-model']) == 0
    assert command.maintain.call_args.kwargs['change_model'] is True


@pytest.mark.parametrize('action', ['redeploy', 'rollback', 'status'])
def test_change_model_flag_requires_update_before_reading_files(action):
    with pytest.raises(SystemExit) as error:
        cli.main([action, '--change-model', '--settings', 'missing'])
    assert error.value.code == 2


def test_code_only_update_rejects_secret_file_without_reading_it(command, monkeypatch, tmp_path):
    from ai_market_intelligence import settings
    monkeypatch.setattr(settings.Settings, 'model_validate_json', lambda raw: NS(llm_auth_mode='openai'))
    assert cli.main(['update', *command.base, '--apply', '--llm-api-key-file', str(tmp_path / 'missing')]) == 1
    command.maintain.assert_not_called()


@pytest.mark.parametrize('restore', [False, True])
def test_migration_dispatch_preserves_recovery_identity_and_bound(command, monkeypatch, tmp_path, restore):
    from ai_market_intelligence import migration
    plugin = tmp_path / 'plugin'
    (plugin / 'aidp_agent_flow').mkdir(parents=True)
    (plugin / 'aidp_agent_flow/native_lifecycle.py').touch()
    prepare = Mock(return_value='prepared')
    forward = Mock(return_value='frozen-forward')
    migrate = Mock(return_value={'status': 'completed'})
    recover = Mock(return_value={'status': 'restored'})
    for name, value in [('prepare', prepare), ('saved_forward', forward), ('migrate', migrate), ('restore', recover)]:
        monkeypatch.setattr(migration, name, value)
    assert cli.main(['migrate', *command.base, '--apply', '--project', str(tmp_path),
                     '--plugin-scripts', str(plugin), *(['--restore-migration'] if restore else [])]) == 0
    if restore:
        forward.assert_called_once_with(tmp_path, command.native, OP, plugin)
        prepare.assert_called_once_with(tmp_path, command.native, OP, plugin, restoring=True)
        recover.assert_called_once_with(command.native, 'prepared', 'frozen-forward', OP, max_steps=120)
        migrate.assert_not_called()
    else:
        prepare.assert_called_once_with(tmp_path, command.native, OP, plugin)
        migrate.assert_called_once_with(command.native, 'prepared', OP, apply=True, max_steps=120)
        forward.assert_not_called()
        recover.assert_not_called()
    command.preflight.assert_not_called()
    assert command.closed == [True]


@pytest.mark.parametrize('confirm', [False, True])
def test_lock_release_keeps_explicit_confirmation_and_exact_etag(command, monkeypatch, confirm):
    from ai_market_intelligence import operation
    release = Mock()
    monkeypatch.setattr(operation, 'release_lock', release)
    args = ['release-lock', *command.base, '--apply', '--expected-etag', 'observed-etag']
    if confirm:
        assert cli.main([*args, '--confirm-no-running-hook']) == 0
        release.assert_called_once_with(command.native, expected_etag='observed-etag')
    else:
        with pytest.raises(SystemExit) as error:
            cli.main(args)
        assert error.value.code == 2
        release.assert_not_called()
    command.preflight.assert_not_called()
    assert command.closed == [True]


def test_oversized_llm_secret_is_rejected_before_client_creation(command, monkeypatch, tmp_path, capsys):
    from ai_market_intelligence import settings
    monkeypatch.setattr(settings.Settings, 'model_validate_json', lambda raw: NS(llm_auth_mode='openai'))
    path = tmp_path / 'oversized-key.txt'
    path.write_text('a' * 4098)
    assert cli.main(['update', *command.base, '--apply', '--change-model', '--llm-api-key-file', str(path)]) == 1
    command.verify.assert_not_called()
    command.maintain.assert_not_called()
    assert command.closed == []
    assert 'a' * 32 not in capsys.readouterr().out
