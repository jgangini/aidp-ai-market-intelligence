"""Local graph diagnosis with optional captured evidence; no live retrieval."""

import hashlib
import json
from pathlib import Path
import tomllib
from uuid import uuid4

from ai_market_intelligence.package import load_package
from ai_market_intelligence.runtime.factory import (
    MarketEvaluationSnapshot, MarketEvaluationStore, MarketFrozenCollector, market_checked_json,
)
from ai_market_intelligence.runtime.research_contracts import MarketBrief, MarketEvidence
from ai_market_intelligence.runtime.model import DecisionModel
from ai_market_intelligence.runtime.model_bridge import StructuredBridge
from ai_market_intelligence.runtime.package_identity import directory_identity, MANIFEST
from ai_market_intelligence.runtime.runtime_core import Scope
from ai_market_intelligence.runtime.runtime_versions import runtime_versions
from ai_market_intelligence.runtime.state import MarketRunStore
from ai_market_intelligence.runtime.workflow import MarketWorkflow


class NoRetrieval:
    async def collect(self, **arguments):
        raise ValueError('Intake diagnosis must stop before collection; no tool was invoked')


class CapturedRetrieval(MarketFrozenCollector):
    def __init__(self, captured):
        self.brief = MarketBrief.model_validate(captured['brief']).model_dump(mode='json')
        aliases = captured.get('entity_aliases', [self.brief['entity']])
        if (not isinstance(aliases, list) or not 1 <= len(aliases) <= 10
                or any(not isinstance(name, str) or not name.strip() or len(name) > 256 for name in aliases)
                or self.brief['entity'] not in aliases):
            raise ValueError('Capture aliases must explicitly include the original entity')
        self.entity_aliases = frozenset(aliases)
        rows = [MarketEvidence.model_validate(row).model_dump(mode='json')
                for row in captured['evidence']]
        if not rows or any(row['source'] not in self.brief['sources'] for row in rows):
            raise ValueError('A positive capture within the selected sources is required')
        super().__init__(rows)

    async def collect(self, **arguments):
        fields = ('market', 'start', 'end', 'retrieval_mode')
        if (arguments['brief'].get('entity') not in self.entity_aliases
                or set(arguments['brief'].get('sources', [])) != set(self.brief['sources'])
                or any(arguments['brief'].get(key) != self.brief.get(key) for key in fields)):
            raise ValueError('The new research scope differs from the captured evidence')
        return await super().collect(**arguments)


def candidate_metadata(entry):
    source_hash = directory_identity(entry.parent)
    manifest = json.loads(entry.with_name('agent-manifest.json').read_bytes())
    if manifest.get('schema_version') == 2:
        if manifest.get('source_sha256') != source_hash:
            raise ValueError('Prepared candidate changed')
        revision = manifest.get('candidate_revision')
    elif (manifest.get('schema_version') == 1 and manifest.get('layout_version') == 2
          and manifest.get('module_id') == 'ai_market_intelligence'):
        revision = manifest.get('pack_sha256')
        if revision != load_package('ai_market_intelligence', require_available=False).pack_sha256:
            raise ValueError('Installed candidate differs from the canonical agent package')
    else:
        raise ValueError('Unsupported prepared or installed candidate descriptor')
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError('Candidate revision is required')
    return {'source_sha256': source_hash, 'candidate_revision': revision}


def build_graph(runtime):
    if runtime['external_effects_allowed'] is not False:
        raise ValueError('Intake diagnosis forbids external tools')
    if getattr(runtime['model'], 'client', None) is not None:
        raise ValueError('Use a fresh tracked model for identity validation')
    root = Path.cwd()
    config = tomllib.loads((root / 'aidp-agent.toml').read_text(encoding='utf-8'))
    options = config['intake_test']
    if ('factory_sha256' in options
            and options['factory_sha256'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()):
        raise ValueError('Local test factory fingerprint changed')
    from aidp_agent_flow.project import contained, private_json

    binding = market_checked_json(root, options['binding_file'], options['binding_sha256'])
    entry = contained(root, config['agent']['entry'])
    manifest = candidate_metadata(entry)
    import ai_market_intelligence.runtime.factory as portable_factory
    source_root = Path(portable_factory.__file__).parent
    inventory = json.loads(entry.with_name(MANIFEST).read_bytes())['files']
    for name, expected in inventory.items():
        if name.startswith('core/') and name != 'core/runtime.json':
            module = source_root / name.removeprefix('core/')
            if hashlib.sha256(module.read_bytes()).hexdigest() != expected:
                raise ValueError('Local modules differ from the candidate')
    for key in ('model_id', 'region', 'compartment_id'):
        if config['model'][key] != binding[key]:
            raise ValueError('Selected model binding changed')
    run = root / '.aidp-local/intake' / uuid4().hex

    class Snapshot(MarketEvaluationSnapshot):
        def write(self, value, etag):
            super().write(value, etag)
            private_json(
                run / 'snapshot.json',
                {
                    'local_only': True,
                    'native_test_accepted': False,
                    'runtime_profile': runtime_versions(),
                    'source_sha256': manifest['source_sha256'],
                    'state': self.value,
                },
                overwrite=True,
            )
            from ai_market_intelligence.runtime.wire import market_event_packet, market_wire_line
            from datetime import datetime, timezone

            packets = [market_event_packet(store, event) for event in self.value['events']
                       if event['sequence'] > getattr(self, 'public_cursor', 0)]
            if self.value.get('response') and not getattr(self, 'public_response_written', False):
                packets.append(market_wire_line('response', request_id=self.value['request_id'],
                                                response=self.value['response']))
            for packet in packets:
                with (run / 'public.jsonl').open('a', encoding='utf-8') as output:
                    output.write(packet)
                with (run / 'public-arrivals.jsonl').open('a', encoding='utf-8') as arrivals:
                    arrivals.write(json.dumps({'received_at': datetime.now(timezone.utc).isoformat()}) + '\n')
            self.public_cursor = self.value['next_sequence'] - 1
            self.public_response_written = bool(self.value.get('response'))

    snapshot = Snapshot(Scope('local-development', 'local-operator', str(uuid4())))
    collector = NoRetrieval()
    if 'capture_file' in options:
        captured = market_checked_json(root, options['capture_file'], options['capture_sha256'])
        collector = CapturedRetrieval(captured)
        store = MarketEvaluationStore(snapshot, captured['question'], manifest['candidate_revision'])
    else:
        store = MarketRunStore(snapshot, manifest['candidate_revision'])
    model = DecisionModel(
        StructuredBridge(runtime['model'], binding, observations_dir=run / 'model')
    )
    workflow = MarketWorkflow(
        store,
        model,
        collector,
        enabled_sources=binding['enabled_sources'],
        clock=runtime['clock'],
        conversation_context={'scope_defaults': binding['search_context']},
    )
    return workflow.build(runtime['checkpointer'])
