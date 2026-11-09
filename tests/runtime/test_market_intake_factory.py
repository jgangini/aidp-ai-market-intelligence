"""Intake diagnosis uses production decisions while keeping retrieval disabled."""
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from datetime import datetime, timezone

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from tests.runtime.test_modular_package import stage


def intake_module():
    path = Path(__file__).resolve().parents[2] / 'scripts/market_intake.py'
    spec = importlib.util.spec_from_file_location('market_intake_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_intake_refuses_effects_and_preinitialized_model():
    module = intake_module()
    with pytest.raises(ValueError, match='forbids external'):
        module.build_graph({'external_effects_allowed': True})
    with pytest.raises(ValueError, match='fresh tracked model'):
        module.build_graph({'external_effects_allowed': False, 'model': SimpleNamespace(client=object())})
    with pytest.raises(ValueError, match='no tool was invoked'):
        asyncio.run(module.NoRetrieval().collect(task={'source': 'x'}))


@pytest.mark.parametrize('descriptor_kind', ['prepared', 'installed'])
def test_intake_uses_checkpoint_and_preserves_private_semantic_output(tmp_path, monkeypatch, descriptor_kind):
    source_hash = stage(tmp_path / 'candidate')
    module = intake_module()
    descriptor = {'schema_version': 2, 'candidate_revision': 'test-candidate', 'source_sha256': source_hash}
    if descriptor_kind == 'installed':
        descriptor = {'schema_version': 1, 'layout_version': 2, 'module_id': 'ai_market_intelligence', 'pack_sha256': 'a' * 64}
        monkeypatch.setattr(module, 'load_package', lambda *a, **k: SimpleNamespace(pack_sha256='a' * 64))
    (tmp_path/'candidate/agent-manifest.json').write_text(json.dumps(descriptor))
    binding = {'model_id':'fixture','region':'fixture-region','compartment_id':'fixture',
        'search_context':{'country_code':'CO','language_code':'es'},'enabled_sources':['web']}
    raw = json.dumps(binding).encode()
    (tmp_path/'binding.json').write_bytes(raw)
    (tmp_path/'aidp-agent.toml').write_text(
        '[agent]\nentry="candidate/market_intelligence.py"\n'
        '[intake_test]\nbinding_file="binding.json"\nbinding_sha256="'+hashlib.sha256(raw).hexdigest()+'"\n'
        '[model]\nmodel_id="fixture"\nregion="fixture-region"\ncompartment_id="fixture"\n')
    plugin = ModuleType('aidp_agent_flow.project')
    plugin.contained = lambda root, relative: root / relative
    def private_json(path, value, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
    plugin.private_json = private_json
    monkeypatch.setitem(sys.modules, plugin.__name__, plugin)
    monkeypatch.chdir(tmp_path)
    class Model:
        calls = 0
        client = None
        async def complete(self, system, question):
            self.calls += 1
            assert 'D1' in question
            return json.dumps({'intent':'research','entity':'Tiendas D1','market':'Colombia','period_is_explicit':True,
                'objective':'Conversacion sobre D1','start':'2026-11-01T00:00:00+00:00',
                'end':'2026-11-08T00:00:00+00:00','sources':['web'],
                'clarification':None,'assumptions':[],'retrieval_mode':'conversation'})
    model = Model()
    saver = InMemorySaver()
    graph = module.build_graph({'external_effects_allowed':False,'model':model,
        'checkpointer':saver,'clock':lambda: datetime(2026,11,8,tzinfo=timezone.utc)})
    assert graph.checkpointer is saver
    async def run():
        invocation = {'configurable':{'thread_id':'intake'}}
        output = await graph.ainvoke({'request_id':'intake','question':'que se dice de D1 en Colombia'},
                                    config=invocation, interrupt_before=['plan'])
        assert output['route'] == 'plan'
        assert (await graph.aget_state(invocation)).next == ('plan',)
    asyncio.run(run())
    assert model.calls == 1
    snapshots = list((tmp_path/'.aidp-local/intake').glob('*/snapshot.json'))
    assert len(snapshots) == 1
    result = json.loads(snapshots[0].read_bytes())
    assert result['local_only'] is True and result['native_test_accepted'] is False
    assert result['source_sha256'] == source_hash
    assert result['state']['brief']['entity'] == 'Tiendas D1'
    assert result['state']['plan'] is None


@pytest.mark.parametrize('change', [{'pack_sha256': 'b' * 64}, {'layout_version': 1}, {'module_id': 'foreign'}])
def test_installed_candidate_descriptor_rejects_foreign_package_or_layout(tmp_path, monkeypatch, change):
    stage(tmp_path / 'candidate')
    module = intake_module()
    monkeypatch.setattr(module, 'load_package', lambda *a, **k: SimpleNamespace(pack_sha256='a' * 64))
    descriptor = {'schema_version': 1, 'layout_version': 2, 'module_id': 'ai_market_intelligence', 'pack_sha256': 'a' * 64}
    (tmp_path / 'candidate/agent-manifest.json').write_text(json.dumps({**descriptor, **change}))
    with pytest.raises(ValueError):
        module.candidate_metadata(tmp_path / 'candidate/market_intelligence.py')


def test_local_factory_fingerprint_is_checked_before_binding_or_model_use(tmp_path, monkeypatch):
    module = intake_module()
    (tmp_path / 'aidp-agent.toml').write_text('[intake_test]\nfactory_sha256="stale"\n')
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match='factory fingerprint'):
        module.build_graph({'external_effects_allowed': False, 'model': SimpleNamespace(client=None)})


def test_capture_replay_preserves_scope_and_discloses_frozen_sources():
    from ai_market_intelligence.runtime.research_contracts import MarketBrief

    brief = MarketBrief(intent='research', entity='D1', market='Colombia', objective='Conversación',
        start='2026-11-01T00:00:00+00:00', end='2026-11-08T00:00:00+00:00',
        sources=['x'], clarification=None, assumptions=[], retrieval_mode='conversation').model_dump(mode='json')
    row = {'evidence_id': 'post-1', 'source': 'x', 'discovery_channel': 'x',
        'url': 'https://x.com/example/status/123', 'text': 'Publicación capturada de prueba.',
        'published_at': '2026-11-07T00:00:00+00:00', 'observed_at': '2026-11-08T00:00:00+00:00',
        'receipt_id': 'captured-run', 'content_basis': 'post_text'}
    cls = intake_module().CapturedRetrieval
    with pytest.raises(ValueError, match='positive capture'):
        cls({'brief': brief, 'evidence': []})
    collector = cls({'brief': brief, 'evidence': [row]})
    arguments = {'brief': brief, 'task': {'source': 'x', 'urls': []}, 'operation_key': 'replay'}
    result = asyncio.run(collector.collect(**arguments))
    assert result['evidence'][0]['receipt_id'] == 'captured-run'
    assert 'congelados' in result['limitations'][0]
    with pytest.raises(ValueError, match='scope differs'):
        asyncio.run(collector.collect(**{**arguments, 'brief': {**brief, 'market': 'México'}}))
    with pytest.raises(ValueError, match='scope differs'):
        asyncio.run(collector.collect(**{**arguments, 'brief': {**brief, 'entity': 'Ara'}}))
    with pytest.raises(ValueError, match='scope differs'):
        asyncio.run(collector.collect(**{**arguments, 'brief': {**brief, 'entity': 'Tiendas D1'}}))
    reviewed = cls({'brief': brief, 'evidence': [row], 'entity_aliases': ['D1', 'Tiendas D1']})
    result = asyncio.run(reviewed.collect(**{**arguments, 'brief': {**brief, 'entity': 'Tiendas D1'}}))
    assert result['evidence'][0]['receipt_id'] == 'captured-run'
    for field, changed in [('entity', 'Ara'), ('market', 'México'), ('sources', ['web']),
                           ('end', '2026-11-09T00:00:00+00:00'), ('retrieval_mode', 'documentary')]:
        with pytest.raises(ValueError, match='scope differs'):
            asyncio.run(reviewed.collect(**{**arguments, 'brief': {**brief, field: changed}}))
    for aliases in [[], ['Ara'], 'D1', ['D1', ''], ['D1', 1]]:
        with pytest.raises(ValueError, match='aliases'):
            cls({'brief': brief, 'evidence': [row], 'entity_aliases': aliases})
    multisource = {**brief, 'sources': ['x', 'youtube']}
    collector = cls({'brief': multisource, 'evidence': [row]})
    result = asyncio.run(collector.collect(**{**arguments, 'brief': {**multisource, 'sources': ['youtube', 'x']}}))
    assert result['evidence'][0]['receipt_id'] == 'captured-run'
    for sources in [['x'], ['youtube'], ['x', 'youtube', 'web']]:
        with pytest.raises(ValueError, match='scope differs'):
            asyncio.run(collector.collect(**{**arguments, 'brief': {**multisource, 'sources': sources}}))
