"""Deterministic task reports built only from admitted canonical results."""

import copy
from datetime import datetime
from html import escape
from typing import Annotated, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from .contracts import Output
from .runtime_core import digest, public_url


Identity = Annotated[str, Field(min_length=1, max_length=128, pattern=r"\S")]
Title = Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")]
_ALWAYS = {"summary", "scope", "limitations"}
_DEFAULT = {
    "template_id": "market-overview",
    "version": 1,
    "sections": [
        {"section_id": "summary", "title": "Resumen", "visibility": "always"},
        {"section_id": "scope", "title": "Alcance y método", "visibility": "always"},
        {"section_id": "findings", "title": "Hallazgos por fuente", "visibility": "when_present"},
        {"section_id": "metrics", "title": "Muestra observada", "visibility": "when_present"},
        {"section_id": "sentiment", "title": "Sentimiento de la muestra", "visibility": "when_present"},
        {"section_id": "limitations", "title": "Limitaciones", "visibility": "always"},
    ],
}


class _Section(Output):
    section_id: Literal["summary", "scope", "findings", "metrics", "sentiment", "limitations"]
    title: Title
    visibility: Literal["always", "when_present"]


class _Template(Output):
    template_id: Identity
    version: Annotated[int, Field(ge=1, le=2**53 - 1)]
    sections: Annotated[list[_Section], Field(min_length=1, max_length=40)]

    @model_validator(mode="after")
    def consistent_sections(self):
        identities = [section.section_id for section in self.sections]
        if len(set(identities)) != len(identities):
            raise ValueError("Report template section identities must be unique")
        always = {section.section_id for section in self.sections if section.visibility == "always"}
        if not _ALWAYS <= always:
            raise ValueError("Summary, scope and limitations must remain visible")
        return self


class _Delivery(Output):
    mode: Literal["report"]
    task_id: Identity
    task_run_id: Identity
    template: _Template | None = None

    @field_validator("task_id", "task_run_id")
    @classmethod
    def safe_identity(cls, value):
        if any(ord(char) < 32 for char in value):
            raise ValueError("Task identities cannot contain control characters")
        return value


def validate_delivery(value):
    """Reject unsupported input before the caller admits model/tool effects."""
    if not isinstance(value, dict) or value.get("template", ...) is None:
        raise ValueError("Report delivery must be an object with an optional structured template")
    result = _Delivery.model_validate(value).model_dump(mode="json", exclude_none=True)
    return result


def report_capabilities():
    return {
        "schema_versions": ["1.0"],
        "delivery_modes": ["report"],
        "extensions": {"market.platform_metrics": ["1.0"], "market.sentiment": ["1.0"]},
        "input_max_bytes": 65536,
    }


def _plain(value):
    text = escape(str(value), quote=False)
    for char in "\\`*_{}[]()#+.!|":
        text = text.replace(char, "\\" + char)
    return text.replace("\r", " ").replace("\n", " ")


