"""Pure, scoped counts and reviewed target sentiment for report delivery."""

import copy
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Output
from .runtime_core import digest, public_url


Text = Annotated[str, Field(min_length=1, max_length=500)]
Limits = Annotated[list[Text], Field(max_length=12)]
_SOURCES = {"web", "x", "facebook", "instagram", "youtube", "financial_document", "market_data", "world_bank"}
_UNITS = {"post_text": "post", "comment_text": "comment", "page_text": "page", "pdf_text": "page"}
_LABELS = ("positive", "negative", "neutral", "mixed")
_SAMPLE_LIMIT = "Los conteos y etiquetas describen evidencia retenida, no el universo de publicaciones ni personas."


class SentimentAnnotation(Output):
    evidence_id: Text
    target: Text
    aspect: Text | None
    label: Literal["positive", "negative", "neutral", "mixed", "unclassified"]
    supporting_text: Annotated[str, Field(min_length=1, max_length=1000)] | None
    limitations: Limits

    @field_validator("evidence_id", "target", "aspect", "supporting_text")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Sentiment fields cannot be blank")
        return value

    @model_validator(mode="after")
    def supported_shape(self):
        if self.label == "unclassified" and not self.limitations:
            raise ValueError("Abstention requires an explicit limitation")
        if self.label != "unclassified" and self.supporting_text is None:
            raise ValueError("A sentiment label requires an original support span")
        return self


class SentimentDraft(Output):
    annotations: Annotated[list[SentimentAnnotation], Field(max_length=50)]

    @model_validator(mode="after")
    def unique_annotations(self):
        identities = [annotation_key(row.model_dump()) for row in self.annotations]
        if len(identities) != len(set(identities)):
            raise ValueError("A sentiment unit/target/aspect cannot be classified twice")
        return self


def annotation_key(annotation):
    return digest([annotation["evidence_id"], annotation["target"], annotation["aspect"]])


def _distinct_evidence(value):
    rows = value.get("evidence", {})
    if not isinstance(rows, dict) or len(rows) > 800:
        raise ValueError("Report analytics requires bounded owned evidence")
    retained, identities = {}, {}
    for identity, row in sorted(rows.items()):
        if row.get("evidence_id") != identity or row.get("source") not in _SOURCES:
            raise ValueError("Report evidence identity or source is inconsistent")
        if row.get("content_basis") not in _UNITS:
            continue
        key = row["source"], row["content_basis"], public_url(row["url"])
        previous = retained.get(key)
        if previous is None or _observed(row) > _observed(previous):
            retained[key] = copy.deepcopy(row)
        identities[identity] = key
    parents = {(row["source"], public_url(row["url"])) for row in retained.values()
               if row["content_basis"] == "post_text"}
    admitted = [row for row in retained.values() if _valid_parent(row, parents)]
    ids = {row["evidence_id"] for row in admitted}
    aliases = {identity: retained[key]["evidence_id"] for identity, key in identities.items()}
    return admitted, {identity: alias for identity, alias in aliases.items() if alias in ids}


def _observed(row):
    value = row.get("observed_at")
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("Evidence observation time must carry its timezone")
    return moment.astimezone(timezone.utc)


def _valid_parent(row, parents):
    if row["content_basis"] != "comment_text":
        return True
    parent = row.get("requested_url")
    return isinstance(parent, str) and (row["source"], public_url(parent)) in parents


def _reviewed_ids(value, aliases):
    identities = set()
    for block in value.get("blocks", {}).values():
        if block.get("type") in {"draft.upsert", "draft.corrected"} and block.get("status") != "failed":
            identities.update(block.get("evidence_ids", []))
    for item in value.get("content_items", {}).get("items", []):
        identities.update(item["evidence_ids"])
        comments = item.get("comments_analysis") or {}
        if comments.get("status") in {"partial", "complete"}:
            identities.update(comments.get("evidence_ids", []))
    return {aliases[identity] for identity in identities if identity in aliases
            and value["evidence"][identity]["text"] == value["evidence"][aliases[identity]]["text"]}


def _source_operations(value):
    sources = defaultdict(set)
    events = [*value.get("events", []), *value.get("publications", {}).values()]
    for event in events:
        coverage = event.get("coverage", {})
        source, operation = coverage.get("source"), coverage.get("operation_key")
        if source in _SOURCES and isinstance(operation, str):
            sources[source].add(operation)
    return sources


