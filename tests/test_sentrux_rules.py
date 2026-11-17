"""Exercise the installed rules engine on isolated allowed and forbidden imports."""
from pathlib import Path
import shutil
import subprocess

import pytest


SENTRUX = shutil.which('sentrux')
pytestmark = pytest.mark.skipif(SENTRUX is None, reason='The Sentrux CLI is not installed')
AGENT = 'aidp/workspaces/default/files/agents/core'


@pytest.fixture
def repository(tmp_path):
    subprocess.run(['git', 'init', '--quiet'], cwd=tmp_path, check=True)
    rules = tmp_path / '.sentrux/rules.toml'
    rules.parent.mkdir()
    rules.write_bytes((Path(__file__).resolve().parents[1] / '.sentrux/rules.toml').read_bytes())
    return tmp_path


def write(repository, name, text):
    path = repository / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def check(repository):
    subprocess.run(['git', 'add', '.'], cwd=repository, check=True)
    return subprocess.run([SENTRUX, 'check', '.'], cwd=repository,
                          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)


def test_allowed_administrative_dependency_on_agent_passes(repository):
    write(repository, AGENT + '/native.py', 'def run():\n    return 1\n')
    write(repository, 'src/ai_market_intelligence/cli.py',
          'from aidp.workspaces.default.files.agents.core.native import run\n')
    result = check(repository)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '6 rules checked' in result.stdout


@pytest.mark.parametrize('source,target,module', [
    (AGENT + '/native.py', 'src/ai_market_intelligence/cli.py', 'ai_market_intelligence.cli'),
    (AGENT + '/native.py', 'src/ai_market_intelligence/hooks.py', 'ai_market_intelligence.hooks'),
    (AGENT + '/native.py', 'scripts/dev_probe.py', 'scripts.dev_probe'),
    ('src/ai_market_intelligence/cli.py', 'scripts/dev_probe.py', 'scripts.dev_probe'),
])
def test_forbidden_resolved_dependencies_fail(repository, source, target, module):
    write(repository, target, 'def run():\n    return 1\n')
    write(repository, source, 'from ' + module + ' import run\n')
    result = check(repository)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'Boundary violation' in result.stdout
    assert source in result.stdout and target in result.stdout


def test_cycle_is_rejected(repository):
    module = AGENT.replace('/', '.')
    write(repository, AGENT + '/a.py', f'from {module}.b import b\ndef a():\n    return b()\n')
    write(repository, AGENT + '/b.py', f'from {module}.a import a\ndef b():\n    return a()\n')
    result = check(repository)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'max_cycles' in result.stdout


def test_excessive_fan_out_is_rejected(repository):
    imports = []
    for index in range(16):
        write(repository, AGENT + f'/leaf_{index}.py', 'def run():\n    return 1\n')
        imports.append('from ' + AGENT.replace('/', '.') + f'.leaf_{index} import run as run_{index}\n')
    write(repository, AGENT + '/hub.py', ''.join(imports))
    result = check(repository)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'no_god_files' in result.stdout
