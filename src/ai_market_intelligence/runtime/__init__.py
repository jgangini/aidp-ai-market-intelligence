"""Static import namespace for the canonical AIDP authoring sources.

The wheel includes the same aidp tree under _assets; no generated runtime copy.
"""
from ai_market_intelligence.package import package_root

__path__ = [str(package_root('ai_market_intelligence') / 'aidp/workspaces/default/files/agents/core')]
AGENT_NAME = 'ai_market_intelligence'
APIFY_CREDENTIAL_NAME = 'AiMarketIntelligenceApifyBearer'
