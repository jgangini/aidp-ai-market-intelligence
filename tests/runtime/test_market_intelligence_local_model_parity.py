"""A local factory cannot infer through another candidate's provider or model."""

import copy
import hashlib
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from ai_market_intelligence.runtime import factory, model_bridge
from ai_market_intelligence.runtime.config import LLM_CREDENTIAL_NAME, OPENAI_ENDPOINT
from ai_market_intelligence.runtime.package_identity import MANIFEST, directory_identity, package_manifest
from tests.runtime.test_market_intake_factory import intake_module
from tests.runtime.test_market_intelligence_research_runtime import market_active_config


def configured(mode="oci_iam", model="chosen-model-alpha"):
    config = market_active_config()
    config["services"].update(model_id=model, guardrails_config={"policies": []})
    if mode == "openai":
        config["schema_version"] = 2
        config["services"].update(llm_auth_mode=mode, model_endpoint=OPENAI_ENDPOINT,
                                   llm_credential_name=LLM_CREDENTIAL_NAME)
    return config


def local_settings(binding):
    return {**{key: binding[key] for key in ("model_id", "region", "compartment_id")},
            "llm_auth_mode": binding.get("llm_auth_mode", "oci_iam")}


def candidate_runtime(tmp_path, config):
    entry = tmp_path / "candidate/market_intelligence.py"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("# Local parity fixture\n", encoding="utf-8")
    runtime = entry.parent / "core/runtime.json"
    runtime.parent.mkdir()
    raw = json.dumps(config).encode()
    runtime.write_bytes(raw)
    return entry, {"core/runtime.json": hashlib.sha256(raw).hexdigest()}


@pytest.mark.parametrize("mode", ["oci_iam", "openai"])
@pytest.mark.parametrize("model", ["chosen-model-alpha", "chosen-model-beta"])
def test_complete_candidate_accepts_the_arbitrary_selected_model_without_secrets(tmp_path, mode, model):
    config = configured(mode, model)
    binding = config["services"]
    settings = local_settings(binding)
    original = copy.deepcopy((config, binding, settings))
    entry, inventory = candidate_runtime(tmp_path, config)
    selected = model_bridge.validate_local_model_selection(settings, binding, entry, inventory,
                                                           tracked_settings=settings)
    assert selected["model_id"] == model and selected["llm_auth_mode"] == mode
    assert (config, binding, settings) == original
    assert not any("api_key" in key or "secret" in key for key in selected)


@pytest.mark.parametrize("native_mode,local_mode,model", [
    ("openai", "oci_iam", "another-model"),
    ("oci_iam", "openai", "another-model"),
    ("openai", "openai", "another-model"),
    ("oci_iam", "oci_iam", "another-model"),
])
def test_coherent_local_binding_cannot_override_the_candidate_selection(tmp_path, native_mode, local_mode, model):
    entry, inventory = candidate_runtime(tmp_path, configured(native_mode))
    binding = configured(local_mode, model)["services"]
    with pytest.raises(ValueError, match="candidate model selection"):
        model_bridge.validate_local_model_selection(local_settings(binding), binding, entry, inventory)


@pytest.mark.parametrize("field", ["model_endpoint", "llm_credential_name"])
def test_explicit_endpoint_or_credential_changes_fail_before_any_inference(tmp_path, field):
    config = configured("openai")
    binding = config["services"]
    entry, inventory = candidate_runtime(tmp_path, config)
    settings = {**local_settings(binding), field: "different-selection"}
    with pytest.raises(ValueError, match="selection differs"):
        model_bridge.validate_local_model_selection(settings, binding, entry, inventory)


def test_openai_mode_must_be_explicit_and_effective_tracker_model_cannot_differ(tmp_path):
    config = configured("openai")
    binding = config["services"]
    entry, inventory = candidate_runtime(tmp_path, config)
    settings = local_settings(binding)
    with pytest.raises(ValueError, match="authentication modes"):
        model_bridge.validate_local_model_selection({key: value for key, value in settings.items() if key != "llm_auth_mode"},
                                                    binding, entry, inventory)
    with pytest.raises(ValueError, match="model_id"):
        model_bridge.validate_local_model_selection(settings, binding, entry, inventory,
                                                    tracked_settings={**settings, "model_id": "different-runtime-model"})


