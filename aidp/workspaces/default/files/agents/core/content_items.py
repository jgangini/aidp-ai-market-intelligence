"""Stable content units and bounded publication of externally reviewed summaries."""

import copy
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import FactualReview, Output
from .runtime_core import digest, encoded, public_url


Short = Annotated[str, Field(min_length=1, max_length=500)]
Identity = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
Revision = Annotated[int, Field(ge=1, le=2**53 - 1)]
EvidenceIDs = Annotated[list[Short], Field(max_length=50)]
Limitations = Annotated[list[Short], Field(max_length=12)]
Prose = Annotated[str, Field(min_length=1, max_length=3000)]
_URL = re.compile(r"(?:\b(?:https?|javascript|data):|\[[^\]]*\]\s*\()", re.I)
_BASES = {"page_text", "pdf_text", "post_text", "comment_text", "media_metadata",
          "title_and_description", "search_title_and_snippet", "financial_series"}


def _prose(value):
    if not value.strip() or "<" in value or "```" in value or _URL.search(value):
        raise ValueError("Content summaries must contain prose without URLs, HTML or code")
    return value


class ContentItemSummaryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    unit_id: Short
    text: Prose
    evidence_ids: Annotated[list[Short], Field(min_length=1, max_length=50)]
    limitations: Limitations

    _public_text = field_validator("text")(_prose)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Content summary evidence identities must be unique")
        return values


class ContentItemReview(FactualReview):
    unit_id: Short


class ContentItemsReview(Output):
    reviews: Annotated[list[ContentItemReview], Field(max_length=50)]


class _ItemRevisionRecord(Output):
    item_revision: Revision
    fingerprint: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class _CommentsAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["not_collected", "unavailable", "partial", "complete"]
    parent_item_id: Identity
    evidence_ids: EvidenceIDs
    text: Prose | None
    analyzed_count: Annotated[int, Field(ge=0, le=50)]
    limitations: Limitations

    @model_validator(mode="after")
    def consistent(self):
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Comment evidence identities must be unique")
        if self.status in {"not_collected", "unavailable"}:
            if self.text is not None or self.evidence_ids or self.analyzed_count:
                raise ValueError("Unanalyzed comments cannot contain a summary")
        elif self.text is None or not 1 <= self.analyzed_count <= len(self.evidence_ids):
            raise ValueError("Comment analysis requires text and analyzed evidence")
        if self.text is not None:
            _prose(self.text)
        return self


class _ContentItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    item_id: Identity
    item_revision: Revision
    source_key: Annotated[str, Field(min_length=1, max_length=128)]
    kind: Literal["video", "post", "article", "page", "document", "record", "unknown"]
    primary_evidence_id: Short
    evidence_ids: Annotated[list[Short], Field(min_length=1, max_length=50)]
    text: Prose
    content_basis: Annotated[list[Annotated[str, Field(min_length=1, max_length=128)]], Field(min_length=1, max_length=8)]
    limitations: Limitations
    comments_analysis: _CommentsAnalysis | None = None

    _public_text = field_validator("text")(_prose)

    @model_validator(mode="after")
    def consistent(self):
        if self.primary_evidence_id not in self.evidence_ids or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Primary and unique evidence identities are required")
        if len(set(self.content_basis)) != len(self.content_basis):
            raise ValueError("Content bases must be unique")
        if self.comments_analysis and self.comments_analysis.parent_item_id != self.item_id:
            raise ValueError("Comment analysis belongs to another content item")
        return self


class ContentItemsCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"] = "1.0"
    revision: Revision
    items: Annotated[list[_ContentItem], Field(max_length=400)]

    @model_validator(mode="after")
    def bounded(self):
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("Content item identities must be unique")
        if len(encoded(_collection_dump(self))) > 1024 * 1024:
            raise ValueError("Content collection exceeds its byte allowance")
        return self


def content_item_id(evidence):
    """Identity excludes transient run, evidence, timestamp and text values."""
    return "item-" + digest([evidence["source"], public_url(evidence["url"]), evidence.get("locator")])


def content_revision(evidence):
    """Content fingerprint for callers; not a factual acceptance verdict."""
    fields = ("source", "text", "content_basis", "locator", "source_title", "source_description",
              "author_name", "author_url", "published_at", "limitations", "document_sha256")
    rows = [{key: row.get(key) for key in fields} for row in evidence]
    return digest(sorted(rows, key=digest))


def _kind(rows):
    bases = {row["content_basis"] for row in rows}
    if rows[0]["source"] == "youtube" and "title_and_description" in bases:
        return "video"
    if rows[0]["source"] in {"x", "facebook", "instagram"} and "post_text" in bases:
        return "post"
    if "pdf_text" in bases:
        return "document"
    return "page" if "page_text" in bases else "unknown"


