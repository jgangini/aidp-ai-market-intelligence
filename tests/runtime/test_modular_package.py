"""Exercise the distributable independently of the checkout and its import aliases."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ai_market_intelligence.installation.agent_package import agent_package
from ai_market_intelligence.runtime.package_identity import (
    MANIFEST, directory_identity, source_identity, validate_inventory,
)


def stage(directory, config=None):
    source, assets = agent_package(config or {'schema_version': 1, 'research_enabled': False})
    files = {'market_intelligence.py': source, **assets}
    for name, raw in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return source_identity(files)


def test_clean_subprocess_imports_the_packaged_entry_and_answers_paused_d1(tmp_path):
    expected = stage(tmp_path)
    script = '''
import asyncio, json, pathlib, sys, types
sys.path.insert(0, sys.argv[1])
import market_intelligence
helper=types.ModuleType('aidputils.agents.toolkit.agent_helper')
helper.pre_invoke_setup=lambda **kwargs: {'configurable': {'thread_id': 'isolated-session'}}
sys.modules[helper.__name__]=helper
agent=market_intelligence.MarketIntelligenceAgent()
agent.setup()
async def run():
    probe=await agent.invoke('inspect_runtime',subject_id='fixture')
    probe=[message async for message in probe]
    metadata=json.loads(probe[0].content)
    response=await agent.invoke('que se dice de d1 en colombia',subject_id='fixture',trace_id='fixture')
    response=[message async for message in response]
    assert 'pausada' in response[0].content
    assert '"protocol"' not in response[0].content
    command=json.dumps({'request_id':'fixture-paused','question':'que se dice de d1 en colombia'})
    structured=await agent.invoke(command,subject_id='fixture',trace_id='fixture')
    structured=[message async for message in structured]
    assert json.loads(structured[0].content)['response']['status']=='paused'
    assert agent.runtime.services is None
    assert metadata['source_identity_version']==2
    assert metadata['model_calls']==metadata['apify_starts']==0
    print(metadata['source_sha256'])
asyncio.run(run())
'''
    env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
    result = subprocess.run([sys.executable, '-I', '-c', script, str(tmp_path)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected == directory_identity(tmp_path)


@pytest.mark.parametrize('name', ['core/workflow.py', 'core/decisions/understand.py',
                                  'core/runtime.json', 'requirements.txt', 'market_intelligence.py'])
def test_module_prompt_config_or_dependency_change_invalidates_identity(tmp_path, name):
    stage(tmp_path)
    path = tmp_path / name
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='bytes changed'):
        directory_identity(tmp_path)


@pytest.mark.parametrize('name', ['../private.json', '/Workspace/private.json',
                                  'core/../private.json', 'core/.private.json', 'core\\native.py'])
def test_manifest_cannot_read_outside_the_executable_inventory(name):
    hashes = {'market_intelligence.py': 'a'*64, 'requirements.txt': 'b'*64, name: 'c'*64}
    with pytest.raises(ValueError, match='inventory'):
        validate_inventory(hashes)


def test_prepare_and_verification_share_identity_and_reject_module_drift(tmp_path):
    from tests.runtime.test_market_intelligence_research_lifecycle import lifecycle_script
    script = lifecycle_script()
    config = tmp_path / 'config.json'
    config.write_text('{"schema_version":1,"research_enabled":false}', encoding='utf-8')
    result = script.prepare_market(tmp_path, config, 'candidate')
    settings = {'native': {'remote_root': script.REMOTE_ROOT},
                'agent': {'entry': 'candidate/market_intelligence.py'}}
    script.verify_market_target(tmp_path, settings, candidate_required=True)
    assert result['source_sha256'] == directory_identity(tmp_path / 'candidate')
    path = tmp_path / 'candidate/core/workflow.py'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='bytes changed'):
        script.verify_market_target(tmp_path, settings, candidate_required=True)


def test_prepared_pilot_accepts_full_package_identity_before_any_native_calls(tmp_path):
    from types import SimpleNamespace
    from tests.runtime.test_market_research_pilot import definition, script
    module = script()
    path, _ = definition(tmp_path)
    config = json.loads((tmp_path / 'runtime.json').read_text(encoding='utf-8'))
    stage(tmp_path / 'candidate', config)
    module.prepare_pilot(tmp_path, path)
    lifecycle = SimpleNamespace(config={'agent': {'entry': 'candidate/market_intelligence.py'}})
    assert module.run_pilot(lifecycle, tmp_path, apply=False) == {
        'action': 'pilot-run', 'applied': False, 'cases': 6}
    module_path = tmp_path / 'candidate/core/model.py'
    module_path.write_bytes(module_path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='bytes changed'):
        module.run_pilot(lifecycle, tmp_path, apply=False)


def test_prompt_inventory_preserves_unchanged_text_and_reviewed_research_revision():
    from ai_market_intelligence.runtime.decisions import PROMPTS
    fixture = Path(__file__).with_name('fixtures') / 'prompt-hashes-2.0.3.json'
    expected = json.loads(fixture.read_text(encoding='utf-8'))
    # Live D1 diagnosis changed routing, disambiguation, relevance and attribution.
    expected.update(
        market_select_sources='58896d15b6add9bef378ef50959ebe0f0221cec0779de82051f2245e86f2db9d',
        market_understand='ab4fab90b6068f404ec006d99fffb8f6dfa0fcc3c2f477a485f2fc85e44b0db0',
        market_plan='a74a53493bb3ab35f61c724a232a204acd61d309003444ff195f67920380e8df',
        market_analyze='8b2aa0a9b85272a62bd84301f04879076e4e96e0f3ade0664d9a667f734530c4',
        market_draft='f6aee433309455e010f611ae828e17d4cfaa246c75100e7d9b1ee86f1c0088ea',
        market_consolidate='0b1aad9efa2dff4be246917aee7e7ab7f28537a5ab06ee54b721d0a8419ae6f9',
        # Reviewed distinction between shared predicates and thematic aggregation.
        market_repair_units='298a3073cf4a8cfc6bda2b67995021b0417425df165954a99308381ffb8a091f',
        market_verify='426d743ea7224a7397f4b69b008bfa243f7d847bb63ac90542ba993bd95d5458',
        market_verify_repair='00b4cd03ef4af2571b5b9bea12e89a33cb8f113966bf399a39208dba3efd881e',
    )
    assert {name: hashlib.sha256(text.encode()).hexdigest() for name, text in PROMPTS.items()} == expected


def test_runtime_rejects_changes_after_setup_even_if_manifest_is_rewritten(tmp_path, monkeypatch):
    from tests.runtime.packaged import load_candidate
    from ai_market_intelligence.runtime.package_identity import package_manifest
    entry = load_candidate(tmp_path, monkeypatch, {'schema_version': 1, 'research_enabled': False})
    runtime = entry.MarketIntelligenceAgent()
    runtime.setup()
    (tmp_path / 'core/workflow.py').write_bytes((tmp_path / 'core/workflow.py').read_bytes() + b'\n')
    names = json.loads((tmp_path / MANIFEST).read_bytes())['files']
    changed = {name: (tmp_path/name).read_bytes() for name in names}
    (tmp_path / MANIFEST).write_text(json.dumps(package_manifest(changed)), encoding='utf-8')
    with pytest.raises(ValueError, match='changed after setup'):
        asyncio.run(runtime.invoke('inspect_runtime', subject_id='fixture'))


def test_local_factory_accepts_prepared_identity_before_accessing_evaluation_data(tmp_path, monkeypatch):
    from ai_market_intelligence.runtime import factory
    from tests.runtime.test_market_intelligence_research_lifecycle import lifecycle_script
    script = lifecycle_script()
    config = tmp_path / 'config.json'
    config.write_text('{"schema_version":1,"research_enabled":false}', encoding='utf-8')
    candidate = script.prepare_market(tmp_path, config, 'candidate')
    native = Path(factory.__file__)
    (tmp_path / 'aidp-agent.toml').write_text(
        '[agent]\nentry="candidate/market_intelligence.py"\n[market_test]\n'
        f'factory_sha256="{hashlib.sha256(native.read_bytes()).hexdigest()}"\n'
        f'adapter_sha256="{hashlib.sha256(native.with_name("model_bridge.py").read_bytes()).hexdigest()}"\n'
        f'candidate_revision="{candidate["candidate_revision"]}"\n'
        'snapshot_file="snapshot.json"\nsnapshot_sha256="fixture"\n', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    class ReachedEvaluationData(Exception):
        pass
    def boundary(*args):
        raise ReachedEvaluationData
    monkeypatch.setattr(factory, 'market_checked_json', boundary)
    with pytest.raises(ReachedEvaluationData):
        factory.build_graph({'external_effects_allowed': False, 'model': object()})
    changed = tmp_path / 'candidate/core/model.py'
    changed.write_bytes(changed.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='bytes changed'):
        factory.build_graph({'external_effects_allowed': False, 'model': object()})