def test_unbound_paused_candidate_and_replaced_runtime_bytes_cannot_attest_parity(tmp_path):
    entry, inventory = candidate_runtime(tmp_path, {"schema_version": 1, "research_enabled": False})
    binding = configured()["services"]
    with pytest.raises(ValueError, match="complete model binding"):
        model_bridge.validate_local_model_selection(local_settings(binding), binding, entry, inventory)
    (entry.parent / "core/runtime.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="changed before local evaluation"):
        model_bridge.validate_local_model_selection(local_settings(binding), binding, entry, inventory)
    with pytest.raises(ValueError, match="declared runtime"):
        model_bridge.validate_local_model_selection(local_settings(binding), binding, entry, {})


def prepare_factory(tmp_path, monkeypatch, name, native_mode, local_mode, model):
    config = configured(native_mode)
    entry, _ = candidate_runtime(tmp_path, config)
    files = {"market_intelligence.py": entry.read_bytes(), "requirements.txt": b"# Fixture\n",
             "core/runtime.json": (entry.parent / "core/runtime.json").read_bytes()}
    for module in (factory, model_bridge):
        path = Path(module.__file__)
        files["core/" + path.name] = path.read_bytes()
    for relative, raw in files.items():
        (entry.parent / relative).write_bytes(raw)
    (entry.parent / MANIFEST).write_text(json.dumps(package_manifest(files)), encoding="utf-8")
    revision = "b" * 64
    (entry.parent / "agent-manifest.json").write_text(json.dumps({"schema_version": 2,
        "candidate_revision": revision, "source_sha256": directory_identity(entry.parent)}), encoding="utf-8")
    binding = configured(local_mode, model)["services"]
    raw = json.dumps(binding).encode()
    (tmp_path / "binding.json").write_bytes(raw)
    (tmp_path / "snapshot.json").write_bytes(b"{}")
    module = intake_module() if name == "intake" else factory
    section = "intake_test" if name == "intake" else "market_test"
    options = {"binding_file": "binding.json", "binding_sha256": hashlib.sha256(raw).hexdigest(),
               "factory_sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()}
    if name == "frozen":
        options.update(adapter_sha256=hashlib.sha256(Path(model_bridge.__file__).read_bytes()).hexdigest(),
                       candidate_revision=revision, snapshot_file="snapshot.json",
                       snapshot_sha256=hashlib.sha256(b"{}").hexdigest())
        monkeypatch.setattr(factory, "exact_runtime_comparison", lambda *args: {"exact_match": True})
        monkeypatch.setattr(factory, "market_evaluation_graph", lambda *args, **kwargs: ("graph", None, None))
    else:
        project = ModuleType("aidp_agent_flow.project")
        project.contained = lambda root, relative: root / relative
        project.private_json = lambda *args, **kwargs: pytest.fail("Building the factory cannot write a research snapshot")
        import sys
        monkeypatch.setitem(sys.modules, project.__name__, project)
    sections = {"agent": {"entry": "candidate/market_intelligence.py"}, section: options,
                "model": local_settings(binding)}
    (tmp_path / "aidp-agent.toml").write_text("\n".join(
        "[" + key + "]\n" + "\n".join(field + "=" + json.dumps(value) for field, value in values.items())
        for key, values in sections.items()), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    tracked = SimpleNamespace(client=None, calls=0, settings=local_settings(binding))
    return module, tracked


@pytest.mark.parametrize("name", ["intake", "frozen"])
@pytest.mark.parametrize("native_mode,local_mode,model", [
    ("openai", "oci_iam", "different-model"),
    ("oci_iam", "openai", "different-model"),
    ("openai", "openai", "different-model"),
])
def test_both_factories_refuse_another_selection_before_adapter_or_model(tmp_path, monkeypatch, name, native_mode, local_mode, model):
    module, tracked = prepare_factory(tmp_path, monkeypatch, name, native_mode, local_mode, model)
    monkeypatch.setattr(module, "StructuredBridge", lambda *args, **kwargs: pytest.fail("Mismatched selection reached the model adapter"))
    with pytest.raises(ValueError, match="candidate model selection"):
        module.build_graph({"external_effects_allowed": False, "model": tracked})
    assert tracked.calls == 0


@pytest.mark.parametrize("name", ["intake", "frozen"])
@pytest.mark.parametrize("mode", ["oci_iam", "openai"])
def test_both_factories_build_with_the_selected_generic_provider(tmp_path, monkeypatch, name, mode):
    module, tracked = prepare_factory(tmp_path, monkeypatch, name, mode, mode, "chosen-model-alpha")
    graph = module.build_graph({"external_effects_allowed": False, "model": tracked,
                               "clock": lambda: None, "checkpointer": None})
    assert graph is not None and tracked.calls == 0
