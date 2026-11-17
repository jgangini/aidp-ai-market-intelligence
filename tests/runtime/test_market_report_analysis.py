"""Report aggregates are derived from reviewed owned units, never model totals."""

import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from ai_market_intelligence.runtime.report_analysis import (
    SentimentDraft, annotation_key, build_sentiment, platform_metrics, sentiment_units,
)


def row(identity="post", *, source="facebook", basis="post_text", url=None, parent=None):
    return {"evidence_id": identity, "source": source, "content_basis": basis,
            "url": url or "https://example.com/" + identity,
            "requested_url": parent, "text": "Mercado Faro ofrece buena atención.",
            "limitations": []}


def value(*rows):
    return {"brief": {"sources": ["facebook"]}, "plan": {"tasks": []},
            "evidence": {r["evidence_id"]: r for r in rows}, "blocks": {},
            "content_items": {"items": []}, "collections": {}, "domain_outcomes": {},
            "events": [], "publications": {}}


def draft(*rows):
    return {"annotations": [{"evidence_id": r["evidence_id"], "target": "Mercado Faro",
                              "aspect": "atención", "label": "positive",
                              "supporting_text": r["text"], "limitations": []} for r in rows]}


def validate(extension, name):
    schema = json.loads(Path("contracts/extensions/market-" + name + "-v1.schema.json").read_text())
    Draft202012Validator(schema).validate(extension)


def test_retained_and_reviewed_counts_are_distinct_and_recaptures_do_not_add_people():
    first = row()
    repeated = {**first, "evidence_id": "recaptured"}
    other = row("other")
    source = value(first, repeated, other)
    source["blocks"]["source-facebook"] = {"type": "draft.upsert", "status": "provisional", "evidence_ids": ["recaptured"]}
    original = copy.deepcopy(source)
    result = platform_metrics(source)
    observation = result["platforms"][0]["observations"][0]
    assert observation["retained_count"] == 2 and observation["analyzed_count"] == 1
    assert observation["analyzed_evidence_ids"] == ["post"]
    assert result["platforms"][0]["query_operations"]["started"] is None
    assert source == original
    validate(result, "platform-metrics")


def test_rejected_block_does_not_count_as_an_admitted_analysis():
    source = value(row())
    source["blocks"]["source-facebook"] = {"type": "draft.corrected", "status": "failed", "evidence_ids": ["post"]}
    result = platform_metrics(source)
    assert result["platforms"][0]["observations"][0]["analyzed_count"] == 0


def test_review_of_an_old_capture_does_not_mark_changed_text_as_analyzed():
    first = row()
    latest = {**first, "evidence_id": "new-version", "text": "Texto distinto no revisado.",
              "observed_at": "2026-11-12T12:00:00+00:00"}
    source = value(first, latest)
    source["blocks"]["source-facebook"] = {"type": "draft.upsert", "status": "provisional",
                                           "evidence_ids": [first["evidence_id"]]}
    observation = platform_metrics(source)["platforms"][0]["observations"][0]
    assert observation["retained_count"] == 1
    assert observation["analyzed_count"] == 0 and observation["analyzed_evidence_ids"] == []


def test_rejected_abstention_cannot_publish_its_unsupported_reason():
    original = row()
    proposed = draft(original)
    proposed["annotations"][0].update(label="unclassified", limitations=["Razón factual rechazada."])
    result = build_sentiment([original], proposed, [], "Mercado Faro")
    annotation = result["groups"][0]["annotations"][0]
    assert annotation["label"] == "unclassified" and annotation["supporting_text"] is None
    assert "Razón factual rechazada." not in json.dumps(result, ensure_ascii=False)


