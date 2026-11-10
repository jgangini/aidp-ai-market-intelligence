"""Content grouping and publication stay bound to observed units, never prose guesses."""

import copy

import pytest

from ai_market_intelligence.runtime.content_items import (
    ContentItemsCollection,
    build_content_units,
    content_item_id,
    content_revision,
    item_fingerprint,
    materialize_content_items,
    replace_content_sources,
    validate_content_items,
)


def observation(identity="post", *, source="facebook", url=None, basis="post_text", **fields):
    return {"evidence_id": identity, "source": source,
            "url": url or "https://facebook.com/example/posts/123", "text": "Texto observado.",
            "content_basis": basis, "limitations": ["Muestra acotada."], **fields}


def draft(unit, text="Resumen atribuido de la publicación.", ids=None):
    return {"unit_id": unit["unit_id"], "text": text,
            "evidence_ids": unit["evidence_ids"] if ids is None else ids,
            "limitations": []}


def grouped():
    post = observation()
    comment = observation("comment", url="https://facebook.com/example/posts/123?comment_id=456",
                          basis="comment_text", requested_url=post["url"])
    return [post, comment], build_content_units([post, comment])


def test_item_identity_is_stable_across_captures_and_content_revision_is_separate():
    first = observation(run_id="run1", receipt_id="receipt1")
    second = {**first, "evidence_id": "other", "run_id": "run2", "receipt_id": "receipt2",
              "url": first["url"] + "?utm_campaign=test#fragment"}
    assert content_item_id(first) == content_item_id(second)
    assert content_revision([first]) == content_revision([second])
    second["text"] = "Otra observación."
    assert content_item_id(first) == content_item_id(second)
    assert content_revision([first]) != content_revision([second])
    assert content_item_id({**first, "locator": "Página 2"}) != content_item_id(first)
    assert content_item_id({**first, "source": "web"}) != content_item_id(first)


def test_grouping_is_deterministic_and_distinguishes_observed_locators():
    rows = [observation("b"), observation("a"), observation("page2", locator="Página 2")]
    before = copy.deepcopy(rows)
    units = build_content_units(rows)
    assert units == build_content_units(list(reversed(rows)))
    assert sorted(len(unit["evidence_ids"]) for unit in units) == [1, 2]
    assert rows == before


@pytest.mark.parametrize("source,basis,kind", [
    ("youtube", "title_and_description", "video"), ("youtube", "media_metadata", "unknown"),
    ("instagram", "post_text", "post"), ("web", "page_text", "page"),
    ("financial_document", "pdf_text", "document"), ("market_data", "financial_series", "unknown"),
    ("web", "search_title_and_snippet", "unknown"),
])
def test_kind_uses_observed_basis_and_never_claims_media_analysis(source, basis, kind):
    unit = build_content_units([observation(source=source, basis=basis)])[0]
    assert unit["kind"] == kind and "media_content_analyzed" not in unit


def test_comments_attach_only_to_exact_same_source_observed_parent():
    rows, units = grouped()
    parent, comments = next(u for u in units if not u["is_comments"]), next(u for u in units if u["is_comments"])
    assert comments["parent_item_id"] == parent["item_id"]
    assert comments["evidence_ids"] == ["comment"] and parent["evidence_ids"] == ["post"]
    for changed in ({"requested_url": "https://facebook.com/other/posts/123"},
                    {"requested_url": None}, {"source": "instagram"}):
        invalid = {**rows[1], **changed, "text": "La publicación 123 corresponde al padre."}
        assert not any(u["is_comments"] for u in build_content_units([rows[0], invalid]))


def test_comments_with_ambiguous_locator_parents_are_not_duplicated():
    rows, _ = grouped()
    units = build_content_units([*rows, observation("second-locator", locator="Otra sección")])
    assert len(units) == 2 and not any(unit["is_comments"] for unit in units)


def test_materializes_only_reviewed_drafts_with_separate_comment_analysis():
    rows, units = grouped()
    parent = next(u for u in units if not u["is_comments"])
    comments = next(u for u in units if u["is_comments"])
    value = materialize_content_items(units, [draft(parent), draft(comments, "Opinión de su autor.")])
    item = value["items"][0]
    assert value["schema_version"] == "1.0" and value["revision"] == item["item_revision"] == 1
    assert item["evidence_ids"] == ["post"] and item["primary_evidence_id"] == "post"
    assert item["comments_analysis"] == {"status": "partial", "parent_item_id": item["item_id"],
        "evidence_ids": ["comment"], "text": "Opinión de su autor.", "analyzed_count": 1,
        "limitations": ["Muestra acotada."]}
    assert validate_content_items(value, {row["evidence_id"]: row for row in rows}) == value
    assert materialize_content_items(units, [draft(comments)])["items"] == []


