"""Deterministic identity for the complete executable package, excluding its manifest."""

import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

MANIFEST = "package-manifest.json"
LAYOUT_VERSION = 2


def file_hashes(files):
    return {
        name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())
    }


def inventory_digest(hashes):
    return hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_inventory(hashes):
    if (
        not isinstance(hashes, dict)
        or not {"market_intelligence.py", "requirements.txt"} <= hashes.keys()
    ):
        raise ValueError("The executable package inventory is incomplete")
    for name, digest in hashes.items():
        if not isinstance(name, str):
            raise ValueError("Invalid executable package path")
        path = PurePosixPath(name)
        if (
            str(path) != name
            or path.is_absolute()
            or ".." in path.parts
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
            or not (
                name in {"market_intelligence.py", "requirements.txt", MANIFEST}
                or name.startswith("core/")
                and path.suffix in {".py", ".json"}
            )
            or any(part.startswith(".") for part in path.parts)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("Invalid executable package inventory")
    return hashes


def package_manifest(files):
    hashes = validate_inventory(file_hashes(files))
    if MANIFEST in hashes:
        raise ValueError("The package manifest cannot hash itself")
    return {"schema_version": 1, "layout_version": LAYOUT_VERSION, "files": hashes}


def source_identity(files):
    """Read historical entry identities or verify a complete modular candidate."""
    if MANIFEST not in files:
        return hashlib.sha256(files["market_intelligence.py"]).hexdigest()
    manifest = json.loads(files[MANIFEST])
    expected = package_manifest(
        {name: raw for name, raw in files.items() if name != MANIFEST}
    )
    if manifest != expected:
        raise ValueError("The executable package differs from its manifest")
    return inventory_digest(expected["files"])


def directory_identity(root):
    """Validate a staged candidate without trusting manifest paths or importing it."""
    root = Path(root).resolve()
    manifest = json.loads((root / MANIFEST).read_bytes())
    if (
        manifest.get("schema_version") != 1
        or manifest.get("layout_version") != LAYOUT_VERSION
    ):
        raise ValueError("Unsupported executable package manifest")
    hashes = validate_inventory(manifest.get("files"))
    if MANIFEST in hashes:
        raise ValueError("The package manifest cannot hash itself")
    actual = {}
    for name in hashes:
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Executable resource escaped its package")
        actual[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != hashes:
        raise ValueError("Executable package bytes changed")
    return inventory_digest(hashes)


def installed_identity():
    """Bind the probe to package bytes and to the modules actually imported."""
    root = Path(__file__).resolve().parent.parent
    identity = directory_identity(root)
    hashes = json.loads((root / MANIFEST).read_bytes())["files"]
    package = __package__
    for name, module in tuple(sys.modules.items()):
        if name == package or name.startswith(package + "."):
            origin = getattr(module, "__file__", None)
            if origin is None or not Path(origin).resolve().is_relative_to(
                root / "core"
            ):
                raise ValueError(
                    "Runtime imported a module outside the executable package"
                )
            if Path(origin).resolve().relative_to(root).as_posix() not in hashes:
                raise ValueError("Runtime imported an undeclared module")
    return identity
