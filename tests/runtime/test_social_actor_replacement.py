"""Replacement source contracts and explicit post-comment authority."""

import asyncio
import pytest

from ai_market_intelligence.runtime.actor_tools import actor_tool, actor_tool_specifications
from ai_market_intelligence.runtime.connectors import FACEBOOK_QUERY_MAX_LENGTH
from ai_market_intelligence.runtime.social_search import comment_observations, facebook_search_observations
from tests.runtime.test_market_intelligence_social_search import (
    WINDOW, RECEIPT, search_call, facebook_row, instagram_row,
)
from tests.runtime.test_market_intelligence_payload import provider_for


def test_facebook_search_enforces_the_shared_provider_query_length_boundary():
    arguments = {"query": "a" * FACEBOOK_QUERY_MAX_LENGTH, "max_items": 3}
    call = actor_tool("facebook_search").compile(arguments, WINDOW, unlimited=True)
    assert call["input"]["query"] == arguments["query"]
    with pytest.raises(ValueError, match="at most 100 characters"):
        actor_tool("facebook_search").compile(
            {**arguments, "query": arguments["query"] + "b"}, WINDOW, unlimited=True
        )


def comment_call(platform, urls=None, limit=5):
    selected = urls or ["https://facebook.com/reel/123" if platform == "facebook"
                        else "https://instagram.com/p/ABC123"]
    return actor_tool(platform + "_comments").compile(
        {"urls": selected, "max_items": limit}, WINDOW,
        approved_urls=selected, unlimited=True)


def comment_row(platform):
    if platform == "facebook":
        parent = "https://www.facebook.com/reel/123/"
        return {"commentId": "456", "commentUrl": parent + "?comment_id=456",
                "inputUrl": parent, "facebookUrl": parent, "text": "A customer response",
                "date": "2026-10-29T12:00:00Z", "threadingDepth": 0}
    parent = "https://www.instagram.com/p/ABC123/"
    return {"id": "456", "commentUrl": parent + "c/456", "postUrl": parent,
            "text": "A customer response", "timestamp": "2026-10-29T12:00:00Z"}


def test_instagram_catalogue_advertises_keyword_hashtag_and_comments():
    names = {item["name"] for item in actor_tool_specifications(["instagram"])}
    assert {"instagram_search", "instagram_hashtag", "instagram_comments"} <= names
    call = actor_tool("instagram_hashtag").compile(
        {"query": "another_brand", "max_items": 3}, WINDOW, unlimited=True)
    assert call["input"] == {"hashtags": ["another_brand"], "keywordSearch": False,
                             "resultsType": "posts", "resultsLimit": 3}
    with pytest.raises(ValueError):
        actor_tool("instagram_hashtag").compile(
            {"query": "another brand", "max_items": 3}, WINDOW, unlimited=True)


def test_mixed_instagram_posts_and_exact_empty_marker_preserve_partial_coverage():
    post = instagram_row()
    route = post["inputUrl"]
    marker = {"inputUrl": route, "url": route, "error": "no_items",
              "errorDescription": "Empty or private data for provided input"}
    provider, _ = provider_for([post, marker])
    receipt = {**RECEIPT, "status": "SUCCEEDED", "datasetId": "fixture-data"}
    result = asyncio.run(provider.observations(receipt, search_call("instagram")))
    assert len(result.items) == 1
    assert len(result.coverage["unavailable_search_routes"]) == 1
    for patch in ({"inputUrl": "https://instagram.com/explore/tags/other"},
                  {"error": "blocked"}, {"requestErrorMessages": ["blocked"]}):
        bad, _ = provider_for([post, {**marker, **patch}])
        with pytest.raises(ValueError):
            asyncio.run(bad.observations(receipt, search_call("instagram")))


@pytest.mark.parametrize("timestamp", [True, 1793275200, "1793275200000", float("inf"), None])
def test_facebook_rejects_ambiguous_timestamp_units(timestamp):
    with pytest.raises(ValueError):
        facebook_search_observations([{**facebook_row(), "timestamp": timestamp}],
                                     RECEIPT, search_call("facebook"))