def _unit(item_id, rows, *, comments=False):
    rows = sorted(rows, key=lambda row: (row["content_basis"] == "media_metadata", row["evidence_id"]))
    return {
        "unit_id": "unit-" + digest([item_id, comments]), "item_id": item_id,
        "parent_item_id": item_id if comments else None, "is_comments": comments,
        "source_key": rows[0]["source"], "kind": "unknown" if comments else _kind(rows),
        "evidence_ids": sorted(row["evidence_id"] for row in rows),
        "primary_evidence_id": rows[0]["evidence_id"],
        "content_basis": sorted({row["content_basis"] for row in rows}),
        "limitations": list(dict.fromkeys(text for row in rows for text in row.get("limitations", [])))[:12],
        "evidence": copy.deepcopy(rows), "content_revision": content_revision(rows),
    }


def _bounded_evidence(evidence):
    if not isinstance(evidence, list) or len(evidence) > 800:
        raise ValueError("Content units require at most 800 collected observations")
    identities, sources = set(), {}
    for row in evidence:
        source = row["source"]
        sources[source] = sources.get(source, 0) + 1
        if sources[source] > 100 or len(sources) > 8:
            raise ValueError("Content observations exceed the source collection allowance")
        if row["evidence_id"] in identities or row["content_basis"] not in _BASES:
            raise ValueError("Content observations require unique identities and known bases")
        identities.add(row["evidence_id"])


def build_content_units(evidence):
    """Group observations; orphan or ambiguous comments never acquire a parent by text."""
    _bounded_evidence(evidence)
    groups, parents, comments = {}, {}, []
    for row in evidence:
        if row["content_basis"] == "comment_text":
            comments.append(row)
            continue
        item_id = content_item_id(row)
        groups.setdefault(item_id, []).append(row)
        key = (row["source"], public_url(row["url"]))
        parents.setdefault(key, set()).add(item_id)
    units = [_unit(item_id, rows) for item_id, rows in sorted(groups.items())]
    attached = {}
    for row in comments:
        if not row.get("requested_url"):
            continue
        key = (row["source"], public_url(row["requested_url"]))
        targets = parents.get(key, set())
        if len(targets) == 1:
            attached.setdefault(next(iter(targets)), []).append(row)
    return units + [_unit(item_id, rows, comments=True) for item_id, rows in sorted(attached.items())]


def _server_units(units):
    expected = build_content_units([row for unit in units for row in unit["evidence"]])
    if sorted(units, key=lambda unit: unit["unit_id"]) != sorted(expected, key=lambda unit: unit["unit_id"]):
        raise ValueError("Content units must retain their server grouping and parent identities")
    by_id = {unit["unit_id"]: unit for unit in units}
    if len(by_id) != len(units):
        raise ValueError("Content units must have unique identities")
    return by_id


def _accepted_drafts(units, drafts):
    by_id = _server_units(units)
    accepted = {}
    for raw in drafts:
        draft = ContentItemSummaryDraft.model_validate(raw).model_dump(mode="json")
        identity = draft["unit_id"]
        unit = by_id.get(identity)
        ids = draft["evidence_ids"]
        if unit is None or identity in accepted or len(set(ids)) != len(ids) or not set(ids).issubset(unit["evidence_ids"]):
            raise ValueError("Content summary evidence must belong to its unique server unit")
        selected = {row["content_basis"] for row in unit["evidence"] if row["evidence_id"] in ids}
        if selected <= {"media_metadata"}:
            raise ValueError("Media metadata alone cannot become a content summary")
        accepted[identity] = draft
    return accepted


def _materialized_item(unit, draft, comment_unit, comment_draft):
    ids = draft["evidence_ids"]
    selected = [row for row in unit["evidence"] if row["evidence_id"] in ids]
    item = {key: unit[key] for key in ("item_id", "source_key", "kind", "content_basis")}
    item.update(kind=_kind(selected), content_basis=sorted({row["content_basis"] for row in selected}))
    item.update(item_revision=1, primary_evidence_id=unit["primary_evidence_id"] if unit["primary_evidence_id"] in ids else sorted(ids)[0],
                evidence_ids=sorted(ids), text=draft["text"], limitations=list(dict.fromkeys([*unit["limitations"], *draft["limitations"]]))[:12])
    comments = {"status": "not_collected", "parent_item_id": unit["item_id"],
                "evidence_ids": [], "text": None, "analyzed_count": 0, "limitations": []}
    if comment_draft:
        comments.update(status="partial", evidence_ids=sorted(comment_draft["evidence_ids"]), text=comment_draft["text"],
                        analyzed_count=len({public_url(row["url"]) for row in comment_unit["evidence"]
                                            if row["evidence_id"] in comment_draft["evidence_ids"]}),
                        limitations=list(dict.fromkeys([*comment_unit["limitations"], *comment_draft["limitations"]]))[:12])
    elif comment_unit is not None:
        comments.update(status="unavailable", limitations=[
            "Se recuperaron comentarios, pero no se publicó una síntesis individual verificada."
        ])
    item["comments_analysis"] = comments
    return item


