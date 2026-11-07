"""Current AIDP source, immutable package and standalone assembly contracts."""
import ast
import hashlib
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from ai_market_intelligence import package as catalog
from ai_market_intelligence.package import load_aidp_manifest
from ai_market_intelligence.installation.agent_package import FILES, SOURCE_ROOT, agent_package
from ai_market_intelligence.installation.agent_source import assemble_agent
from ai_market_intelligence.installation import agent_package as packaging


def test_package_has_only_current_source_and_real_aidp_resources():
    pack = catalog.load_package('ai_market_intelligence', require_available=False)
    assert pack.status == 'planned' and pack.pack_version == '2.0.2'
    with pytest.raises(catalog.PackageError, match='not available yet'):
        catalog.load_package(pack.lab_id)
    assert not pack.datasets and not pack.notebooks and not pack.tables
    assert pack.agent['card']['version'] == pack.pack_version
    expected = {SOURCE_ROOT + 'ai_market_intelligence/' + name for name in FILES}
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


def test_current_assembly_is_deterministic_readable_and_has_one_entrypoint():
    config = {'schema_version': 1, 'research_enabled': False}
    source, assets = agent_package(config)
    assert source == agent_package(config)[0]
    assert assets == {'requirements.txt': b'httpx==0.28.1\npypdf==6.20.1\n'}
    tree = ast.parse(source)
    compile(tree, 'market_intelligence.py', 'exec')
    assert not any(isinstance(node, ast.ImportFrom) and node.level for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in {'exec', 'eval', '__import__'} for node in ast.walk(tree))
    entries = [node.name for node in tree.body if isinstance(node, ast.ClassDef)
               and {'setup', 'invoke'} <= {getattr(child, 'name', '') for child in node.body}]
    assert entries == ['MarketIntelligenceAgent']
    assert all(value not in source for value in (b'CREATE TABLE ', b'DROP TABLE ', b'class Journal', b'class Records'))


@pytest.mark.parametrize('config', [
    {'runtime_version': 'legacy', 'research_enabled': False},
    {'runtime_version': 'v2', 'research_enabled': False},
    {'schema_version': True, 'research_enabled': False},
    {'schema_version': 1, 'research_enabled': False, 'token': 'secret'},
])
def test_unsupported_binding_is_rejected_before_packaging(config):
    with pytest.raises(ValueError):
        agent_package(config)


def test_assembly_rejects_changed_entry_or_relative_dependency():
    pack = catalog.load_package('ai_market_intelligence', require_available=False)
    raw = {asset.name.removeprefix(SOURCE_ROOT): asset.read_bytes() for asset in pack.runtime_files}
    files = {name.removeprefix('ai_market_intelligence/'): content for name, content in raw.items()
             if name.startswith('ai_market_intelligence/')}
    config = {'schema_version': 1, 'research_enabled': False}
    with pytest.raises(ValueError, match='entry wrapper'):
        assemble_agent(files, raw['market_intelligence.py'] + b'\nprint("unexpected")\n', config)
    files['native.py'] += b'\nfrom .unreviewed import effect\n'
    with pytest.raises(ValueError, match='Unknown standalone import'):
        assemble_agent(files, raw['market_intelligence.py'], config)


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
    canonical = root / SOURCE_ROOT / 'ai_market_intelligence'
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


def test_paused_model_binding_is_embedded_without_grants_or_native_version_claims():
    config={'schema_version':1,'research_enabled':False,'services':{
        'model_id':'selected-model','region':'us-chicago-1','compartment_id':'selected-compartment'}}
    source,_=agent_package(config)
    tree=ast.parse(source)
    constants={target.id:ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
        for target in node.targets if isinstance(target,ast.Name) and target.id=='RUNTIME_CONFIG'}
    assert constants['RUNTIME_CONFIG']==config
    changed={**config,'services':{**config['services'],'model_id':'another-model'}}
    other,_=agent_package(changed)
    assert source!=other
    assert 'runtime_profile' not in constants['RUNTIME_CONFIG']['services']
    assert 'spending_mode' not in constants['RUNTIME_CONFIG']['services']


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
