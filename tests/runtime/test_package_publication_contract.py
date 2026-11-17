"""Validate publication manifests against synthetic, non-private package inputs."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import runpy
import tarfile
from types import ModuleType
import zipfile

import pytest

from ai_market_intelligence import package as catalog


def generated_manifest(tmp_path, *, source_present=False):
    aidp = tmp_path / 'aidp'
    aidp.mkdir()
    (aidp / 'input.py').write_bytes(b'value = 1\n')
    asset = {
        'workspace_path': '/Workspace/.artifact/ai_market_intelligence/runtime.py',
        'kind': 'python', 'generated': True, 'assembly_sources': ['aidp/input.py'],
    }
    if source_present:
        asset['source'] = None
    manifest = {
        'schema_version': 1, 'module_id': 'ai_market_intelligence',
        'workspace_id': 'default',
        'publication_root': '/Workspace/.artifact/ai_market_intelligence',
        'assets': [asset], 'model': {'metadata': None, 'tables': []},
        'workflows': [], 'compute': [], 'agents': [],
    }
    (aidp / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return manifest


@pytest.mark.parametrize('source_present', [False, True])
def test_generated_source_closure_accepts_absent_or_null_source(tmp_path, source_present):
    generated_manifest(tmp_path, source_present=source_present)
    manifest = catalog.load_aidp_manifest(tmp_path, 'ai_market_intelligence')
    assert catalog.manifest_sources(manifest) == {'aidp/manifest.json', 'aidp/input.py'}


@pytest.mark.parametrize('name', [
    'deployment.tfvars', 'DEPLOYMENT.TFVARS', 'terraform.tfstate',
    'terraform.tfstate.backup', 'terraform.TFSTATE.temporary',
])
def test_private_terraform_input_is_rejected_before_read(tmp_path, name):
    (tmp_path / name).write_text('synthetic private input', encoding='utf-8')
    with pytest.raises(ValueError, match='Private configuration'):
        catalog.source_file(tmp_path, name)


@pytest.mark.parametrize('name', ['.OCI/config', '.LOCAL/receipt.json', '.ENV', '.ENV.production'])
def test_private_path_case_cannot_bypass_publication_boundary(tmp_path, name):
    source = tmp_path / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('synthetic private input', encoding='utf-8')
    with pytest.raises(ValueError, match='Private configuration'):
        catalog.source_file(tmp_path, name)


@pytest.mark.parametrize('name', ['main.tf', 'terraform/example.tfvars.json', 'aidp/settings.json'])
def test_public_source_and_terraform_example_remain_valid(tmp_path, name):
    source = tmp_path / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('synthetic public input', encoding='utf-8')
    assert catalog.source_file(tmp_path, name) == source.resolve()


def write_manifest(tmp_path, manifest):
    (tmp_path / 'aidp/manifest.json').write_text(json.dumps(manifest), encoding='utf-8')


@pytest.mark.parametrize(('change', 'message'), [
    ({'workspace_path': '/Workspace/elsewhere/x.py', 'kind': 'invalid'}, 'publication targets'),
    ({'kind': 'invalid', 'assembly_sources': None}, 'kind or generation'),
    ({'generated': 1}, 'kind or generation'),
    ({'assembly_sources': ['aidp/input.py', 'aidp/input.py']}, 'assembly source closure'),
    ({'assembly_sources': [123]}, 'assembly source closure'),
    ({'assembly_sources': []}, 'explicit source closure'),
    ({'source': 'aidp/input.py'}, 'explicit source closure'),
    ({'assembly_sources': ['aidp/missing.py']}, 'Missing or external'),
    ({'assembly_sources': ['aidp/deployment.tfvars']}, 'Private configuration'),
])
def test_generated_asset_validation_retains_contract_and_order(tmp_path, change, message):
    manifest = generated_manifest(tmp_path)
    manifest['assets'][0].update(change)
    write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match=message):
        catalog.load_aidp_manifest(tmp_path, 'ai_market_intelligence')


def test_editable_asset_mirrors_source_and_has_no_other_canonical_input(tmp_path):
    manifest = generated_manifest(tmp_path)
    name = 'aidp/workspaces/default/files/core/runtime.py'
    source = tmp_path / name
    source.parent.mkdir(parents=True)
    source.write_text('value = 1\n', encoding='utf-8')
    asset = manifest['assets'][0]
    asset.update(source=name, generated=False, assembly_sources=[],
                 workspace_path=manifest['publication_root'] + '/core/runtime.py')
    write_manifest(tmp_path, manifest)
    assert catalog.load_aidp_manifest(tmp_path, 'ai_market_intelligence') == manifest
    asset['assembly_sources'] = ['aidp/missing.py']
    write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match='another canonical source'):
        catalog.load_aidp_manifest(tmp_path, 'ai_market_intelligence')
    asset['workspace_path'] = manifest['publication_root'] + '/wrong.py'
    write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match='mirror their workspace files path'):
        catalog.load_aidp_manifest(tmp_path, 'ai_market_intelligence')


def test_duplicate_targets_are_rejected_before_the_second_asset_source(tmp_path):
    manifest = generated_manifest(tmp_path)
    manifest['assets'].append({**manifest['assets'][0], 'assembly_sources': None})
    write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match='publication targets'):
        catalog.load_aidp_manifest(tmp_path, 'ai_market_intelligence')


def test_resource_closure_keeps_all_categories_and_validates_objects(tmp_path):
    manifest = generated_manifest(tmp_path)
    for key in ('metadata', 'tables', 'workflows', 'compute', 'agents'):
        name = 'aidp/' + key + '.json'
        (tmp_path / name).write_text('{}', encoding='utf-8')
        if key == 'metadata':
            manifest['model'][key] = name
        elif key == 'tables':
            manifest['model'][key] = [name]
        else:
            manifest[key] = [name]
    write_manifest(tmp_path, manifest)
    loaded = catalog.load_aidp_manifest(tmp_path, 'ai_market_intelligence')
    assert catalog.manifest_sources(loaded) == {
        'aidp/manifest.json', 'aidp/input.py', 'aidp/metadata.json', 'aidp/tables.json',
        'aidp/workflows.json', 'aidp/compute.json', 'aidp/agents.json',
    }
    (tmp_path / 'aidp/agents.json').write_text('[]', encoding='utf-8')
    with pytest.raises(ValueError, match='must be JSON objects'):
        catalog.load_aidp_manifest(tmp_path, 'ai_market_intelligence')
    manifest['compute'] = manifest['agents']
    write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match='Duplicate AIDP resource definitions'):
        catalog.load_aidp_manifest(tmp_path, 'ai_market_intelligence')


def test_resource_category_validation_precedes_resource_file_reads(tmp_path):
    manifest = generated_manifest(tmp_path)
    manifest['model']['tables'] = ['aidp/missing.json']
    del manifest['agents']
    write_manifest(tmp_path, manifest)
    with pytest.raises(ValueError, match='must declare agents'):
        catalog.load_aidp_manifest(tmp_path, 'ai_market_intelligence')


def asset_entry(name, content=b'value = 1\n'):
    return {'file': name, 'sha256': hashlib.sha256(content).hexdigest()}


@pytest.mark.parametrize('folder', [None, 'aidp'])
def test_module_asset_nested_path_and_hash_contract(tmp_path, folder):
    source = tmp_path / 'aidp/core/runtime.py'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'value = 1\n')
    entry = asset_entry('aidp/core/runtime.py')
    assets = catalog._module_assets(tmp_path, [entry], folder)
    assert assets[0].path == source.resolve()
    assert assets[0].read_bytes() == b'value = 1\n'
    with pytest.raises(catalog.PackageError, match='Duplicate global module asset'):
        catalog._module_assets(tmp_path, [entry, entry], folder)
    source.write_bytes(b'drift')
    with pytest.raises(catalog.PackageError, match='SHA-256 mismatch'):
        catalog._module_assets(tmp_path, [entry], folder)


@pytest.mark.parametrize('name', ['../private.py', 'aidp/../private.py', '/absolute.py', 'aidp//code.py'])
def test_module_asset_path_validation_precedes_file_reads(tmp_path, name):
    with pytest.raises(catalog.PackageError, match='Invalid global module asset path'):
        catalog._module_assets(tmp_path, [asset_entry(name)], 'aidp')


def test_module_asset_rejects_private_source_with_original_error_boundary(tmp_path):
    with pytest.raises(catalog.PackageError, match='Missing global module asset') as caught:
        catalog._module_assets(tmp_path, [asset_entry('aidp/deployment.tfvars')], None)
    assert isinstance(caught.value.__cause__, ValueError)
    assert 'Private configuration' in str(caught.value.__cause__)


@pytest.mark.parametrize('external', [False, True])
def test_resolved_symlink_containment_contract_is_preserved(tmp_path, external):
    root = tmp_path / 'package'
    root.mkdir()
    target = (tmp_path if external else root) / 'runtime.py'
    target.write_bytes(b'value = 1\n')
    link = root / 'linked.py'
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f'Symbolic links unavailable: {exc}')
    entry = asset_entry('linked.py')
    if external:
        with pytest.raises(ValueError, match='Missing or external'):
            catalog.source_file(root, 'linked.py')
        with pytest.raises(catalog.PackageError, match='escapes its package'):
            catalog._module_assets(root, [entry], None)
    else:
        assert catalog.source_file(root, 'linked.py') == target.resolve()
        assert catalog._module_assets(root, [entry], None)[0].path == target.resolve()


@pytest.mark.parametrize('private', ['.OCI/config', '.ENV'])
def test_public_alias_cannot_publish_a_private_resolved_target(tmp_path, private):
    target = tmp_path / private
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'fixture-private-data\n')
    alias = tmp_path / 'public.json'
    try:
        alias.symlink_to(target)
    except OSError as exc:
        pytest.skip(f'Symbolic links unavailable: {exc}')
    with pytest.raises(ValueError, match='Private configuration'):
        catalog.source_file(tmp_path, 'public.json')
    with pytest.raises(catalog.PackageError, match='Missing global module asset'):
        catalog._module_assets(tmp_path, [asset_entry('public.json', target.read_bytes())], None)


@pytest.fixture
def distribution_package(tmp_path, monkeypatch):
    generated_manifest(tmp_path)
    metadata = {
        'schema_version': 1, 'lab_id': 'ai_market_intelligence', 'display_name': 'Fixture',
        'pack_version': '1.0.0', 'status': 'planned', 'kind': 'global_module', 'scope': 'global',
        'installation_modes': ['laboratory', 'production'], 'datasets': [], 'notebooks': [],
        'tables': {}, 'runtime_files': [asset_entry('aidp/input.py', (tmp_path / 'aidp/input.py').read_bytes())],
        'source_manifests': [asset_entry('aidp/manifest.json', (tmp_path / 'aidp/manifest.json').read_bytes())],
    }
    metadata['pack_sha256'] = catalog._pack_hash(metadata)
    (tmp_path / 'package').mkdir()
    (tmp_path / 'package/lab.json').write_text(json.dumps(metadata), encoding='utf-8')
    monkeypatch.setattr(catalog, 'package_root', lambda _: tmp_path)
    return tmp_path


def test_distribution_only_reads_the_verified_closure(distribution_package):
    root = distribution_package
    for name in ('.ENV', 'legacy.py', 'private.tfvars', 'terraform.tfstate.backup'):
        (root / 'aidp' / name).write_bytes(b'undeclared fixture data')
    assets = catalog.distribution_assets()
    assert set(assets) == {'package/lab.json', 'aidp/input.py', 'aidp/manifest.json'}
    assert assets['aidp/input.py'] == b'value = 1\n'


@pytest.mark.parametrize('mutated', ['asset', 'metadata'])
def test_distribution_rejects_bytes_changed_after_validation(distribution_package, monkeypatch, mutated):
    root = distribution_package
    original = catalog.load_package
    def load(*args, **kwargs):
        pack = original(*args, **kwargs)
        if mutated == 'asset':
            (root / 'aidp/input.py').write_bytes(b'changed source\n')
        else:
            path = root / 'package/lab.json'
            metadata = json.loads(path.read_bytes())
            metadata['display_name'] = 'Changed fixture'
            metadata['pack_sha256'] = catalog._pack_hash(metadata)
            path.write_text(json.dumps(metadata), encoding='utf-8')
        return pack
    monkeypatch.setattr(catalog, 'load_package', load)
    with pytest.raises(catalog.PackageError, match='changed'):
        catalog.distribution_assets()


@pytest.mark.skipif(shutil.which('uv') is None, reason='The project build tool uv is not installed')
def test_wheel_and_source_archive_exclude_undeclared_and_private_inputs(distribution_package):
    root = distribution_package
    project = Path(__file__).resolve().parents[2]
    (root / 'setup.py').write_bytes((project / 'setup.py').read_bytes())
    (root / 'MANIFEST.in').write_bytes((project / 'MANIFEST.in').read_bytes())
    source = root / 'src/ai_market_intelligence/package.py'
    source.parent.mkdir(parents=True)
    source.write_bytes((project / 'src/ai_market_intelligence/package.py').read_bytes())
    (root / 'pyproject.toml').write_text(
        '[build-system]\nrequires=["setuptools>=75"]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname="fixture-market"\nversion="0.1.0"\nrequires-python=">=3.11"\n'
        '[tool.setuptools.packages.find]\nwhere=["src"]\n', encoding='utf-8')
    secret = b'synthetic-private-build-fixture'
    (root / 'aidp/.env.fixture.json').write_bytes(secret)
    (root / 'aidp/legacy.py').write_bytes(b'undeclared = True\n')
    expected = catalog.distribution_assets()
    output = root / 'dist'
    result = subprocess.run(['uv', 'build', '--offline', '--out-dir', str(output)], cwd=root,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    wheel, = output.glob('*.whl')
    source_archive, = output.glob('*.tar.gz')
    prefix = 'ai_market_intelligence/_assets/'
    with zipfile.ZipFile(wheel) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
        assert {name[len(prefix):]: raw for name, raw in contents.items() if name.startswith(prefix)} == expected
    assert not any(secret in raw or b'.env.fixture' in raw or '.env.fixture' in name
                   or name.endswith('/legacy.py') for name, raw in contents.items())
    with tarfile.open(source_archive) as archive:
        contents = {item.name.split('/', 1)[1]: archive.extractfile(item).read()
                    for item in archive.getmembers() if item.isfile()}
    assert {name: raw for name, raw in contents.items() if name.startswith(('aidp/', 'package/'))} == expected
    assert not any(secret in raw or b'.env.fixture' in raw or b'aidp/legacy.py' in raw
                   or '.env.fixture' in name or name.endswith('/legacy.py') for name, raw in contents.items())
    assert catalog.distribution_assets() == expected


@pytest.fixture
def build_commands(monkeypatch):
    setuptools = ModuleType('setuptools')
    setuptools.setup = lambda **kwargs: None
    command = ModuleType('setuptools.command')
    wheel = ModuleType('setuptools.command.bdist_wheel')
    wheel.bdist_wheel = object
    source = ModuleType('setuptools.command.sdist')
    source.sdist = object
    for name, module in [('setuptools', setuptools), ('setuptools.command', command),
                         ('setuptools.command.bdist_wheel', wheel), ('setuptools.command.sdist', source)]:
        monkeypatch.setitem(sys.modules, name, module)
    project = Path(__file__).resolve().parents[2]
    return runpy.run_path(str(project / 'setup.py'))


@pytest.mark.parametrize('foreign', [None, 'module', 'assets'])
def test_build_validator_cannot_reuse_another_checkout_module(tmp_path, monkeypatch, build_commands, foreign):
    project = Path(__file__).resolve().parents[2]
    original_path = list(sys.path)
    if foreign == 'module':
        monkeypatch.setattr(catalog, '__file__', str(tmp_path / 'foreign/package.py'))
        with pytest.raises(RuntimeError, match='another source checkout'):
            with build_commands['source_package']():
                pytest.fail('A foreign validator must not be used')
    elif foreign == 'assets':
        monkeypatch.setattr(catalog, 'package_root', lambda _: tmp_path)
        with pytest.raises(RuntimeError, match='another source root'):
            with build_commands['source_package']():
                pytest.fail('Foreign assets must not be used')
    else:
        with build_commands['source_package']() as (root, module):
            assert root == project and module is catalog
    assert sys.path == original_path
    assert sys.modules['ai_market_intelligence.package'] is catalog


def test_source_distribution_rejects_an_existing_release_tree(tmp_path, build_commands):
    destination = tmp_path / 'previous-release'
    destination.mkdir()
    stale = destination / 'old.py'
    stale.write_bytes(b'stale = True\n')
    command = build_commands['SourceWithAssets']()
    with pytest.raises(FileExistsError, match='must be fresh'):
        command.make_release_tree(str(destination), [])
    assert stale.read_bytes() == b'stale = True\n'