def _prose(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 100000:
        raise ValueError("Report sections require bounded canonical prose")
    if "<" in text or "```" in text:
        raise ValueError("Report prose cannot contain raw HTML or executable fences")
    return text


def _period(brief):
    start, end = brief.get("start"), brief.get("end")
    if start is None or end is None:
        return None
    moments = [datetime.fromisoformat(item.replace("Z", "+00:00")) for item in (start, end)]
    if any(moment.tzinfo is None for moment in moments) or moments[0] >= moments[1]:
        raise ValueError("A report requires an ordered timezone-aware publication period")
    zone = brief.get("publication_timezone") or "UTC"
    ZoneInfo(zone)
    return {"start": start, "end": end, "time_zone": zone, "bounds": "[start,end]"}


def _scope_text(brief, period):
    rows = [
        "**Objetivo:** " + _plain(brief.get("objective") or "Encargo de investigación"),
        "**Entidad:** " + _plain(brief.get("entity") or "No indicada"),
        "**Mercado:** " + _plain(brief.get("market") or "No indicado"),
        "Los hallazgos se limitan a la evidencia revisada; no representan todo el mercado.",
    ]
    if period and brief.get("retrieval_mode", "conversation") == "conversation":
        rows.append("**Periodo:** " + _plain(period["start"]) + " → " + _plain(period["end"])
                    + " (" + _plain(period["time_zone"]) + ").")
    return "\n\n".join(rows)


def _findings(response, references):
    parts, identities = [], set()
    for block in response.get("blocks", []):
        if block.get("type") not in {"draft.upsert", "draft.corrected"} or block.get("status") not in {"provisional", "final", "partial"}:
            continue
        ids = set(block.get("evidence_ids", []))
        if not ids <= references:
            raise ValueError("Report findings must refer only to public response evidence")
        if ids:
            parts.append(_prose(block["text"]))
            identities.update(ids)
    return "\n\n".join(parts), sorted(identities)


def _extension_evidence_ids(extensions):
    identities = {identity
                  for platform in extensions.get("market.platform_metrics", {}).get("platforms", [])
                  for unit in platform["observations"] for identity in unit["evidence_ids"]}
    identities.update(row["evidence_id"]
                      for group in extensions.get("market.sentiment", {}).get("groups", [])
                      for row in group["annotations"])
    return identities


def _sentiment(extension):
    rows = []
    identities = set()
    for group in extension["groups"]:
        labels = [label for label, count in group["counts"].items() if count]
        if not labels:
            continue
        names = {"positive": "favorables", "negative": "desfavorables", "neutral": "neutrales", "mixed": "mixtas"}
        basis = "comentarios" if group["basis"] == "comment_text" else "publicaciones"
        rows.append("- En los textos de " + basis + " de " + _plain(group["source"])
                    + " se admitieron opiniones " + ", ".join(names[label] for label in labels)
                    + " hacia " + _plain(group["target"])
                    + (" sobre " + _plain(group["aspect"]) if group["aspect"] else "") + ".")
        identities.update(row["evidence_id"] for row in group["annotations"] if row["label"] != "unclassified")
    if rows:
        rows.append("La clasificación no está calibrada y describe sólo estos textos, no la opinión del mercado. "
                    "Las unidades sin clasificación no equivalen a opiniones neutrales.")
    else:
        rows.append("No hay opiniones admitidas suficientes para determinar una orientación. "
                    "Esto no demuestra neutralidad ni ausencia de conversación.")
    return "\n\n".join(rows), sorted(identities)


def _section_data(value, response, references, extensions, period, summary, include_findings):
    findings = _findings(response, references)
    supported = set(summary["evidence_ids"]) if summary is not None else set(findings[1])
    if not supported <= references:
        raise ValueError("Report summary support must refer only to public response evidence")
    visible_support = supported | (set(findings[1]) if include_findings else set())
    limits = list(dict.fromkeys([*(summary["limitations"] if summary is not None else []), *[
        limit for block in response.get("blocks", [])
        if block.get("type") in {"draft.upsert", "draft.corrected"}
        and block.get("status") in {"provisional", "final", "partial"}
        and set(block.get("evidence_ids", [])) & visible_support
        for limit in block.get("limitations", [])]]))
    data = {
        "summary": (_prose(response["markdown"]) if supported else
                    "No hay hallazgos verificados suficientes para responder al encargo. "
                    "La evidencia disponible no permite fundamentar una decisión comercial.", sorted(supported)),
        "scope": (_scope_text(value.get("brief") or {}, period), []),
        "findings": findings,
        "metrics": ("", []),
        "sentiment": ("", []),
        "limitations": ("\n".join("- " + _plain(limit) for limit in limits)
                        or "Las conclusiones se limitan a los hallazgos respaldados y al alcance indicado.", []),
    }
    if "market.sentiment" in extensions:
        data["sentiment"] = _sentiment(extensions["market.sentiment"])
    return data, limits


def build_report_response(value, response, delivery, *, extensions=None, summary=None):
    """Project a reviewed result into a stable task template without new inference."""
    delivery = validate_delivery(delivery)
    if response.get("status") not in {"complete", "partial"}:
        return copy.deepcopy(response)
    template = copy.deepcopy(delivery.get("template") or _DEFAULT)
    references = [row["evidence_id"] for row in response.get("references", [])]
    if len(references) != len(set(references)) or any(not isinstance(identity, str) or not 1 <= len(identity) <= 128 for identity in references):
        raise ValueError("Report references must have unique bounded evidence identities")
    extensions = {} if extensions is None else copy.deepcopy(extensions)
    if not isinstance(extensions, dict) or len(extensions) > 20 or any(not isinstance(payload, dict) for payload in extensions.values()):
        raise ValueError("Report extensions require an object of validated objects")
    if not _extension_evidence_ids(extensions) <= set(references):
        raise ValueError("Report extension support is absent from public references")
    limits = response.get("limitations", [])
    if not isinstance(limits, list) or len(limits) > 40 or any(not isinstance(limit, str) or not limit.strip() or len(limit) > 2000 for limit in limits):
        raise ValueError("Report limitations exceed the supported public contract")
    period = _period(value.get("brief") or {})
    include_findings = any(section["section_id"] == "findings" for section in template["sections"])
    data, narrative_limits = _section_data(value, response, set(references), extensions, period, summary, include_findings)
    sections = []
    for definition in template["sections"]:
        text, identities = data[definition["section_id"]]
        if not text and definition["visibility"] == "when_present":
            continue
        if not set(identities) <= set(references):
            raise ValueError("A report section refers to evidence absent from public references")
        sections.append({"section_id": definition["section_id"],
                         "markdown": _prose(text or "No hay contenido revisado disponible para esta sección."),
                         "evidence_ids": identities})
    report = {
        "schema_version": "1.0",
        "report_id": digest(["market-report/1.0", value["request_id"], delivery["task_id"], delivery["task_run_id"], template]),
        "task_id": delivery["task_id"], "task_run_id": delivery["task_run_id"],
        "title": "Informe de investigación de mercado", "template": template,
        "sections": sections, "limitations": narrative_limits,
    }
    original = response.get("coverage", {}).get("reused_request_id")
    if original is not None:
        if not isinstance(original, str) or not 1 <= len(original) <= 128 or not original.strip():
            raise ValueError("An original report request identity must be bounded")
        report["source_request_id"] = original
    if period:
        report["period"] = period
    if extensions:
        report["extensions"] = extensions
    titles = {section["section_id"]: section["title"] for section in template["sections"]}
    markdown = ["# " + _plain(report["title"])]
    markdown.extend("## " + _plain(titles[section["section_id"]]) + "\n\n" + section["markdown"] for section in sections)
    cited = {identity for section in sections for identity in section["evidence_ids"]}
    if cited:
        links = ["- [" + _plain(row["source"]) + "](<"
                 + quote(public_url(row["url"]), safe=":/?=&%#@!$*+,;~._-()") + ">)"
                 for row in response["references"] if row["evidence_id"] in cited]
        markdown.append("## Referencias\n\n" + "\n".join(links))
    return {**copy.deepcopy(response), "report": report, "markdown": "\n\n".join(markdown)}