def _query_outcome(row):
    if row.get("state") == "rejected":
        return "failed"
    if row.get("state") == "running":
        return "pending"
    if row.get("state") != "completed":
        return None
    status = (row.get("receipt") or {}).get("status")
    if status not in {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}:
        return None
    coverage = (row.get("result") or {}).get("coverage") or {}
    return "failed" if status != "SUCCEEDED" or coverage.get("provider_result_error") else "completed"


def _query_counts(source, operations, collection_keys):
    unknown = {"started": None, "completed": None, "failed": None}
    # The Instagram hashtag effect has a separately hashed key, without a source
    # discriminator in historical effect records. A mapped keyword is not its total.
    if operations is None or source == "instagram" or not collection_keys:
        return unknown
    keys = {base + suffix for base in collection_keys for suffix in ("-search", "-discover")}
    selected = [operation for key, operation in operations.items() if key in keys and operation.get("kind") == "apify"]
    if not selected:
        return unknown
    outcomes = [_query_outcome(row) for row in selected]
    if None in outcomes:
        return unknown
    return {"started": len(selected), "completed": outcomes.count("completed"), "failed": outcomes.count("failed")}


def _source_status(source, value, rows, keys):
    collections = [value.get("collections", {})[key] for key in keys if key in value.get("collections", {})]
    if any(row.get("status") == "pending" for row in collections):
        return "pending"
    if collections and all(row.get("status") == "unavailable" for row in collections):
        return "provider_failed"
    if any(row.get("status") != "complete" for row in collections):
        return "partial"
    if rows or any(row["source"] == source for row in value.get("evidence", {}).values()):
        return "available"
    if collections and all(row.get("result_outcome") == "empty" for row in collections):
        return "empty"
    attempted = source in value.get("domain_outcomes", {}) or bool(keys)
    return "partial" if attempted else "not_queried"


def _observation(unit, rows, reviewed, source_status):
    ids = sorted(row["evidence_id"] for row in rows if _UNITS[row["content_basis"]] == unit)
    if ids:
        status = "partial" if source_status in {"partial", "pending", "provider_failed"} else "available"
        known = True
    else:
        status = "empty" if source_status == "empty" and unit != "comment" else "not_collected"
        known = status == "empty"
    return {"unit": unit, "status": status, "population": "retained_evidence",
            "retained_count": len(ids) if known else None,
            "analyzed_count": len(set(ids) & reviewed) if known else None,
            "evidence_ids": ids, "analyzed_evidence_ids": sorted(set(ids) & reviewed),
            "limitations": [] if known else ["No se dispone de una muestra retenida verificable de esta unidad."]}


def platform_metrics(value, operations=None):
    """Derive counts from owned stored units and, when attributable, ledger effects."""
    rows, aliases = _distinct_evidence(value)
    reviewed, keys = _reviewed_ids(value, aliases), _source_operations(value)
    selected = set((value.get("brief") or {}).get("sources", []))
    selected.update(task["source"] for task in (value.get("plan") or {}).get("tasks", []))
    selected.update(row["source"] for row in rows)
    if not selected <= _SOURCES or len(selected) > 8:
        raise ValueError("Unknown report source")
    platforms = []
    for source in sorted(selected):
        retained = [row for row in rows if row["source"] == source]
        status = _source_status(source, value, retained, keys[source])
        counts = _query_counts(source, operations, keys[source])
        units = ("post", "comment") if source in {"x", "facebook", "instagram", "youtube"} else ("page",)
        limits = ["La contabilidad de consultas por fuente no está completamente identificada."] if counts["started"] is None else []
        platforms.append({"source": source, "retrieval_status": status, "query_operations": counts,
                          "observations": [_observation(unit, retained, reviewed, status) for unit in units],
                          "limitations": limits})
    return {"schema_version": "1.0", "method": {"method_id": "retained-evidence-counts", "version": "1.0"},
            "platforms": platforms, "limitations": [_SAMPLE_LIMIT]}