def materialize_content_items(units, drafts, *, previous=None, replace_sources=None, revision_history=None,
                              observed_evidence=None):
    """Publish only drafts already accepted by the caller's factual review."""
    accepted = _accepted_drafts(units, drafts)
    comments = {unit["item_id"]: unit for unit in units if unit["is_comments"]}
    observed_comments = {unit["item_id"]: unit for unit in build_content_units(
        [] if observed_evidence is None else observed_evidence) if unit["is_comments"]}
    items = []
    for unit in units:
        draft = accepted.get(unit["unit_id"])
        if unit["is_comments"] or draft is None:
            continue
        comment = comments.get(unit["item_id"], observed_comments.get(unit["item_id"]))
        comment_draft = accepted.get(comment["unit_id"]) if comment else None
        items.append(_materialized_item(unit, draft, comment, comment_draft))
    sources = {unit["source_key"] for unit in units} if replace_sources is None else set(replace_sources)
    return replace_content_sources(previous, items, sources, revision_history=revision_history)


def replace_content_sources(previous, items, source_keys, *, revision_history=None):
    """Replace an authoritative source snapshot; an explicit empty list retracts it."""
    old = _collection_dump(ContentItemsCollection.model_validate(previous)) if previous is not None else None
    old_items = {item["item_id"]: item for item in (old or {}).get("items", [])}
    source_keys = set(source_keys)
    replacements = [_item_dump(_ContentItem.model_validate(item)) for item in items]
    if any(item["source_key"] not in source_keys for item in replacements):
        raise ValueError("Replacement items exceed their source scope")
    for item in replacements:
        prior = old_items.get(item["item_id"])
        floor = _revision_floor(item["item_id"], prior, revision_history)
        item["item_revision"] = 1 if floor is None else floor["item_revision"] + (item_fingerprint(item) != floor["fingerprint"])
    combined = [item for item in old_items.values() if item["source_key"] not in source_keys] + replacements
    combined.sort(key=lambda item: item["item_id"])
    revision = old["revision"] if old else 1
    if old and combined != old["items"]:
        revision += 1
    return _collection_dump(ContentItemsCollection(revision=revision, items=combined))


def _item_dump(item):
    value = item.model_dump(mode="json")
    if value["comments_analysis"] is None:
        value.pop("comments_analysis")
    return value


def _collection_dump(collection):
    return {"schema_version": collection.schema_version, "revision": collection.revision,
            "items": [_item_dump(item) for item in collection.items]}


def item_fingerprint(item):
    """Canonical public payload fingerprint, independent of its revision counter."""
    value = _item_dump(_ContentItem.model_validate(item))
    value.pop("item_revision")
    return digest(value)


def _revision_floor(item_id, prior, history):
    if history is not None and not isinstance(history, dict):
        raise ValueError("Content revision history must be an identity map")
    recorded = (history or {}).get(item_id)
    floor = _ItemRevisionRecord.model_validate(recorded).model_dump() if recorded is not None else None
    if prior:
        current = {"item_revision": prior["item_revision"], "fingerprint": item_fingerprint(prior)}
        if floor and floor["item_revision"] == current["item_revision"] and floor["fingerprint"] != current["fingerprint"]:
            raise ValueError("A content revision cannot have conflicting payload fingerprints")
        if floor is None or current["item_revision"] > floor["item_revision"]:
            floor = current
    return floor


def validate_content_items(collection, evidence):
    """Validate publication links against stored evidence; does not judge factual prose."""
    value = _collection_dump(ContentItemsCollection.model_validate(collection))
    rows = evidence if isinstance(evidence, dict) else {row["evidence_id"]: row for row in evidence}
    used = set()
    for item in value["items"]:
        _validate_parent_link(item, rows, used)
        _validate_comment_link(item, rows, used)
    return value


def _validate_parent_link(item, rows, used):
    selected = _item_evidence(item["evidence_ids"], rows, used)
    if any(row["content_basis"] == "comment_text" or row["source"] != item["source_key"]
           or content_item_id(row) != item["item_id"] for row in selected):
        raise ValueError("Content item evidence belongs to another server unit")
    if item["kind"] != _kind(selected) or item["content_basis"] != sorted({row["content_basis"] for row in selected}):
        raise ValueError("Content kind and bases must reflect their observed evidence")
    if set(item["content_basis"]) <= {"media_metadata"}:
        raise ValueError("Media metadata alone cannot become a content summary")


def _validate_comment_link(item, rows, used):
    comments = item.get("comments_analysis")
    if comments:
        parent = public_url(rows[item["primary_evidence_id"]]["url"])
        selected = _item_evidence(comments["evidence_ids"], rows, used)
        if comments["analyzed_count"] != len({public_url(row["url"]) for row in selected}):
            raise ValueError("Analyzed comment count must reflect distinct observed comments")
        for row in selected:
            if (row["content_basis"] != "comment_text" or row["source"] != item["source_key"]
                    or not row.get("requested_url") or public_url(row["requested_url"]) != parent):
                raise ValueError("Comment evidence must belong to its exact observed parent")


def _item_evidence(identities, evidence, used):
    if used.intersection(identities) or any(identity not in evidence for identity in identities):
        raise ValueError("Content evidence must exist and belong to only one item unit")
    selected = [evidence[identity] for identity in identities]
    if any(row["evidence_id"] != identity for row, identity in zip(selected, identities)):
        raise ValueError("Stored evidence identity changed")
    used.update(identities)
    return selected