def test_collected_comments_without_reviewed_summary_are_unavailable_not_absent():
    _, units = grouped()
    parent = next(u for u in units if not u["is_comments"])
    value = materialize_content_items(units, [draft(parent)])
    comments = value["items"][0]["comments_analysis"]
    assert comments["status"] == "unavailable" and comments["text"] is None
    assert comments["evidence_ids"] == [] and comments["analyzed_count"] == 0
    assert comments["limitations"] == [
        "Se recuperaron comentarios, pero no se publicó una síntesis individual verificada."
    ]


def test_no_admitted_comments_remain_not_collected():
    units = build_content_units([observation()])
    comments = materialize_content_items(units, [draft(units[0])])["items"][0]["comments_analysis"]
    assert comments == {"status": "not_collected", "parent_item_id": units[0]["item_id"],
                        "evidence_ids": [], "text": None, "analyzed_count": 0, "limitations": []}


def test_recaptured_comment_under_two_receipts_counts_one_observed_comment():
    rows, _ = grouped()
    rows.append({**rows[1], "evidence_id": "recaptured", "receipt_id": "another-receipt"})
    units = build_content_units(rows)
    value = materialize_content_items(units, [draft(unit) for unit in units])
    comments = value["items"][0]["comments_analysis"]
    assert comments["evidence_ids"] == ["comment", "recaptured"] and comments["analyzed_count"] == 1
    assert validate_content_items(value, rows) == value
    comments["analyzed_count"] = 2
    with pytest.raises(ValueError, match="distinct observed comments"):
        validate_content_items(value, rows)


@pytest.mark.parametrize("change", ["comment_swap", "foreign", "duplicate_ids", "duplicate_draft", "duplicate_unit", "parent_swap"])
def test_drafts_cannot_move_evidence_or_change_server_units(change):
    _, units = grouped()
    parent = next(u for u in units if not u["is_comments"])
    drafts = [draft(parent)]
    if change == "comment_swap":
        drafts[0]["evidence_ids"] = ["comment"]
    elif change == "foreign":
        drafts[0]["unit_id"] = "unknown"
    elif change == "duplicate_ids":
        drafts[0]["evidence_ids"] = ["post", "post"]
    elif change == "duplicate_draft":
        drafts *= 2
    elif change == "duplicate_unit":
        units = [*units, parent]
    else:
        units = copy.deepcopy(units)
        next(u for u in units if u["is_comments"])["parent_item_id"] = "item-forged"
    with pytest.raises(ValueError):
        materialize_content_items(units, drafts)


@pytest.mark.parametrize("text", ["<b>Resumen</b>", "```texto```", "Mira https://example.com", "javascript:acción", "[fuente](ruta)"])
def test_model_summaries_cannot_supply_urls_html_or_code(text):
    units = build_content_units([observation()])
    with pytest.raises(ValueError):
        materialize_content_items(units, [draft(units[0], text)])


def test_observed_names_and_abbreviations_are_not_mistaken_for_model_links():
    units = build_content_units([observation()])
    text = "ElTiempo.com y la referencia a EE.UU. aparecen como nombres en la muestra."
    value = materialize_content_items(units, [draft(units[0], text)])
    assert value["items"][0]["text"] == text


def test_metadata_only_cannot_become_a_summary_even_in_a_mixed_unit():
    rows = [observation(), observation("metadata", basis="media_metadata")]
    units = build_content_units(rows)
    with pytest.raises(ValueError, match="metadata alone"):
        materialize_content_items(units, [draft(units[0], ids=["metadata"])])
    assert materialize_content_items(build_content_units(rows[1:]), [])["items"] == []