def sentiment_units(value):
    """Return at most fifty distinct post/comment texts, retaining parent context."""
    rows, _ = _distinct_evidence(value)
    eligible = [row for row in rows if row["content_basis"] in {"post_text", "comment_text"}
                and row["source"] in {"web", "x", "facebook", "instagram", "youtube"}]
    # Parents precede comments so the cap cannot retain an orphan comment.
    eligible.sort(key=lambda row: (row["content_basis"] == "comment_text", row["evidence_id"]))
    chosen = eligible[:50]
    parents = {(row["source"], public_url(row["url"])) for row in chosen if row["content_basis"] == "post_text"}
    chosen = [row for row in chosen if _valid_parent(row, parents)]
    omitted = len(eligible) - len(chosen)
    limits = [f"Se excluyeron {omitted} unidades del análisis acotado de sentimiento."] if omitted else []
    raw_count = sum(row.get("content_basis") in {"post_text", "comment_text"}
                    and row.get("source") in {"web", "x", "facebook", "instagram", "youtube"}
                    for row in value.get("evidence", {}).values())
    if raw_count > len(eligible):
        limits.append(f"Se excluyeron {raw_count - len(eligible)} observaciones duplicadas o sin publicación padre admisible.")
    return chosen, limits


def _validated_annotations(units, draft, target):
    if not isinstance(target, str) or not target.strip() or len(target) > 500:
        raise ValueError("A bounded explicit sentiment target is required")
    rows = {row["evidence_id"]: row for row in units}
    if len(rows) != len(units) or len(rows) > 50:
        raise ValueError("Sentiment requires unique bounded evidence")
    parents = {(row["source"], public_url(row["url"])) for row in units if row["content_basis"] == "post_text"}
    if any(row["source"] not in {"web", "x", "facebook", "instagram", "youtube"}
           or row["content_basis"] not in {"post_text", "comment_text"}
           or not _valid_parent(row, parents) for row in units):
        raise ValueError("Sentiment comments require their retained parent")
    result = SentimentDraft.model_validate(draft).model_dump(mode="json")
    for annotation in result["annotations"]:
        row = rows.get(annotation["evidence_id"])
        if row is None or annotation["target"] != target:
            raise ValueError("Sentiment annotation changed its evidence or target")
        quote = annotation["supporting_text"]
        if quote is not None and quote not in row["text"]:
            raise ValueError("Sentiment support must be literal original text")
    return rows, result["annotations"]


def _public_annotation(annotation, accepted):
    fields = ("evidence_id", "label", "supporting_text", "limitations")
    public = {field: copy.deepcopy(annotation[field]) for field in fields}
    if not accepted:
        public.update(label="unclassified", supporting_text=None,
                      limitations=["La etiqueta no fue admitida por la revisión independiente."])
    return public


def _sentiment_group(key, annotations):
    counts = Counter(row["label"] for row in annotations)
    return {"source": key[0], "basis": key[1], "target": key[2], "aspect": key[3],
            "population": "retained_evidence", "eligible_count": len(annotations),
            "classified_count": sum(counts[label] for label in _LABELS),
            "unclassified_count": counts["unclassified"],
            "counts": {label: counts[label] for label in _LABELS},
            "annotations": annotations, "limitations": []}


def build_sentiment(units, draft, accepted_keys, target, limitations=()):
    """Only independently accepted labels contribute to classified counts."""
    rows, annotations = _validated_annotations(units, draft, target)
    allowed = {annotation_key(row) for row in annotations}
    if not set(accepted_keys) <= allowed:
        raise ValueError("Sentiment review identifies an unknown annotation")
    grouped, covered = defaultdict(list), set()
    for annotation in annotations:
        row = rows[annotation["evidence_id"]]
        key = row["source"], row["content_basis"], target, annotation["aspect"]
        grouped[key].append(_public_annotation(annotation, annotation_key(annotation) in accepted_keys))
        covered.add(annotation["evidence_id"])
    for identity in sorted(set(rows) - covered):
        row = rows[identity]
        key = row["source"], row["content_basis"], target, None
        grouped[key].append({"evidence_id": identity, "label": "unclassified", "supporting_text": None,
                             "limitations": ["No se propuso una etiqueta verificable para esta unidad."]})
    if len(grouped) > 32:
        raise ValueError("Sentiment exceeds its grouped output allowance")
    groups = [_sentiment_group(key, values) for key, values in sorted(grouped.items(), key=lambda item: str(item[0]))]
    status = "analyzed" if any(group["classified_count"] for group in groups) else "insufficient"
    limits = list(dict.fromkeys([_SAMPLE_LIMIT, *limitations]))
    if status == "insufficient":
        limits.append("No se admitió una muestra suficiente para determinar polaridad; no equivale a neutralidad.")
    if len(limits) > 20 or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in limits):
        raise ValueError("Sentiment limitations exceed their public allowance")
    return {"schema_version": "1.0", "method": {"method_id": "target-aspect-polarity", "version": "1.0", "calibration_status": "not_calibrated"},
            "status": status, "groups": groups, "limitations": limits}