@pytest.mark.parametrize("platform", ["facebook", "instagram"])
def test_comments_bind_public_comment_identity_to_approved_parent_and_date(platform):
    call = comment_call(platform)
    assert call["input"]["includeNestedComments"] is False
    result = comment_observations([comment_row(platform)], RECEIPT, call)[0]
    assert result["published_at"] == "2026-10-29T12:00:00+00:00"
    assert result["provenance"]["content_basis"] == "comment_text"
    assert result["provenance"]["comments_retrieved"] is True
    assert result["provenance"]["provider_run_id"] == RECEIPT["runId"]
    assert result["provenance"]["requested_url"].endswith("123")
    assert "456" in result["url"]
    if platform == "facebook":
        assert call["input"]["viewOption"] == "RECENT_ACTIVITY"
        assert call["input"]["onlyCommentsNewerThan"] == "2026-10-27T10:00:00Z"


@pytest.mark.parametrize("platform", ["facebook", "instagram"])
def test_comment_compiler_rejects_unapproved_urls_profiles_and_duplicate_parents(platform):
    good = "https://facebook.com/reel/123" if platform == "facebook" else "https://instagram.com/p/ABC123"
    for selected, approved in [([good], []), ([good, good], [good]),
                               (["https://" + platform + ".com/some_profile"], [good])]:
        with pytest.raises(ValueError):
            actor_tool(platform + "_comments").compile(
                {"urls": selected, "max_items": 5}, WINDOW,
                approved_urls=approved, unlimited=True)


@pytest.mark.parametrize("platform", ["facebook", "instagram"])
@pytest.mark.parametrize("mutation", ["parent", "identity", "comment_url", "nested", "date"])
def test_comments_reject_foreign_parent_identity_nested_reply_and_missing_date(platform, mutation):
    row = comment_row(platform)
    if mutation == "parent":
        row["inputUrl" if platform == "facebook" else "postUrl"] = (
            "https://facebook.com/reel/999" if platform == "facebook" else "https://instagram.com/p/OTHER1")
    elif mutation == "identity":
        row["commentId" if platform == "facebook" else "id"] = "999"
    elif mutation == "comment_url":
        row["commentUrl"] = "https://evil.example/comment/456"
    elif mutation == "nested":
        row["replies"] = [{"text": "not admitted"}]
    else:
        row["date" if platform == "facebook" else "timestamp"] = "2026-10-29T12:00:00"
    with pytest.raises(ValueError):
        comment_observations([row], RECEIPT, comment_call(platform))


@pytest.mark.parametrize("platform", ["facebook", "instagram"])
def test_comment_provider_projection_and_dispatch(platform):
    provider, calls = provider_for([comment_row(platform)])
    receipt = {**RECEIPT, "status": "SUCCEEDED", "datasetId": "fixture-data"}
    result = asyncio.run(provider.observations(receipt, comment_call(platform)))
    assert len(result.items) == 1
    assert result.items[0]["provenance"]["content_basis"] == "comment_text"


def test_projected_posts_use_their_public_url_identity_without_optional_ids():
    from ai_market_intelligence.runtime.social_search import instagram_search_observations
    facebook = facebook_row()
    facebook.pop("postId")
    instagram = instagram_row()
    instagram.pop("shortCode")
    assert len(facebook_search_observations([facebook], RECEIPT, search_call("facebook"))) == 1
    assert len(instagram_search_observations([instagram], RECEIPT, search_call("instagram"))) == 1
    with pytest.raises(ValueError):
        facebook_search_observations([{**facebook, "url": "https://facebook.com/profile"}],
                                     RECEIPT, search_call("facebook"))


@pytest.mark.parametrize("platform", ["facebook", "instagram"])
def test_comments_cannot_exceed_per_parent_limit_or_repeat_identity(platform):
    call = comment_call(platform)
    row = comment_row(platform)
    with pytest.raises(ValueError):
        comment_observations([row, row], RECEIPT, call)
    second = "https://facebook.com/reel/999" if platform == "facebook" else "https://instagram.com/p/OTHER1"
    parent = row.get("inputUrl") or row.get("postUrl")
    call = comment_call(platform, [parent, second], limit=2)
    other = {**row, "commentId" if platform == "facebook" else "id": "457",
             "commentUrl": row["commentUrl"].replace("456", "457")}
    with pytest.raises(ValueError, match="limit per post"):
        comment_observations([row, other], RECEIPT, call)


def test_instagram_route_cannot_hide_additional_empty_query_parameters():
    from ai_market_intelligence.runtime.social_search import instagram_search_observations
    row = instagram_row()
    row["inputUrl"] += "&other="
    with pytest.raises(ValueError):
        instagram_search_observations([row], RECEIPT, search_call("instagram"))