def test_empty_unqueried_and_provider_failure_are_not_interchangeable():
    source = value()
    assert platform_metrics(source)["platforms"][0]["retrieval_status"] == "not_queried"
    event = {"coverage": {"source": "facebook", "operation_key": "capture"}}
    source["events"] = [event]
    source["collections"]["capture"] = {"status": "complete", "result_outcome": "empty"}
    empty = platform_metrics(source)["platforms"][0]
    assert empty["retrieval_status"] == "empty" and empty["observations"][0]["retained_count"] == 0
    assert empty["observations"][1]["retained_count"] is None
    source["collections"]["capture"] = {"status": "unavailable"}
    failed = platform_metrics(source)["platforms"][0]
    assert failed["retrieval_status"] == "provider_failed"
    assert failed["observations"][0]["retained_count"] is None


def test_actual_mapped_query_receipt_is_counted_once_not_poll_or_model_or_read():
    source = value(row())
    event = {"coverage": {"source": "facebook", "operation_key": "capture"}}
    source["events"] = [event, event]
    source["publications"] = {"copy": event}
    operations = {"capture-search": {"kind": "apify", "state": "completed", "receipt": {"runId": "run", "status": "SUCCEEDED"}},
                  "capture-read": {"kind": "apify", "state": "completed", "receipt": {"runId": "read"}},
                  "model-x": {"kind": "model", "state": "completed", "receipt": {"id": "model"}}}
    counts = platform_metrics(source, operations)["platforms"][0]["query_operations"]
    assert counts == {"started": 1, "completed": 1, "failed": 0}
    operations["capture-search"]["state"] = "uncertain"
    assert platform_metrics(source, operations)["platforms"][0]["query_operations"]["started"] is None


def test_query_attempt_population_includes_rejection_before_run_creation():
    source = value(row())
    source["events"] = [{"coverage": {"source": "facebook", "operation_key": "capture"}}]
    operations = {key: {"kind": "apify", "state": "rejected", "receipt": None}
                  for key in ("capture-search", "capture-discover")}
    counts = platform_metrics(source, operations)["platforms"][0]["query_operations"]
    assert counts == {"started": 2, "completed": 0, "failed": 2}


@pytest.mark.parametrize("receipt,result,expected", [
    ({"runId": "run", "status": "FAILED"}, {}, {"started": 1, "completed": 0, "failed": 1}),
    ({"runId": "run", "status": "SUCCEEDED"}, {"coverage": {"provider_result_error": "invalid_dataset"}},
     {"started": 1, "completed": 0, "failed": 1}),
    ({"runId": "run"}, {}, {"started": None, "completed": None, "failed": None}),
])
def test_query_terminal_failures_and_unverified_results_are_not_successful_searches(receipt, result, expected):
    source = value(row())
    source["events"] = [{"coverage": {"source": "facebook", "operation_key": "capture"}}]
    operations = {"capture-search": {"kind": "apify", "state": "completed", "receipt": receipt, "result": result}}
    assert platform_metrics(source, operations)["platforms"][0]["query_operations"] == expected


def test_instagram_cannot_present_keyword_receipt_as_complete_hashtag_accounting():
    source = value(row(source="instagram"))
    source["brief"]["sources"] = ["instagram"]
    source["events"] = [{"coverage": {"source": "instagram", "operation_key": "capture"}}]
    operations = {"capture-search": {"kind": "apify", "state": "completed", "receipt": {"runId": "run"}}}
    assert platform_metrics(source, operations)["platforms"][0]["query_operations"] == {"started": None, "completed": None, "failed": None}


def test_comments_require_a_retained_same_source_parent_and_do_not_mix_with_posts():
    parent = row()
    comment = row("comment", basis="comment_text", parent=parent["url"])
    orphan = row("orphan", basis="comment_text", parent="https://example.com/absent")
    foreign = row("foreign", source="instagram", basis="comment_text", parent=parent["url"])
    units, limits = sentiment_units(value(parent, comment, orphan, foreign))
    assert {r["evidence_id"] for r in units} == {"post", "comment"}
    assert any("2" in limit and "padre" in limit for limit in limits)
    proposed = draft(parent, comment)
    accepted = [annotation_key(a) for a in proposed["annotations"]]
    result = build_sentiment(units, proposed, accepted, "Mercado Faro", limits)
    assert len(result["groups"]) == 2
    assert {group["basis"] for group in result["groups"]} == {"post_text", "comment_text"}
    validate(result, "sentiment")


