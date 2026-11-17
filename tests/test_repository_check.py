"""Renames must not make source validation crash or omit newly moved files."""
from pathlib import Path
import subprocess
import sys
import pytest


def copy_policy(root, target):
    policy = target / '.sentrux'
    policy.mkdir()
    for name in ('rules.toml', 'rules.schema.json'):
        (policy / name).write_bytes((root / '.sentrux' / name).read_bytes())


def test_source_check_handles_deleted_tracked_files_and_scans_untracked_replacements(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    subprocess.run(['git', 'init', '--quiet'], cwd=tmp_path, check=True)
    (tmp_path / 'scripts').mkdir()
    script = tmp_path / 'scripts/check_repository.py'
    script.write_bytes((repo / 'scripts/check_repository.py').read_bytes())
    copy_policy(repo, tmp_path)
    old = tmp_path / 'old.py'
    old.write_text('value = 1\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'old.py'], cwd=tmp_path, check=True)
    old.unlink()
    new = tmp_path / 'new.py'
    new.write_text('value = 1\n', encoding='utf-8')
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    new.write_text('from ' + 'app.private import value\n', encoding='utf-8')
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0 and 'new.py: monolith import' in result.stderr


@pytest.mark.parametrize('source,code,blocked', [
    ('aidp/workspaces/default/files/agents/core/probe.py',
     'from ai_market_intelligence import client\n', True),
    ('aidp/workspaces/default/files/agents/core/probe.py',
     'def run():\n    import ai_market_intelligence.client as admin\n', True),
    ('aidp/workspaces/default/files/agents/core/probe.py',
     'from src import ai_market_intelligence\n', True),
    ('aidp/workspaces/default/files/agents/market_intelligence.py',
     'from scripts.market_intake import build_graph\n', True),
    ('src/ai_market_intelligence/probe.py', 'import scripts.market_intake\n', True),
    ('aidp/workspaces/default/files/agents/core/probe.py',
     'from oracle_memory_clients.client import AsyncProxyCheckpointClient\n', False),
    ('aidp/workspaces/default/files/agents/core/probe.py', 'from . import client\n', False),
    ('src/ai_market_intelligence/probe.py', 'from .client import MarketAidpClient\n', False),
])
def test_production_import_boundaries_use_complete_module_names(tmp_path, source, code, blocked):
    root = Path(__file__).resolve().parents[1]
    subprocess.run(['git', 'init', '--quiet'], cwd=tmp_path, check=True)
    script = tmp_path / 'scripts/check_repository.py'
    script.parent.mkdir()
    script.write_bytes((root / 'scripts/check_repository.py').read_bytes())
    copy_policy(root, tmp_path)
    path = tmp_path / source
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding='utf-8')
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True)
    assert (result.returncode != 0) is blocked, result.stdout + result.stderr
    if blocked:
        assert source + ': forbidden production import:' in result.stderr


@pytest.mark.parametrize('rules,valid', [
    ('[constraints]\nmax_cycles = 0\nno_god_files = true\n', True),
    ('[constraints]\nmax_cyclez = 0\n', False),
    ('[constraints]\nmax_cyclez = 0\nno_god_files = true\n', False),
    ('[constraints]\nmax_cycles = "never"\n', False),
    ('[constraints]\nmax_cycles = 0.0\n', False),
    ('[constraints]\nmax_cycles = true\n', False),
    ('[constraints]\nmax_cycles = 9223372036854775807\n', True),
    ('[constraints]\nmax_cycles = 9223372036854775808\n', False),
    ('[constraints]\nmax_cycles = 18446744073709551616\n', False),
    ('[constraints]\nmax_cc = 4294967296\n', False),
    ('[constraints]\nmin_quality = 7000\n', False),
    ('[constraints]\nmin_quality = nan\n', False),
    ('[constraints]\nmax_coupling_score = inf\n', False),
    ('[constraints]\nmax_coupling = "B"\n', False),
    ('[constraints]\nno_god_files = false\n', False),
    ('[constraints\nmax_cycles = 0\n', False),
    ('[[boundaries]]\nfrom = "src/**"\ntoo = "scripts/**"\n', False),
    ('[constraints]\nmax_cycles = 0\n[[layers]]\nname = "unsupported"\n', False),
])
def test_sentrux_policy_rejects_ignored_or_invalid_configuration(tmp_path, rules, valid):
    root = Path(__file__).resolve().parents[1]
    script = tmp_path / 'scripts/check_repository.py'
    script.parent.mkdir()
    script.write_bytes((root / 'scripts/check_repository.py').read_bytes())
    policy = tmp_path / '.sentrux'
    policy.mkdir()
    (policy / 'rules.schema.json').write_bytes((root / '.sentrux/rules.schema.json').read_bytes())
    (policy / 'rules.toml').write_text(rules, encoding='utf-8')
    result = subprocess.run([sys.executable, str(script), '--rules-only'], cwd=tmp_path,
                            capture_output=True, text=True)
    assert (result.returncode == 0) is valid, result.stdout + result.stderr
    if not valid:
        assert 'Sentrux' in result.stderr


def test_rules_only_requires_the_policy_before_any_git_inventory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = tmp_path / 'scripts/check_repository.py'
    script.parent.mkdir()
    script.write_bytes((root / 'scripts/check_repository.py').read_bytes())
    result = subprocess.run([sys.executable, str(script), '--rules-only'], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Sentrux rules are required' in result.stderr
    assert 'not a git repository' not in result.stderr


def test_full_source_check_cannot_succeed_after_policy_removal(tmp_path):
    root = Path(__file__).resolve().parents[1]
    subprocess.run(['git', 'init', '--quiet'], cwd=tmp_path, check=True)
    script = tmp_path / 'scripts/check_repository.py'
    script.parent.mkdir()
    script.write_bytes((root / 'scripts/check_repository.py').read_bytes())
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Sentrux rules are required' in result.stderr


@pytest.mark.parametrize('name,private', [
    ('src/ai_market_intelligence/settings.tfstate.backup', True),
    ('src/ai_market_intelligence/settings.TFVARS', True),
    ('src/ai_market_intelligence/.ENV', True),
    ('.OCI/config', True),
    ('.aws/config', True),
    ('.codex/config.toml', True),
    ('.codex/AGENTS.md', False),
    ('.codex/improvement-log.md', False),
    ('terraform/example.tfvars.json', False),
])
def test_source_inventory_keeps_private_artifacts_out_without_rejecting_guidance(tmp_path, name, private):
    root = Path(__file__).resolve().parents[1]
    subprocess.run(['git', 'init', '--quiet'], cwd=tmp_path, check=True)
    script = tmp_path / 'scripts/check_repository.py'
    script.parent.mkdir()
    script.write_bytes((root / 'scripts/check_repository.py').read_bytes())
    copy_policy(root, tmp_path)
    file = tmp_path / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text('fixture-data\n', encoding='utf-8')
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True)
    assert (result.returncode != 0) is private, result.stdout + result.stderr
    if private:
        assert 'private/generated' in result.stderr
