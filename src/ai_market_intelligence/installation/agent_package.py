"""Publish maintained modules unchanged, with explicit nonsecret configuration."""

import json
from ai_market_intelligence.package import PackageError, load_package
from ai_market_intelligence.runtime.config import validate_market_config
from ai_market_intelligence.runtime.package_identity import MANIFEST, package_manifest

SOURCE_ROOT = "aidp/workspaces/default/files/agents/"
MODULES = (
    "__init__",
    "runtime_core",
    "media",
    "actor_profiles",
    "contracts",
    "content_items",
    "finance",
    "serp",
    "social",
    "social_search",
    "connectors",
    "model",
    "memory",
    "mcp_payload",
    "provider",
    "actor_tools",
    "research_contracts",
    "reporting",
    "report_analysis",
    "periods",
    "effects",
    "passages",
    "documents",
    "collector",
    "state",
    "artifacts",
    "financial",
    "workflow",
    "runtime_versions",
    "config",
    "services",
    "session",
    "wire",
    "native",
    "factory",
    "model_bridge",
    "package_identity",
)
DECISIONS = ("__init__", "understand", "research", "report", "verify")
FILES = (
    tuple(module + ".py" for module in MODULES)
    + ("mcp_tools.json", "runtime.json")
    + tuple("decisions/" + module + ".py" for module in DECISIONS)
)


def authoring_files():
    return {
        name: raw
        for name, raw in _reviewed_assets().items()
        if name.startswith("core/")
    }


def _reviewed_assets():
    pack = load_package("ai_market_intelligence", require_available=False)
    declared = {SOURCE_ROOT + "core/" + name for name in FILES}
    declared.update(
        {SOURCE_ROOT + "market_intelligence.py", SOURCE_ROOT + "requirements.txt"}
    )
    if (
        len(pack.runtime_files) != len(declared)
        or {asset.name for asset in pack.runtime_files} != declared
    ):
        raise PackageError("Market package must declare every reviewed runtime file")
    return {
        asset.name.removeprefix(SOURCE_ROOT): asset.read_bytes()
        for asset in pack.runtime_files
    }


def validate_agent_config(config):
    validate_market_config(config)
    if any(
        any(
            term in key.lower()
            for term in ("secret", "password", "private_key", "token")
        )
        for key in config
    ):
        raise ValueError("Runtime binding cannot contain credential material")


def agent_package(config):
    validate_agent_config(config)
    files = _reviewed_assets()
    files["core/runtime.json"] = (
        json.dumps(config, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()
    files[MANIFEST] = (
        json.dumps(package_manifest(files), sort_keys=True, indent=2) + "\n"
    ).encode()
    return files.pop("market_intelligence.py"), files
