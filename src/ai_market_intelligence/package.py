"""Validate the single immutable Market package and its AIDP source closure.

Adapted from catalog.py and aidp.py at source commit 3c82617fd298933995e954955841007253cc0985.
"""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath
from typing import Any

STANDARD_MODULES = frozenset({'ai_market_intelligence'})
PACK_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")
MEDALLION_LAYERS = frozenset({'landing', 'bronze', 'silver', 'gold'})
PRIVATE_SOURCE_PARTS = frozenset({'.oci', '.aws', '.local', '.aidp-local', '.codex', '__pycache__'})
PRIVATE_SOURCE_SUFFIXES = frozenset({'.pem', '.key', '.p12', '.tfstate', '.tfvars', '.pyc', '.pyo'})

def package_root(package_id: str) -> Path:
    if package_id != 'ai_market_intelligence':
        raise PackageError('Unsupported package_id')
    installed = Path(__file__).resolve().parent / '_assets'
    if (installed / 'aidp/manifest.json').is_file():
        return installed
    return Path(__file__).resolve().parents[2]

def package_metadata_path(package_id: str) -> Path:
    return package_root(package_id) / 'package/lab.json'


def private_source_path(path: PurePath) -> bool:
    name = path.name.casefold()
    return (any(part.casefold() in PRIVATE_SOURCE_PARTS for part in path.parts)
            or name.startswith('.env') or path.suffix.casefold() in PRIVATE_SOURCE_SUFFIXES
            or '.tfstate' in name)


def source_file(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("Invalid AIDP source path")
    relative = PurePosixPath(name)
    if relative.is_absolute() or relative.as_posix() != name or any(part in {".", ".."} for part in relative.parts):
        raise ValueError("AIDP source path must stay inside its module")
    if private_source_path(relative):
        raise ValueError("Private configuration cannot be an AIDP publication source")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Missing or external AIDP source: {name}")
    if private_source_path(path.relative_to(root.resolve())):
        raise ValueError("Private configuration cannot be an AIDP publication source")
    return path


def _publication_root(manifest: Any, module_id: str) -> str:
    publication_root = "/Workspace/.artifact/" + module_id
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("module_id") != module_id or module_id not in STANDARD_MODULES
            or manifest.get("workspace_id") != "default" or manifest.get("publication_root") != publication_root):
        raise ValueError("Invalid AIDP module identity or publication root")
    return publication_root


def _publication_target(asset: Any, publication_root: str, targets: set[str]) -> str:
    if not isinstance(asset, dict):
        raise ValueError("Invalid AIDP publication asset")
    target = asset.get("workspace_path")
    if (not isinstance(target, str) or not target.startswith(publication_root + "/")
            or "\\" in target or PurePosixPath(target).as_posix() != target
            or ".." in PurePosixPath(target).parts or target in targets):
        raise ValueError("AIDP publication targets must be unique and inside their module root")
    targets.add(target)
    return target


def _editable_source(root: Path, asset: dict, publication_root: str, target: str) -> None:
    source = asset.get("source")
    source_file(root, source)
    files_root = "aidp/workspaces/default/files/"
    if not source.startswith(files_root) or target != publication_root + "/" + source.removeprefix(files_root):
        raise ValueError("Editable AIDP assets must mirror their workspace files path")
    if asset["assembly_sources"]:
        raise ValueError("Editable AIDP assets cannot have another canonical source")


def _publication_sources(root: Path, asset: dict, publication_root: str, target: str) -> None:
    if asset.get("kind") not in {"python", "notebook", "text", "json", "data"} or type(asset.get("generated")) is not bool:
        raise ValueError("Invalid AIDP asset kind or generation contract")
    assembly = asset.get("assembly_sources")
    if not isinstance(assembly, list) or any(not isinstance(name, str) for name in assembly) or len(assembly) != len(set(assembly)):
        raise ValueError("Invalid AIDP assembly source closure")
    if asset["generated"]:
        if asset.get("source") is not None or not assembly:
            raise ValueError("Generated AIDP assets need an explicit source closure")
    else:
        _editable_source(root, asset, publication_root, target)
    for name in assembly:
        source_file(root, name)


def _resource_definitions(manifest: dict) -> list[str]:
    model = manifest.get("model")
    if not isinstance(model, dict) or not isinstance(model.get("tables"), list):
        raise ValueError("Invalid AIDP data model contract")
    definitions = ([model["metadata"]] if model.get("metadata") is not None else []) + model["tables"]
    for key in ("workflows", "compute", "agents"):
        values = manifest.get(key)
        if not isinstance(values, list):
            raise ValueError(f"AIDP module must declare {key}")
        definitions.extend(values)
    if any(not isinstance(name, str) for name in definitions) or len(definitions) != len(set(definitions)):
        raise ValueError("Duplicate AIDP resource definitions")
    return definitions