def test_sentiment_cap_is_explicit_and_does_not_mutate_evidence():
    source = value(*(row(str(number)) for number in range(60)))
    original = copy.deepcopy(source)
    units, limits = sentiment_units(source)
    assert len(units) == 50 and "10" in limits[0]
    assert source == original


def test_recapture_retains_latest_observed_version_without_counting_an_extra_unit():
    first = {**row("older"), "observed_at": "2026-11-11T10:00:00+00:00"}
    later = {**first, "evidence_id": "newer", "observed_at": "2026-11-11T06:00:00-05:00", "text": "Otra versión observada."}
    units, limits = sentiment_units(value(first, later))
    assert len(units) == 1 and units[0]["evidence_id"] == "newer"
    assert "duplicadas" in limits[0]


def test_no_evidence_or_rejected_label_is_insufficient_not_neutral():
    empty = build_sentiment([], {"annotations": []}, [], "Mercado Faro")
    assert empty["status"] == "insufficient" and not empty["groups"]
    unit = row()
    proposed = draft(unit)
    result = build_sentiment([unit], proposed, [], "Mercado Faro")
    group = result["groups"][0]
    assert result["status"] == "insufficient" and group["classified_count"] == 0
    assert group["counts"]["neutral"] == 0 and group["unclassified_count"] == 1
    assert group["annotations"][0]["supporting_text"] is None
    validate(empty, "sentiment")
    validate(result, "sentiment")


def test_review_acceptance_counts_only_exact_annotation_and_missing_unit_abstains():
    first, second = row(), row("second")
    proposed = draft(first)
    result = build_sentiment([first, second], proposed, [annotation_key(proposed["annotations"][0])], "Mercado Faro")
    assert result["status"] == "analyzed"
    assert sum(group["classified_count"] for group in result["groups"]) == 1
    assert sum(group["unclassified_count"] for group in result["groups"]) == 1
    assert result["method"]["calibration_status"] == "not_calibrated"
    validate(result, "sentiment")


@pytest.mark.parametrize("field,replacement", [("evidence_id", "foreign"), ("target", "Competidor"), ("supporting_text", "una frase inventada")])
def test_wrong_identity_target_or_nonliteral_quote_fails_before_label_publication(field, replacement):
    unit = row()
    proposed = draft(unit)
    proposed["annotations"][0][field] = replacement
    with pytest.raises(ValueError):
        build_sentiment([unit], proposed, [], "Mercado Faro")


def test_duplicate_annotation_and_unknown_review_key_are_rejected():
    unit = row()
    proposed = draft(unit)
    with pytest.raises(ValueError, match="twice"):
        SentimentDraft.model_validate({"annotations": proposed["annotations"] * 2})
    with pytest.raises(ValueError, match="unknown annotation"):
        build_sentiment([unit], proposed, ["forged-review"], "Mercado Faro")


def test_aspects_are_separate_denominators_and_comment_can_inherit_target_context():
    parent = row()
    comment = row("comment", basis="comment_text", parent=parent["url"])
    comment["text"] = "Carísimo."
    proposed = draft(comment)
    first = proposed["annotations"][0]
    first.update(aspect="precio", label="negative")
    second = {**copy.deepcopy(first), "aspect": "experiencia"}
    proposed["annotations"].append(second)
    result = build_sentiment([parent, comment], proposed, [annotation_key(first), annotation_key(second)], "Mercado Faro")
    classified = [group for group in result["groups"] if group["classified_count"]]
    assert len(classified) == 2 and all(group["classified_count"] == 1 for group in classified)
    validate(result, "sentiment")


def test_direct_orphan_and_unknown_source_units_are_rejected():
    orphan = row("orphan", basis="comment_text", parent="https://example.com/missing")
    with pytest.raises(ValueError, match="retained parent"):
        build_sentiment([orphan], draft(orphan), [], "Mercado Faro")
    unknown = row(source="unrecognized")
    with pytest.raises(ValueError):
        build_sentiment([unknown], draft(unknown), [], "Mercado Faro")
