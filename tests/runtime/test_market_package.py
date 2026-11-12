"""Current AIDP source, immutable package and modular execution contracts."""
import ast
import hashlib
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from ai_market_intelligence import package as catalog
from ai_market_intelligence.package import load_aidp_manifest
from ai_market_intelligence.installation.agent_package import FILES, SOURCE_ROOT, agent_package
from ai_market_intelligence.installation import agent_package as packaging


def test_local_profile_matches_target_without_overriding_native_managed_packages():
    import importlib.metadata
    import tomllib
    from ai_market_intelligence.runtime.runtime_versions import TARGET_GRAPH_PACKAGES
    root=catalog.package_root('ai_market_intelligence')
    requirement='langgraph=='+TARGET_GRAPH_PACKAGES['langgraph']
    project=tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8'))
    assert requirement in project['project']['dependencies']
    _,assets=agent_package({'schema_version':1,'research_enabled':False})
    assert not any(line.startswith('langgraph') for line in assets['requirements.txt'].decode().splitlines())
    assert {name:importlib.metadata.version(name) for name in TARGET_GRAPH_PACKAGES}==TARGET_GRAPH_PACKAGES


def test_package_has_only_current_source_and_real_aidp_resources():
    pack = catalog.load_package('ai_market_intelligence', require_available=False)
    assert pack.status == 'planned' and pack.pack_version == '2.2.0'
    with pytest.raises(catalog.PackageError, match='not available yet'):
        catalog.load_package(pack.lab_id)
    assert not pack.datasets and not pack.notebooks and not pack.tables
    assert pack.agent['card']['version'] == pack.pack_version
    expected = {SOURCE_ROOT + 'core/' + name for name in FILES}
    expected.update({SOURCE_ROOT + 'market_intelligence.py', SOURCE_ROOT + 'requirements.txt'})
    assert {asset.name for asset in pack.runtime_files} == expected
    root = catalog.package_root(pack.lab_id)
    manifest = load_aidp_manifest(root, pack.lab_id)
    for asset in manifest['assets']:
        paths = ([asset['source']] if asset['source'] else []) + asset['assembly_sources']
        assert all(PurePosixPath(name).suffix in {'.py', '.json', '.txt'} for name in paths)
        assert all(not any(part == '__pycache__' or part.startswith('.') for part in PurePosixPath(name).parts)
                   for name in paths)
    assert manifest['publication_root'] == '/Workspace/.artifact/ai_market_intelligence'
    assert manifest['model'] == {'metadata': None, 'tables': []}
    assert manifest['workflows'] == []
    assert manifest['agents'] == ['aidp/workspaces/default/agent_flow/agents/ai_market_intelligence.json']
    assert manifest['compute'] == ['aidp/workspaces/default/agent_flow/compute/ai_market_intelligence.json']
    assert pack.expected_results['installation'] == {
        'business_tables_required': False, 'research_enabled': False, 'scheduled': False,
        'deployment_surface': 'TEST', 'semantic_accepted': False,
    }
    assert not (root / 'package/notebooks').exists()
    for asset in (*pack.runtime_files, *pack.source_manifests):
        raw = asset.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == asset.sha256
        assert b'\r\n' not in raw


@pytest.mark.parametrize('name', [
    'ai_market_intelligence/__pycache__/native.cpython-311.pyc',
    'ai_market_intelligence/.private/credentials.json',
    'ai_market_intelligence/native.pyc',
])
def test_unreviewed_cache_or_private_asset_is_never_read_or_published(monkeypatch, name):
    pack = catalog.load_package('ai_market_intelligence', require_available=False)
    unexpected = SimpleNamespace(name=SOURCE_ROOT + name,
        read_bytes=lambda: pytest.fail('Unreviewed content must not be read for publication'))
    monkeypatch.setattr(packaging, 'load_package', lambda *a, **k:
                        SimpleNamespace(runtime_files=(*pack.runtime_files, unexpected)))
    for publish in (packaging.authoring_files,
                    lambda: packaging.agent_package({'schema_version': 1, 'research_enabled': False})):
        with pytest.raises(catalog.PackageError, match='every reviewed runtime file'):
            publish()