def load_aidp_manifest(root: Path, module_id: str) -> dict:
    manifest = json.loads(source_file(root, "aidp/manifest.json").read_text(encoding="utf-8"))
    publication_root = _publication_root(manifest, module_id)
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("An AIDP module must declare its publication assets")
    targets = set()
    for asset in assets:
        target = _publication_target(asset, publication_root, targets)
        _publication_sources(root, asset, publication_root, target)
    for name in _resource_definitions(manifest):
        if not isinstance(json.loads(source_file(root, name).read_text(encoding="utf-8")), dict):
            raise ValueError("AIDP resource definitions must be JSON objects")
    return manifest


def manifest_sources(manifest: dict) -> set[str]:
    """Return every input whose bytes must be covered by package hashes."""
    sources = {"aidp/manifest.json", *manifest["model"]["tables"]}
    if manifest["model"].get("metadata"):
        sources.add(manifest["model"]["metadata"])
    for key in ("workflows", "compute", "agents"):
        sources.update(manifest[key])
    for asset in manifest["assets"]:
        if asset.get("source"):
            sources.add(asset["source"])
        sources.update(asset["assembly_sources"])
    return sources


class PackageError(ValueError):
    """Raised when a checked-in lab package violates its contract."""

@dataclass(frozen=True)
class PackageAsset:
    name: str
    path: Path
    sha256: str
    row_count: int | None = None
    task_key: str | None = None
    depends_on: tuple[str, ...] = ()

    def read_bytes(self) -> bytes:
        content = self.path.read_bytes()
        if _sha256(content) != self.sha256:
            raise PackageError(f"SHA-256 changed while reading {self.name}")
        return content

@dataclass(frozen=True)
class Package:
    lab_id: str
    display_name: str
    pack_version: str
    status: str
    kind: str
    scope: str
    installation_modes: tuple[str, ...]
    pack_sha256: str
    datasets: tuple[PackageAsset, ...]
    notebooks: tuple[PackageAsset, ...]
    tables: dict[str, tuple[str, ...]]
    formats: dict[str, str]
    expected_results: dict[str, Any]
    agent: dict[str, Any]
    runtime_files: tuple[PackageAsset, ...] = ()
    source_manifests: tuple[PackageAsset, ...] = ()

    @property
    def available(self) -> bool:
        return self.status == "available"

def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"Invalid lab metadata: {path.name}") from exc
    if not isinstance(value, dict):
        raise PackageError(f"Lab metadata must be an object: {path.name}")
    return value


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def _pack_hash(metadata: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in metadata.items() if key != "pack_sha256"}
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(canonical)