def test_source_replacement_preserves_other_sources_and_revisions_and_retracts_explicitly():
    facebook = build_content_units([observation()])
    web = build_content_units([observation("web", source="web", url="https://example.com/article", basis="page_text")])
    first = materialize_content_items(facebook, [draft(facebook[0])])
    both = materialize_content_items(web, [draft(web[0])], previous=first)
    before = copy.deepcopy(both)
    assert materialize_content_items(web, [draft(web[0])], previous=both) == both
    changed = materialize_content_items(web, [draft(web[0], "Resumen actualizado.")], previous=both)
    assert changed["revision"] == both["revision"] + 1
    assert next(i for i in changed["items"] if i["source_key"] == "web")["item_revision"] == 2
    assert next(i for i in changed["items"] if i["source_key"] == "facebook")["item_revision"] == 1
    assert materialize_content_items([], [], previous=changed) == changed
    removed = materialize_content_items([], [], previous=changed, replace_sources=["facebook"])
    assert [i["source_key"] for i in removed["items"]] == ["web"]
    assert removed["revision"] == changed["revision"] + 1 and both == before
    with pytest.raises(ValueError, match="source scope"):
        replace_content_sources(first, both["items"], ["web"])


@pytest.mark.parametrize("change", ["source", "primary", "kind", "basis", "parent", "missing", "comment_url"])
def test_wire_validation_rechecks_server_links_against_evidence(change):
    rows, units = grouped()
    value = materialize_content_items(units, [draft(unit) for unit in units])
    if change == "source":
        value["items"][0]["source_key"] = "instagram"
    elif change == "primary":
        value["items"][0]["primary_evidence_id"] = "comment"
    elif change == "kind":
        value["items"][0]["kind"] = "video"
    elif change == "basis":
        value["items"][0]["content_basis"] = ["page_text"]
    elif change == "parent":
        value["items"][0]["comments_analysis"]["parent_item_id"] = "wrong"
    elif change == "missing":
        rows = rows[:1]
    else:
        rows[1]["requested_url"] = "https://facebook.com/other/posts/123"
    with pytest.raises(ValueError):
        validate_content_items(value, rows)


def test_bounds_apply_to_source_evidence_and_encoded_collection():
    with pytest.raises(ValueError, match="source collection allowance"):
        build_content_units([observation(str(n), url=f"https://facebook.com/example/posts/{n}") for n in range(101)])
    unit = build_content_units([observation()])[0]
    item = materialize_content_items([unit], [draft(unit)])["items"][0]
    items = [{**item, "item_id": f"item-{n}", "text": "字" * 3000,
              "comments_analysis": {**item["comments_analysis"], "parent_item_id": f"item-{n}"}} for n in range(120)]
    with pytest.raises(ValueError, match="byte allowance"):
        ContentItemsCollection(revision=1, items=items)


def test_two_source_collection_passes_are_admitted_without_truncation():
    rows = [observation(str(n), url=f"https://facebook.com/example/posts/{n}") for n in range(100)]
    units = build_content_units(rows)
    assert len(units) == 100 and {u["primary_evidence_id"] for u in units} == {str(n) for n in range(100)}


def test_reintroduced_items_reuse_their_revision_floor_only_for_identical_payloads():
    units = build_content_units([observation()])
    first = materialize_content_items(units, [draft(units[0])])
    item = first["items"][0]
    history = {item["item_id"]: {"item_revision": item["item_revision"], "fingerprint": item_fingerprint(item)}}
    before = copy.deepcopy(history)
    withdrawn = replace_content_sources(first, [], ["facebook"], revision_history=history)
    same = materialize_content_items(units, [draft(units[0])], previous=withdrawn, revision_history=history)
    assert same["items"][0]["item_revision"] == 1
    changed = materialize_content_items(units, [draft(units[0], "Un cambio respaldado.")], previous=withdrawn, revision_history=history)
    assert changed["items"][0]["item_revision"] == 2
    assert "revision_history" not in changed and history == before
    stale = materialize_content_items(units, [draft(units[0], "Otro cambio respaldado.")], previous=changed, revision_history=history)
    assert stale["items"][0]["item_revision"] == 3


def test_conflicting_revision_fingerprint_cannot_reuse_an_immutable_revision():
    units = build_content_units([observation()])
    first = materialize_content_items(units, [draft(units[0])])
    item = first["items"][0]
    with pytest.raises(ValueError, match="conflicting payload"):
        materialize_content_items(units, [draft(units[0])], previous=first,
            revision_history={item["item_id"]: {"item_revision": 1, "fingerprint": "0" * 64}})
