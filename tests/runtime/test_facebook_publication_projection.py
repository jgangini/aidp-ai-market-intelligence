"""Observed Facebook photos and commenters retain their own public identities."""

import asyncio

import pytest

from ai_market_intelligence.runtime.content_items import build_content_units, materialize_content_items
from ai_market_intelligence.runtime.media import retrieved_media
from ai_market_intelligence.runtime.wire import market_reference_manifest
from tests.runtime.test_market_intelligence_payload import provider_for
from tests.runtime.test_market_intelligence_research_collector import market_collection_fixture
from tests.runtime.test_market_intelligence_social_search import WINDOW, RECEIPT, facebook_row, search_call
from tests.runtime.test_social_actor_replacement import comment_call, comment_row


PHOTO = "https://cdn.example.org/photo.jpg?sig=a%2Fb&token=x+y"


def test_keyword_photo_survives_provider_collection_and_public_parent_reference():
    raw = {**facebook_row(), "author": {"name": "Autor del post"},
           "attachments": [{"type": "photo", "url": PHOTO,
                            "accessibilityCaption": "Not a verified image description"}]}
    provider, tool = provider_for([raw])
    receipt = {**RECEIPT, "datasetId": "fixture-data", "status": "SUCCEEDED"}
    result = asyncio.run(provider.observations(receipt, search_call("facebook")))
    assert "attachments" in tool.calls[0]["fields"].split(",")
    _, _, collector = market_collection_fixture()
    evidence = collector.collection("facebook", "facebook", "search", {"items": result.items, "coverage": result.coverage}, receipt, WINDOW)["evidence"]
    reference = market_reference_manifest({row["evidence_id"]: row for row in evidence}, [evidence[0]["evidence_id"]])[0]
    assert reference["author_name"] == "Autor del post" and "text" not in reference
    assert reference["media"] == [{"kind": "image", "url": PHOTO, "delivery": "direct", "role": "attachment"}]
    assert reference["media_content_analyzed"] is False


def test_photo_attachment_keeps_existing_media_guards_deduplication_and_bounds():
    rows = [{"type": "photo", "url": PHOTO}, {"type": "photo", "url": "https://127.0.0.1/private"}]
    rows += [{"type": "photo", "url": f"https://cdn.example.org/{i}.jpg"} for i in range(9)]
    value = retrieved_media({"attachments": rows, "media": [{"image": {"uri": PHOTO}}]}, "facebook")
    assert len(value["media"]) == 8 and value["media_omitted_count"] == 3
    assert sum(item["url"] == PHOTO for item in value["media"]) == 1
    assert retrieved_media({"attachments": [{"type": "link", "url": PHOTO}]}, "facebook")["media"] == []
    assert retrieved_media({"attachments": [{"type": {}, "url": PHOTO}]}, "facebook")["media"] == []


def test_observed_video_attachment_is_not_mislabeled_as_a_photo_or_analyzed_content():
    url = "https://cdn.example.org/reel.mp4?sig=a%2Fb"
    value = retrieved_media({"attachments": [{"type": "video", "url": url}]}, "facebook")
    assert value["media"] == [{"kind": "video", "url": url, "delivery": "direct", "role": "attachment"}]
    assert value["media_content_analyzed"] is False


def test_two_commenters_keep_text_name_and_parent_without_becoming_publications():
    parent_url = "https://facebook.com/reel/123"
    raw = []
    for identity, name in [("456", "Primera persona"), ("457", "Segunda persona")]:
        raw.append({**comment_row("facebook"), "commentId": identity,
                    "commentUrl": parent_url + "?comment_id=" + identity,
                    "profileName": name, "profileUrl": "https://www.facebook.com/user" + identity,
                    "text": "Comentario de " + name, "postTitle": "Autor del post"})
    provider, tool = provider_for(raw)
    receipt = {**RECEIPT, "datasetId": "fixture-data", "status": "SUCCEEDED"}
    result = asyncio.run(provider.observations(receipt, comment_call("facebook")))
    assert {"profileName", "profileUrl"} <= set(tool.calls[0]["fields"].split(","))
    _, _, collector = market_collection_fixture()
    evidence = collector.collection("facebook", "facebook", "comments", {"items": result.items, "coverage": result.coverage}, receipt, WINDOW)["evidence"]
    parent = {**evidence[0], "evidence_id": "parent", "url": parent_url, "requested_url": None,
              "content_basis": "post_text", "text": "Contenido de la publicación.", "author_name": "Autor del post"}
    units = build_content_units([parent, *evidence])
    assert len(units) == 2 and sum(not unit["is_comments"] for unit in units) == 1
    drafts = [{"unit_id": unit["unit_id"], "text": "Comentarios de sus autores." if unit["is_comments"] else parent["text"],
               "evidence_ids": unit["evidence_ids"], "limitations": []} for unit in units]
    collection = materialize_content_items(units, drafts)
    item, = collection["items"]
    assert item["evidence_ids"] == ["parent"] and item["comments_analysis"]["analyzed_count"] == 2
    references = market_reference_manifest({row["evidence_id"]: row for row in [parent, *evidence]},
                                           [row["evidence_id"] for row in evidence])
    assert {row["author_name"] for row in references} == {"Primera persona", "Segunda persona"}
    assert {row["author_url"] for row in references} == {"https://facebook.com/user456", "https://facebook.com/user457"}
    assert all(row["requested_url"] == parent_url and row["text"] == "Comentario de " + row["author_name"] for row in references)


@pytest.mark.parametrize("metadata", [{}, {"profileName": None, "profileUrl": "https://facebook.com.evil.example/user"},
                                       {"profileName": " ", "profileUrl": "https://127.0.0.1/private"},
                                       {"profileUrl": "https://facebook.com/profile.php?id=1&id=2"},
                                       {"profileUrl": "https://facebook.com/profile.php?id=1&redirect=other"}])
def test_unknown_or_unsafe_commenter_metadata_never_uses_parent_author(metadata):
    provider, _ = provider_for([{**comment_row("facebook"), **metadata, "postTitle": "Nombre del padre"}])
    receipt = {**RECEIPT, "datasetId": "fixture-data", "status": "SUCCEEDED"}
    result = asyncio.run(provider.observations(receipt, comment_call("facebook")))
    provenance = result.items[0]["provenance"]
    assert "channel_name" not in provenance and "author_url" not in provenance
    assert result.items[0]["text"] == "A customer response"


def test_commenter_numeric_profile_keeps_its_own_validated_identity():
    raw = {**comment_row("facebook"), "profileName": "Una persona",
           "profileUrl": "https://www.facebook.com/profile.php?id=123"}
    provider, _ = provider_for([raw])
    result = asyncio.run(provider.observations({**RECEIPT, "datasetId": "fixture-data", "status": "SUCCEEDED"}, comment_call("facebook")))
    assert result.items[0]["provenance"]["author_url"] == "https://facebook.com/profile.php?id=123"