def _pack_contract(
    metadata: dict[str, Any], lab_id: str, require_available: bool
) -> tuple[str, str, str, str]:
    if metadata.get("schema_version") != 1 or metadata.get("lab_id") != lab_id:
        raise PackageError(f"Invalid lab identity: {lab_id}")
    status = str(metadata.get("status") or "")
    if status not in {"available", "planned"}:
        raise PackageError(f"Invalid lab status: {lab_id}")
    if require_available and status != "available":
        raise PackageError(f"Lab {lab_id} is not available yet")
    kind = str(metadata.get("kind") or "data_pipeline")
    if kind not in {"data_pipeline", "global_module"}:
        raise PackageError(f"Invalid lab kind: {lab_id}")
    pack_version = str(metadata.get("pack_version") or "")
    if PACK_VERSION_PATTERN.fullmatch(pack_version) is None:
        raise PackageError(f"Invalid pack version: {lab_id}")
    digest = str(metadata.get("pack_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or digest != _pack_hash(metadata):
        raise PackageError(f"Pack hash mismatch: {lab_id}")
    return status, kind, pack_version, digest

def _module_asset_path(root: Path, raw: Any, folder: str | None) -> tuple[str, Path]:
    if not isinstance(raw, dict):
        raise PackageError("Invalid global module asset")
    name = str(raw.get("file") or "")
    relative = PurePosixPath(name)
    prefix, pattern = folder + "/" if folder else "", r"[A-Za-z0-9_./-]+"
    if (not name.startswith(prefix) or re.fullmatch(pattern, name) is None or ".." in relative.parts
            or relative.as_posix() != name):
        raise PackageError("Invalid global module asset path")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise PackageError("Global module asset escapes its package")
    return name, path


def _module_asset(root: Path, raw: Any, folder: str | None) -> PackageAsset:
    name, path = _module_asset_path(root, raw, folder)
    try:
        if folder is None:
            path = source_file(root, name)
        content = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise PackageError(f"Missing global module asset: {name}") from exc
    digest = str(raw.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or _sha256(content) != digest:
        raise PackageError(f"SHA-256 mismatch for {name}")
    return PackageAsset(name, path, digest)


def _module_assets(root: Path, entries: Any, folder: str | None) -> tuple[PackageAsset, ...]:
    """Verify the exact files published by a global module, including nested paths."""
    if not isinstance(entries, list):
        raise PackageError("Global module assets must be a list")
    assets = tuple(_module_asset(root, raw, folder) for raw in entries)
    if len({asset.name for asset in assets}) != len(assets):
        raise PackageError("Duplicate global module asset")
    return assets


def _pack_expected_results(
    metadata: dict[str, Any], status: str, kind: str, lab_id: str
) -> dict[str, Any]:
    expected = metadata.get("expected_results", {})
    if not isinstance(expected, dict) or (
        status == "available" and kind == "data_pipeline"
        and set(expected)
        != {"source_row_counts", "business_aggregates", "quality", "lineage"}
    ):
        raise PackageError(f"Invalid expected results: {lab_id}")
    return expected

def _pack_formats(
    metadata: dict[str, Any], status: str, kind: str, lab_id: str
) -> dict[str, str]:
    raw_formats = metadata.get("formats", {})
    if not isinstance(raw_formats, dict):
        raise PackageError(f"Invalid table formats: {lab_id}")
    if not raw_formats:
        return {}
    formats = {str(layer): str(value).upper() for layer, value in raw_formats.items()}
    if (
        status == "available" and kind == "data_pipeline"
        and set(formats) != MEDALLION_LAYERS
        or any(value not in {"CSV", "DELTA", "ICEBERG"} for value in formats.values())
    ):
        raise PackageError(f"Invalid table formats: {lab_id}")
    return formats
def load_package(lab_id: str = 'ai_market_intelligence', *, require_available: bool = True) -> Package:
    root = package_root(lab_id)
    metadata = _object(package_metadata_path(lab_id))
    status, kind, version, digest = _pack_contract(metadata, lab_id, require_available)
    if kind != 'global_module' or metadata.get('scope') != 'global':
        raise PackageError('Market requires a global module package')
    modes = tuple(metadata.get('installation_modes', []))
    if modes != ('laboratory', 'production'):
        raise PackageError('Invalid global module installation modes')
    if metadata.get('datasets') or metadata.get('notebooks') or metadata.get('tables'):
        raise PackageError('Market does not declare datasets, notebooks or business tables')
    try:
        manifest = load_aidp_manifest(root, lab_id)
    except (ValueError, OSError, TypeError) as exc:
        raise PackageError(str(exc)) from exc
    runtime = _module_assets(root, metadata.get('runtime_files', []), None)
    manifests = _module_assets(root, metadata.get('source_manifests', []), None)
    if not manifest_sources(manifest).issubset({asset.name for asset in (*runtime, *manifests)}):
        raise PackageError('AIDP publication inputs must all have package hashes')
    agent = metadata.get('agent', {})
    if not isinstance(agent, dict):
        raise PackageError('Invalid agent metadata')
    return Package(lab_id=lab_id, display_name=str(metadata.get('display_name', lab_id)),
        pack_version=version, status=status, kind=kind, scope='global', installation_modes=modes,
        pack_sha256=digest, datasets=(), notebooks=(), tables={},
        formats=_pack_formats(metadata,status,kind,lab_id),
        expected_results=_pack_expected_results(metadata,status,kind,lab_id), agent=agent,
        runtime_files=runtime, source_manifests=manifests)


def distribution_assets() -> dict[str, bytes]:
    """Read only the verified package closure, excluding undeclared build inputs."""
    pack = load_package('ai_market_intelligence', require_available=False)
    assets = {asset.name: asset.read_bytes() for asset in (*pack.runtime_files, *pack.source_manifests)}
    try:
        raw = package_metadata_path(pack.lab_id).read_bytes()
        metadata = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise PackageError('Invalid package metadata during distribution') from exc
    if not isinstance(metadata, dict) or _pack_contract(metadata, pack.lab_id, False)[3] != pack.pack_sha256:
        raise PackageError('Package metadata changed during distribution')
    return {'package/lab.json': raw, **assets}
