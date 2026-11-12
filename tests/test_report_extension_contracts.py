"""Offline proposal checks; these do not wire a report into the native producer."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest


ROOT = Path(__file__).resolve().parents[1] / "contracts"
SCHEMAS = {
    "market.platform_metrics": "market-platform-metrics-v1.schema.json",
    "market.sentiment": "market-sentiment-v1.schema.json",
}
CASES = sorted((ROOT / "fixtures/reports").glob("market-*.json"))


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validator(key):
    schema = load(ROOT / "extensions" / SCHEMAS[key])
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.parametrize("path", CASES, ids=lambda path: path.stem)
def test_market_extension_examples_match_their_own_schemas(path):
    fixture = load(path)
    assert fixture["synthetic"] is True
    assert set(fixture["extensions"]) == set(SCHEMAS)
    for key, payload in fixture["extensions"].items():
        validator(key).validate(payload)


@pytest.mark.parametrize("path", CASES, ids=lambda path: path.stem)
def test_reviewed_examples_have_reproducible_counts_and_literal_support(path):
    """Audit these examples; runtime evidence/semantic validation is still pending."""
    fixture = load(path)
    evidence = {row["evidence_id"]: row for row in fixture["evidence"]}
    assert len(evidence) == len(fixture["evidence"])
    basis = {"post": "post_text", "comment": "comment_text", "page": "page_text"}
    for platform in fixture["extensions"]["market.platform_metrics"]["platforms"]:
        for unit in platform["observations"]:
            ids, analyzed = unit["evidence_ids"], unit["analyzed_evidence_ids"]
            assert set(analyzed) <= set(ids) <= set(evidence)
            if unit["retained_count"] is not None:
                assert unit["retained_count"] == len(ids)
            if unit["analyzed_count"] is not None:
                assert unit["analyzed_count"] == len(analyzed)
            for identity in ids:
                row = evidence[identity]
                assert row["source"] == platform["source"]
                assert row["content_basis"] == basis[unit["unit"]]
                if row["content_basis"] == "comment_text":
                    parent = evidence[row["parent_evidence_id"]]
                    assert parent["source"] == row["source"]
                    assert parent["content_basis"] == "post_text"
    for group in fixture["extensions"]["market.sentiment"]["groups"]:
        annotations = group["annotations"]
        assert len({row["evidence_id"] for row in annotations}) == len(annotations)
        counts = Counter(row["label"] for row in annotations)
        assert group["eligible_count"] == len(annotations)
        assert group["unclassified_count"] == counts["unclassified"]
        assert group["classified_count"] == sum(group["counts"].values())
        assert group["eligible_count"] == group["classified_count"] + group["unclassified_count"]
        for label, count in group["counts"].items():
            assert count == counts[label]
        for annotation in annotations:
            row = evidence[annotation["evidence_id"]]
            assert row["source"] == group["source"]
            assert row["content_basis"] == group["basis"]
            assert group["target"] in row["text"]
            if annotation["supporting_text"] is not None:
                assert annotation["supporting_text"] in row["text"]


@pytest.mark.parametrize("key", SCHEMAS)
@pytest.mark.parametrize("field,value", [("schema_version", "2.0"), ("unexpected", True)])
def test_extension_does_not_accept_unknown_versions_or_fields(key, field, value):
    payload = deepcopy(load(ROOT / "fixtures/reports/market-posts.json")["extensions"][key])
    payload[field] = value
    assert list(validator(key).iter_errors(payload))


@pytest.mark.parametrize("mutation", ["negative", "duplicate", "invented_count", "neutral_on_empty", "unsupported_calibration", "missing_support", "unclassified_without_reason"])
def test_schema_rejects_known_structural_misrepresentations(mutation):
    fixture = load(ROOT / "fixtures/reports/market-posts.json")
    key = "market.sentiment"
    payload = fixture["extensions"][key]
    group = payload["groups"][0]
    if mutation == "negative":
        group["classified_count"] = -1
    elif mutation == "duplicate":
        key = "market.platform_metrics"
        payload = fixture["extensions"][key]
        payload["platforms"][0]["observations"][0]["evidence_ids"] *= 2
    elif mutation == "invented_count":
        key = "market.platform_metrics"
        payload = fixture["extensions"][key]
        payload["platforms"][0]["observations"][1]["retained_count"] = 0
    elif mutation == "neutral_on_empty":
        payload["status"] = "insufficient"
        payload["limitations"] = ["No hay muestra."]
        group["counts"].update(positive=0, neutral=1)
    elif mutation == "unsupported_calibration":
        payload["method"]["calibration_status"] = "calibrated_for_declared_scope"
    elif mutation == "missing_support":
        group["annotations"][0]["supporting_text"] = None
    else:
        group["annotations"][0].update(label="unclassified", limitations=[])
    assert list(validator(key).iter_errors(payload))


def test_empty_and_later_comments_keep_the_same_task_template_identity():
    before = load(ROOT / "fixtures/reports/market-empty.json")
    after = load(ROOT / "fixtures/reports/market-posts-comments.json")
    assert (before["template_id"], before["template_version"]) == (after["template_id"], after["template_version"])
    empty = before["extensions"]["market.platform_metrics"]["platforms"][0]
    populated = after["extensions"]["market.platform_metrics"]["platforms"][0]
    assert [row["unit"] for row in empty["observations"]] == [row["unit"] for row in populated["observations"]]
    assert empty["observations"][1]["retained_count"] is None
    assert populated["observations"][1]["retained_count"] == 1
