"""Task delivery shape, canonical projection and provenance without external effects."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from ai_market_intelligence.runtime.reporting import (
    build_report_response,
    report_capabilities,
    validate_delivery,
)


def inputs():
    value = {
        "request_id": "request-report",
        "brief": {"entity": "Mercado Faro", "market": "Colombia", "objective": "Revisar publicaciones",
                  "publication_timezone": "America/Bogota", "start": "2026-11-01T05:00:00+00:00",
                  "end": "2026-11-02T04:59:59.999999+00:00"},
    }
    response = {"status": "complete", "markdown": "La publicación expresa una opinión atribuida.",
                "references": [{"evidence_id": "e1", "source": "facebook", "url": "https://example.com/post"}],
                "artifacts": [], "limitations": ["Muestra acotada."], "coverage": {},
                "content_items": {"schema_version": "1.0", "revision": 1, "items": []},
                "blocks": [{"type": "draft.upsert", "status": "final", "text": "Hallazgo revisado.", "evidence_ids": ["e1"]}]}
    delivery = {"mode": "report", "task_id": "task-1", "task_run_id": "run-1"}
    return value, response, delivery


@pytest.mark.parametrize("change", [
    {"mode": "chat"}, {"task_id": " "}, {"task_run_id": "x" * 129},
    {"task_id": "task\nforeign"}, {"unknown": True}, {"template": None},
])
def test_invalid_delivery_is_rejected_before_use(change):
    _, _, delivery = inputs()
    with pytest.raises(ValueError):
        validate_delivery({**delivery, **change})


@pytest.mark.parametrize("change", ["unknown_section", "duplicate", "missing_always", "version_zero", "version_bool", "unknown_field"])
def test_invalid_template_semantics_cannot_enter_the_report(change):
    value, response, delivery = inputs()
    template = build_report_response(value, response, delivery)["report"]["template"]
    if change == "unknown_section":
        template["sections"][0]["section_id"] = "make-up-an-analysis"
    elif change == "duplicate":
        template["sections"].append(deepcopy(template["sections"][0]))
    elif change == "missing_always":
        template["sections"][0]["visibility"] = "when_present"
    elif change == "version_zero":
        template["version"] = 0
    elif change == "version_bool":
        template["version"] = True
    else:
        template["unknown"] = "value"
    with pytest.raises(ValueError):
        validate_delivery({**delivery, "template": template})


def test_empty_and_populated_results_have_one_stable_template_and_no_input_mutation():
    value, response, delivery = inputs()
    original = deepcopy((value, response, delivery))
    populated = build_report_response(value, response, delivery)
    empty = build_report_response(value, {**response, "status": "partial", "references": [], "blocks": []}, delivery)
    assert populated["report"]["template"] == empty["report"]["template"]
    assert [section["section_id"] for section in empty["report"]["sections"]] == ["summary", "scope", "limitations"]
    assert populated["content_items"] == response["content_items"]
    assert populated["references"] == response["references"]
    assert (value, response, delivery) == original


def test_supplied_template_order_titles_and_version_survive_exactly():
    value, response, delivery = inputs()
    template = build_report_response(value, response, delivery)["report"]["template"]
    template["template_id"] = "operator-template"
    template["version"] = 2
    template["sections"] = [template["sections"][5], template["sections"][0], template["sections"][1]]
    template["sections"][1]["title"] = "Conclusiones **revisadas**"
    result = build_report_response(value, response, {**delivery, "template": template})
    assert result["report"]["template"] == template
    assert [section["section_id"] for section in result["report"]["sections"]] == ["limitations", "summary", "scope"]
    assert "## Conclusiones \\*\\*revisadas\\*\\*" in result["markdown"]


def test_period_origin_status_and_references_are_preserved_without_operational_markdown():
    value, response, delivery = inputs()
    response["coverage"]["reused_request_id"] = "original-request"
    response["status"] = "partial"
    result = build_report_response(value, response, delivery)
    assert result["report"]["source_request_id"] == "original-request"
    assert result["report"]["period"] == {
        "start": value["brief"]["start"], "end": value["brief"]["end"],
        "time_zone": "America/Bogota", "bounds": "[start,end]"}
    assert "**Estado:**" not in result["markdown"]
    assert "**Tarea:**" not in result["markdown"]
    assert "original-request" not in result["markdown"]
    assert "2026\\-11" not in result["markdown"]
    assert "https://example.com/post" in result["markdown"]
    assert result["status"] == "partial"


@pytest.mark.parametrize("change", [{"start": "2026-11-01T00:00:00"}, {"end": "2025-01-01T00:00:00+00:00"}, {"publication_timezone": "Missing/Timezone"}])
def test_invalid_trusted_period_cannot_be_misrepresented(change):
    value, response, delivery = inputs()
    value["brief"].update(change)
    with pytest.raises((ValueError, KeyError)):
        build_report_response(value, response, delivery)


def test_failed_blocks_are_never_added_to_report_findings():
    value, response, delivery = inputs()
    response["blocks"] += [
        {"type": "draft.corrected", "status": "failed", "text": "Rejected", "evidence_ids": ["forged"]},
    ]
    result = build_report_response(value, response, delivery)
    assert "Rejected" not in result["markdown"]


def test_admitted_source_draft_is_present_before_global_finalization():
    value, response, delivery = inputs()
    response["blocks"][0].update(status="provisional", text="Hallazgo por fuente ya revisado.")
    result = build_report_response(value, response, delivery)
    assert "Hallazgo por fuente ya revisado." in result["markdown"]
    response["blocks"][0]["evidence_ids"] = ["forged"]
    with pytest.raises(ValueError, match="public response evidence"):
        build_report_response(value, response, delivery)


def test_report_identity_is_stable_per_request_task_run_and_template():
    value, response, delivery = inputs()
    first = build_report_response(value, response, delivery)["report"]
    second = build_report_response(value, response, delivery)["report"]
    other = build_report_response(value, response, {**delivery, "task_run_id": "other"})["report"]
    assert first["report_id"] == second["report_id"] != other["report_id"]


def test_known_capabilities_are_explicit_and_return_independent_data():
    declared = report_capabilities()
    assert declared["schema_versions"] == ["1.0"]
    assert declared["input_max_bytes"] == 65536
    declared["extensions"].clear()
    assert set(report_capabilities()["extensions"]) == {"market.platform_metrics", "market.sentiment"}


def test_report_sections_cannot_introduce_raw_html_or_unknown_evidence():
    value, response, delivery = inputs()
    response["markdown"] = "<script>bad()</script>"
    with pytest.raises(ValueError, match="raw HTML"):
        build_report_response(value, response, delivery)


def test_non_reportable_terminal_state_retains_original_response_without_an_invented_report():
    value, response, delivery = inputs()
    response.update(status="failed", markdown="La investigación falló.")
    assert build_report_response(value, response, delivery) == response


@pytest.mark.parametrize("case", ["market-empty", "market-posts-comments", "market-partial", "market-ambiguous"])
def test_reviewed_extension_fixtures_are_presented_without_reinterpreting_their_counts(case):
    fixture_path = Path(__file__).resolve().parents[2] / "contracts/fixtures/reports" / (case + ".json")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    value, response, delivery = inputs()
    response["blocks"] = []
    response["references"] = [{key: row[key] for key in ("evidence_id", "source", "url")}
                              for row in fixture["evidence"]]
    extensions = deepcopy(fixture["extensions"])
    result = build_report_response(value, response, delivery, extensions=extensions)
    assert result["report"]["extensions"] == extensions == fixture["extensions"]
    assert "metrics" not in {section["section_id"] for section in result["report"]["sections"]}
    assert "sentiment" in {section["section_id"] for section in result["report"]["sections"]}
    assert all(word not in result["markdown"] for word in ("started:", "completed:", "failed:", "post\\_text", "comment\\_text", "elegibles:"))
    if case == "market-empty":
        assert "No hay opiniones admitidas suficientes" in result["markdown"]
        assert "no demuestra neutralidad" in result["markdown"]
    if case == "market-posts-comments":
        assert "textos de publicaciones" in result["markdown"] and "textos de comentarios" in result["markdown"]
        assert "favorables" in result["markdown"]


def test_extension_support_missing_from_public_manifest_is_not_published():
    value, response, delivery = inputs()
    extension = {"schema_version": "1.0", "status": "analyzed", "limitations": [],
                 "groups": [{"source": "facebook", "basis": "post_text", "target": "Faro", "aspect": None,
                             "eligible_count": 1, "classified_count": 1, "unclassified_count": 0,
                             "counts": {"positive": 1, "negative": 0, "neutral": 0, "mixed": 0},
                             "annotations": [{"evidence_id": "foreign"}], "limitations": []}]}
    with pytest.raises(ValueError, match="absent from public references"):
        build_report_response(value, response, delivery, extensions={"market.sentiment": extension})


@pytest.mark.parametrize("extensions", [[], "not an object", {"unknown": []}])
def test_invalid_extension_container_is_not_silently_discarded(extensions):
    value, response, delivery = inputs()
    with pytest.raises(ValueError, match="validated objects"):
        build_report_response(value, response, delivery, extensions=extensions)


def test_executive_projection_keeps_grounded_action_without_promoting_raw_inventory():
    value, response, delivery = inputs()
    text = ("La publicación reclama retrasos de entrega. Como riesgo comercial, esta experiencia "
            "podría afectar la recompra; conviene contrastar las entregas del canal antes de ampliar su promoción.")
    response["markdown"] = text
    response["references"].append({"evidence_id": "raw", "source": "web", "url": "https://example.com/unrelated"})
    response["limitations"] += ["started: 2; failed: 1", "web: Carencia genérica.", "facebook: Carencia genérica."]
    summary = {"text": text, "evidence_ids": ["e1"], "limitations": ["Una experiencia no mide la frecuencia del problema."]}
    result = build_report_response(value, response, delivery, summary=summary)
    narrative = result["markdown"]
    assert text in narrative
    assert "Una experiencia no mide la frecuencia" in narrative
    assert "Carencia genérica" not in narrative and "started:" not in narrative
    assert "https://example.com/unrelated" not in narrative
    assert "https://example.com/post" in narrative
    assert result["references"] == response["references"]
    assert result["limitations"] == response["limitations"]
    assert result["report"]["limitations"] == summary["limitations"]
    section = next(section for section in result["report"]["sections"] if section["section_id"] == "summary")
    assert section["evidence_ids"] == ["e1"]


def test_raw_or_rejected_only_evidence_cannot_become_executive_advice():
    value, response, delivery = inputs()
    response["blocks"][0].update(status="failed", text="Hay que invertir de inmediato.")
    response["markdown"] = "Hay que invertir de inmediato."
    summary = {"text": response["markdown"], "evidence_ids": [], "limitations": []}
    result = build_report_response(value, response, delivery, summary=summary)
    assert "No hay hallazgos verificados suficientes" in result["markdown"]
    assert "invertir" not in result["markdown"]
    assert "https://example.com/post" not in result["markdown"]
    assert result["references"] == response["references"]


def test_a_summary_cannot_claim_an_audit_reference_as_its_support_without_binding():
    value, response, delivery = inputs()
    summary = {"text": response["markdown"], "evidence_ids": ["foreign"], "limitations": []}
    with pytest.raises(ValueError, match="summary support"):
        build_report_response(value, response, delivery, summary=summary)


@pytest.mark.parametrize("mode", ["financial", "documentary"])
def test_capture_window_is_not_a_business_period_in_executive_scope(mode):
    value, response, delivery = inputs()
    value["brief"].update(retrieval_mode=mode, objective="Resultados de Mercado Faro en 2020")
    result = build_report_response(value, response, delivery)
    assert result["report"]["period"]["start"] == value["brief"]["start"]
    assert "2020" in result["markdown"]
    assert value["brief"]["start"] not in result["markdown"]


def test_material_limit_survives_for_findings_not_selected_in_the_summary():
    value, response, delivery = inputs()
    response["references"].append({"evidence_id": "e2", "source": "web", "url": "https://example.com/second"})
    response["blocks"].append({"type": "draft.upsert", "status": "final", "text": "Otra observación revisada.",
                               "evidence_ids": ["e2"], "limitations": ["Sólo cubre una tienda."]})
    summary = {"text": response["markdown"], "evidence_ids": ["e1"], "limitations": []}
    result = build_report_response(value, response, delivery, summary=summary)
    assert "Otra observación revisada." in result["markdown"]
    assert "Sólo cubre una tienda" in result["markdown"]
    assert result["report"]["limitations"] == ["Sólo cubre una tienda."]
    template = result["report"]["template"]
    template["sections"] = [section for section in template["sections"] if section["section_id"] != "findings"]
    result = build_report_response(value, response, {**delivery, "template": template}, summary=summary)
    assert "Otra observación revisada." not in result["markdown"]
    assert "Sólo cubre una tienda" not in result["markdown"]
