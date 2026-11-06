"""Read the exact resource declarations shipped with this package."""
import json
from ai_market_intelligence.package import package_root

def resource(kind):
    if kind not in {'agents', 'compute'}:
        raise ValueError('Unsupported Market Intelligence resource kind')
    path = package_root('ai_market_intelligence') / 'aidp/workspaces/default/agent_flow' / kind / 'ai_market_intelligence.json'
    return json.loads(path.read_text(encoding='utf-8'))
