"""Load only the staged AIDP entry and modules for native adapter tests."""
import importlib.util
import sys

from ai_market_intelligence.installation.agent_package import agent_package


def load_candidate(directory, monkeypatch, config):
    source, assets = agent_package(config)
    for name, raw in {'market_intelligence.py': source, **assets}.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    for name in tuple(sys.modules):
        if name == 'core' or name.startswith('core.'):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location('packaged_market_entry', directory / 'market_intelligence.py')
    entry = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, entry)
    spec.loader.exec_module(entry)
    # Register imported module removals with monkeypatch's normal teardown.
    for name in tuple(sys.modules):
        if name == 'core' or name.startswith('core.'):
            module = sys.modules.pop(name)
            monkeypatch.setitem(sys.modules, name, module)
    return entry
