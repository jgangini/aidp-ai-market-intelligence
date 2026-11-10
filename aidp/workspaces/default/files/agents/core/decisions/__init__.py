"""Decision routing preserves stable IDs for model calls and durable effects."""

import copy
from . import understand as intake
from . import research

from .understand import understand, UNDERSTAND_ID, UNDERSTAND_PROMPT
from .understand import select_report, SELECT_REPORT_ID, SELECT_REPORT_PROMPT
from .understand import extract_proposal, EXTRACT_PROPOSAL_ID, EXTRACT_PROPOSAL_PROMPT
from .research import plan, PLAN_ID, PLAN_PROMPT
from .research import select_sources, SELECT_SOURCES_ID, SELECT_SOURCES_PROMPT
from .research import select_passages, SELECT_PASSAGES_ID, SELECT_PASSAGES_PROMPT
from .research import analyze, ANALYZE_ID, ANALYZE_PROMPT
from .report import draft, DRAFT_ID, DRAFT_PROMPT
from .report import consolidate, CONSOLIDATE_ID, CONSOLIDATE_PROMPT
from .report import summarize, SUMMARIZE_ID, SUMMARIZE_PROMPT
from .report import repair_units, REPAIR_UNITS_ID, REPAIR_UNITS_PROMPT
from .verify import verify, VERIFY_ID, VERIFY_PROMPT
from .verify import verify_summary, VERIFY_SUMMARY_ID, VERIFY_SUMMARY_PROMPT
from .verify import verify_repair, VERIFY_REPAIR_ID, VERIFY_REPAIR_PROMPT
from .verify import verify_items, VERIFY_ITEMS_ID, VERIFY_ITEMS_PROMPT

PROMPTS = {
    UNDERSTAND_ID: UNDERSTAND_PROMPT,
    SELECT_REPORT_ID: SELECT_REPORT_PROMPT,
    EXTRACT_PROPOSAL_ID: EXTRACT_PROPOSAL_PROMPT,
    PLAN_ID: PLAN_PROMPT,
    SELECT_SOURCES_ID: SELECT_SOURCES_PROMPT,
    SELECT_PASSAGES_ID: SELECT_PASSAGES_PROMPT,
    ANALYZE_ID: ANALYZE_PROMPT,
    DRAFT_ID: DRAFT_PROMPT,
    CONSOLIDATE_ID: CONSOLIDATE_PROMPT,
    SUMMARIZE_ID: SUMMARIZE_PROMPT,
    REPAIR_UNITS_ID: REPAIR_UNITS_PROMPT,
    VERIFY_ID: VERIFY_PROMPT,
    VERIFY_SUMMARY_ID: VERIFY_SUMMARY_PROMPT,
    VERIFY_REPAIR_ID: VERIFY_REPAIR_PROMPT,
    VERIFY_ITEMS_ID: VERIFY_ITEMS_PROMPT,
}


def configure_contract(name, contract, payload, allowed_evidence=None):
    if name == VERIFY_ITEMS_ID:
        identities = [unit["unit_id"] for unit in payload["units"]]
        contract["$defs"]["ContentItemReview"]["properties"]["unit_id"]["enum"] = identities
        contract["properties"]["reviews"].update(minItems=len(identities), maxItems=len(identities))
    if name in {UNDERSTAND_ID, SELECT_REPORT_ID}:
        intake.configure_contract(name, contract, payload)
    if name in {PLAN_ID, ANALYZE_ID, SELECT_SOURCES_ID, SELECT_PASSAGES_ID}:
        research.configure_contract(name, contract, payload)
    if allowed_evidence is not None:
        research.limit_analysis_contract(contract, payload, allowed_evidence)


def normalize_result(name, result, payload, contract):
    if name in {PLAN_ID, ANALYZE_ID}:
        return research.normalize_result(name, result, payload)
    return intake.normalize_result(name, copy.deepcopy(result), payload, contract)
