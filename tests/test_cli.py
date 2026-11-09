import json
import pytest
from ai_market_intelligence.cli import main


def test_package_command_runs_without_credentials_or_network(capsys):
    assert main(['package-check']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['runtime_files'] == 42 and result['deployment_surface'] == 'TEST'
    assert result['research_enabled'] is False


def test_mutation_requires_explicit_apply_before_reading_files():
    with pytest.raises(SystemExit) as failure:
        main(['install', '--settings', 'not-a-real-file'])
    assert failure.value.code == 2


def test_bad_private_settings_are_redacted(tmp_path, capsys):
    settings = tmp_path / 'synthetic.json'
    settings.write_text('{"password":"synthetic-private-input"}')
    assert main(['status','--settings',str(settings)]) == 1
    assert 'synthetic-private-input' not in capsys.readouterr().out