def test_current_package_is_deterministic_and_keeps_the_maintained_entry():
    from ai_market_intelligence.runtime.package_identity import source_identity
    config = {'schema_version': 1, 'research_enabled': False}
    source, assets = agent_package(config)
    assert (source, assets) == agent_package(config)
    assert source == (catalog.package_root('ai_market_intelligence') / SOURCE_ROOT / 'market_intelligence.py').read_bytes()
    assert b'from core.native import MarketAgentRuntime' in source
    assert 'core/runtime.json' in assets and 'package-manifest.json' in assets
    assert len(source.splitlines()) < 20
    assert len(source_identity({'market_intelligence.py': source, **assets})) == 64


@pytest.mark.parametrize('config', [
    {'runtime_version': 'legacy', 'research_enabled': False},
    {'runtime_version': 'v2', 'research_enabled': False},
    {'schema_version': True, 'research_enabled': False},
    {'schema_version': 1, 'research_enabled': False, 'token': 'secret'},
])
def test_unsupported_binding_is_rejected_before_packaging(config):
    with pytest.raises(ValueError):
        agent_package(config)


def test_loaded_runtime_rejects_content_drift(tmp_path):
    folder = tmp_path / 'aidp'
    folder.mkdir()
    source = folder / 'runtime.py'
    source.write_bytes(b'initial')
    asset = catalog._module_assets(tmp_path, [{'file': 'aidp/runtime.py',
        'sha256': hashlib.sha256(b'initial').hexdigest()}], 'aidp')[0]
    source.write_bytes(b'changed')
    with pytest.raises(catalog.PackageError, match='SHA-256 changed'):
        asset.read_bytes()


def test_static_runtime_namespace_uses_canonical_sources_and_identifier_constants():
    import ai_market_intelligence.runtime as runtime
    root = catalog.package_root('ai_market_intelligence')
    canonical = root / SOURCE_ROOT / 'core'
    assert runtime.__path__ == [str(canonical)]
    values = {}
    for node in ast.parse((canonical / '__init__.py').read_bytes()).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            values[node.targets[0].id] = node.value.value
    assert runtime.AGENT_NAME == values['AGENT_NAME']
    assert runtime.APIFY_CREDENTIAL_NAME == values['APIFY_CREDENTIAL_NAME']


def test_package_resolver_rejects_other_products():
    for name in ('ai_data_governance', 'gods_eye_view', '../ai_market_intelligence', None):
        with pytest.raises(catalog.PackageError, match='Unsupported package_id'):
            catalog.load_package(name, require_available=False)


def test_paused_model_binding_is_packaged_without_grants_or_native_version_claims():
    import json
    from ai_market_intelligence.runtime.package_identity import source_identity
    config={'schema_version':1,'research_enabled':False,'services':{
        'model_id':'selected-model','region':'us-chicago-1','compartment_id':'selected-compartment'}}
    source,assets=agent_package(config)
    assert json.loads(assets['core/runtime.json'])==config
    changed={**config,'services':{**config['services'],'model_id':'another-model'}}
    other,other_assets=agent_package(changed)
    assert source==other
    assert source_identity({'market_intelligence.py':source,**assets})!=source_identity({'market_intelligence.py':other,**other_assets})
    assert 'runtime_profile' not in config['services']
    assert 'spending_mode' not in config['services']


@pytest.mark.parametrize('change', [
    {'model_id':''},{'model_id':'bad model'},{'region':'http://example.org'},
    {'compartment_id':'contains whitespace'},{'token':'private'}, {'runtime_profile':{}},
])
def test_paused_model_binding_rejects_malformed_or_additional_fields(change):
    services={'model_id':'selected-model','region':'us-chicago-1','compartment_id':'compartment',**change}
    with pytest.raises(ValueError):
        agent_package({'schema_version':1,'research_enabled':False,'services':services})


def test_paused_subset_does_not_authorize_research():
    with pytest.raises(ValueError,match='complete reviewed binding'):
        agent_package({'schema_version':1,'research_enabled':True,'services':{
            'model_id':'model','region':'us-chicago-1','compartment_id':'compartment'}})
