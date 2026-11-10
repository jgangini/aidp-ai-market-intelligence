"""Public post searches and comments bound to explicitly selected post identities."""

from datetime import datetime, timezone
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .runtime_core import digest, public_url
from .social import social_url, social_metric
from .media import retrieved_media


SOCIAL_SEARCH_ACTORS = {
    "facebook": "scraper_one/facebook-posts-search",
    "instagram": "apify/instagram-hashtag-scraper",
}


COMMENT_ACTORS = {
    "facebook": "apify/facebook-comments-scraper",
    "instagram": "apify/instagram-comment-scraper",
}


def _social_search_rows(rows, call, field):
    limit = call["input"][field]
    if (
        type(limit) is not int
        or not 1 <= limit <= 20
        or not isinstance(rows, list)
        or len(rows) > limit
    ):
        raise ValueError("Social search exceeds its admitted result limit")
    if any(
        not isinstance(row, dict)
        or any(
            row.get(key)
            for key in ("error", "errorDescription", "requestErrorMessages", "isError")
        )
        for row in rows
    ):
        raise ValueError("Social search contains a provider error")
    return rows


def _social_search_date(value):
    if not isinstance(value, str):
        raise ValueError("Social search requires an explicit publication timestamp")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("Social search publication timezone is unknown")
    return moment.astimezone(timezone.utc).isoformat()


def _social_search_metrics(row, mapping, platform="facebook"):
    return {target: social_metric(row.get(field), field, platform)
            for field, target in mapping.items()}


def _facebook_search_post_url(row):
    uri = urlsplit(public_url(row.get("url")))
    path = uri.path.rstrip("/")
    if (
        uri.hostname not in {"facebook.com", "www.facebook.com"}
        or (row.get("postId") is not None and (
            not isinstance(row["postId"], str) or not re.fullmatch(r"[0-9]{1,40}", row["postId"])))
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        raise ValueError("Facebook search requires a public post identity")
    if uri.query and "/groups/" in path:
        if parse_qsl(uri.query, keep_blank_values=True) != [("sale_post_id", path.rsplit("/", 1)[-1])]:
            raise ValueError("Facebook group result has an unreviewed query")
        uri = uri._replace(query="")
    if uri.query:
        return _facebook_permalink_url(uri, path)
    if not re.fullmatch(
        r"/(?:[A-Za-z0-9_.-]+/(?:posts/[A-Za-z0-9_]+|videos/[0-9]{1,40})|reel/[0-9]{1,40}|groups/[A-Za-z0-9_-]+/permalink/[0-9]{1,40})",
        path,
    ):
        raise ValueError("Facebook search requires a reviewed post route")
    return (
        urlunsplit(("https", "facebook.com", path, "", ""))
        if "/permalink/" in path
        else social_url(row["url"], "facebook")
    )


def _facebook_permalink_url(uri, path):
    pairs = parse_qsl(uri.query, keep_blank_values=True)
    values = dict(pairs)
    if (
        path != "/permalink.php"
        or len(pairs) != 2
        or set(values) != {"story_fbid", "id"}
        or not re.fullmatch(r"[0-9]{1,40}", values["id"])
        or not re.fullmatch(
            r"(?:[0-9]{1,40}|pfbid[A-Za-z0-9]{1,200})", values["story_fbid"]
        )
    ):
        raise ValueError("Facebook search requires a reviewed permalink identity")
    return urlunsplit(
        ("https", "facebook.com", path, urlencode(sorted(values.items())), "")
    )


def _social_search_observation(row, call, receipt, platform, text, date, metrics, mode):
    url = (
        _facebook_search_post_url(row)
        if platform == "facebook"
        else social_url(row.get("url"), platform)
    )
    published = _social_search_date(date)
    media = retrieved_media(row, platform)
    if text is None or isinstance(text, str) and not text.strip():
        if not media["media"]:
            return None
        text = "Publicación sin texto recuperado; contiene medios enlazados cuyo contenido no se ha analizado."
        basis = "media_metadata"
    else:
        basis = "post_text"
    if not isinstance(text, str):
        raise ValueError(
            "Social search text analysis requires a retrieved caption or post body"
        )
    return {
        "url": url,
        "text": text[:12000],
        "published_at": published,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "provenance": {
            "source": call["actor"],
            "actor_build": call["callOptions"]["build"],
            "provider_run_id": receipt["runId"],
            "request_arguments_hash": digest(call),
            **media,
            "content_basis": basis,
            "scope": mode,
            "search_mode": mode,
            "source_relation": "unknown",
            "location": None,
            "comments_retrieved": False,
            "video_content_analyzed": False,
            "transcript_retrieved": False,
            "text_truncated": len(text) > 12000,
            "original_character_count": len(text),
        },
    }


def facebook_search_observations(rows, receipt, call):
    inputs = call["input"]
    if (call["actor"] != SOCIAL_SEARCH_ACTORS["facebook"]
            or inputs.get("searchType") != "latest" or not inputs.get("query")):
        raise ValueError("Facebook search requires its reviewed post-search contract")
    results = []
    for row in _social_search_rows(rows, call, "resultsCount"):
        observation = _facebook_search_observation(row, receipt, call)
        if observation is not None:
            results.append(observation)
    if len({row["url"] for row in results}) != len(results):
        raise ValueError("Facebook search repeats a post identity")
    return results


def _facebook_search_observation(row, receipt, call):
    value = row.get("timestamp")
    if type(value) not in {int, float} or not 1000000000000 <= value < 10000000000000:
        raise ValueError("Facebook search requires a millisecond publication timestamp")
    published = datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
    metrics = _social_search_metrics(row, {
        "reactionsCount": "likes", "commentsCount": "comments", "sharesCount": "shares"})
    observation = _social_search_observation(row, call, receipt, "facebook",
        row.get("postText"), published, metrics, "facebook_keyword_posts")
    if observation is not None:
        author = row.get("author") or {}
        name = author.get("name") if isinstance(author, dict) else None
        name = name or row.get("author.name")
        if isinstance(name, str) and name.strip():
            observation["provenance"]["channel_name"] = name[:500]
    return observation


def _instagram_search_route(value, inputs):
    origin = urlsplit(public_url(value))
    expected = inputs["hashtags"][0]
    if origin.hostname not in {"instagram.com", "www.instagram.com"}:
        raise ValueError("Instagram search result has a foreign route")
    if inputs["keywordSearch"]:
        valid = (origin.path.rstrip("/") == "/explore/search/keyword"
                 and parse_qsl(origin.query, keep_blank_values=True) == [("q", expected)])
    else:
        valid = origin.path.rstrip("/") == "/explore/tags/" + expected and not origin.query
    if not valid:
        raise ValueError("Instagram result belongs to another query")
    return urlunsplit(("https", "instagram.com", origin.path.rstrip("/"), origin.query, ""))


def instagram_search_rows(rows, call):
    inputs = call["input"]
    if (call["actor"] != SOCIAL_SEARCH_ACTORS["instagram"]
            or inputs.get("resultsType") != "posts"
            or type(inputs.get("keywordSearch")) is not bool
            or not isinstance(inputs.get("hashtags"), list) or len(inputs["hashtags"]) != 1):
        raise ValueError("Instagram search requires its reviewed keyword/hashtag contract")
    if not isinstance(rows, list) or len(rows) > inputs["resultsLimit"] + 1:
        raise ValueError("Instagram output exceeds the admitted result limit")
    admitted, unavailable = [], []
    for row in rows:
        if isinstance(row, dict) and row.get("error") == "no_items":
            route = _instagram_empty_route(row, inputs)
            if unavailable:
                raise ValueError("Instagram empty result does not match its admitted query")
            unavailable.append(route)
        else:
            admitted.append(row)
    return _social_search_rows(admitted, call, "resultsLimit"), unavailable


def _instagram_empty_route(row, inputs):
    allowed = {"inputUrl", "url", "error", "errorDescription", "requestErrorMessages"}
    if (set(row) - allowed or row.get("errorDescription") != "Empty or private data for provided input"
            or row.get("requestErrorMessages", []) != []):
        raise ValueError("Instagram search contains an unreviewed provider error")
    route = _instagram_search_route(row.get("inputUrl"), inputs)
    if route != _instagram_search_route(row.get("url"), inputs):
        raise ValueError("Instagram empty result does not match its admitted query")
    return route


def _instagram_search_identity(row, inputs):
    _instagram_search_route(row.get("inputUrl"), inputs)
    url = social_url(row.get("url"), "instagram")
    code = row.get("shortCode", urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    if (not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{5,64}", code)
            or urlsplit(url).path.rstrip("/") not in {"/p/" + code, "/reel/" + code}):
        raise ValueError("Instagram search requires a matching public media identity")
    owner = row.get("ownerUsername")
    if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9._]{1,30}", owner):
        raise ValueError("Instagram search requires the observed public owner identity")
    return social_url("https://instagram.com/" + owner, "instagram")


def instagram_search_observations(rows, receipt, call):
    rows, _ = instagram_search_rows(rows, call)
    results = []
    for row in rows:
        author_url = _instagram_search_identity(row, call["input"])
        metrics = _social_search_metrics(row, {"likesCount": "likes", "commentsCount": "comments"}, "instagram")
        observation = _social_search_observation(row, call, receipt, "instagram",
            row.get("caption"), row.get("timestamp"), metrics,
            "instagram_keyword_posts" if call["input"]["keywordSearch"] else "instagram_hashtag_posts")
        if observation is not None:
            observation["provenance"]["author_url"] = author_url
            observation["provenance"]["channel_name"] = row["ownerUsername"]
            results.append(observation)
    if len({row["url"] for row in results}) != len(results):
        raise ValueError("Instagram search repeats a media identity")
    return results


def comment_parent_url(value, platform):
    if platform == "facebook":
        return _facebook_search_post_url({"url": value})
    url = social_url(value, platform)
    if not re.fullmatch(r"/(?:p|reel)/[A-Za-z0-9_-]{5,64}", urlsplit(url).path):
        raise ValueError("Comments require a selected public post, not a profile")
    return url


def compile_comment_input(profile, args, approved_urls, start, limit):
    platform = profile.removesuffix("_comments")
    urls = args["urls"]
    if args["query"] or not isinstance(urls, list) or not 1 <= len(urls) <= 3:
        raise ValueError("Comments require one to three explicitly selected post URLs")
    allowed = {comment_parent_url(url, platform) for url in approved_urls}
    selected = [comment_parent_url(url, platform) for url in urls]
    if len(selected) != len(set(selected)) or not set(selected).issubset(allowed):
        raise ValueError("Comment destinations exceed their explicit authority")
    if len(selected) > limit:
        raise ValueError("The comment limit must cover every selected post")
    inputs = {"resultsLimit": limit // len(selected), "includeNestedComments": False}
    if platform == "facebook":
        return {**inputs, "startUrls": [{"url": url} for url in selected],
                "viewOption": "RECENT_ACTIVITY",
                "onlyCommentsNewerThan": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}
    return {**inputs, "directUrls": selected}


def _comment_identity(row, platform, allowed):
    identity = row.get("commentId" if platform == "facebook" else "id")
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9]{1,40}", identity):
        raise ValueError("Comment requires an explicit public identity")
    parent = comment_parent_url(row.get("inputUrl" if platform == "facebook" else "postUrl"), platform)
    if parent not in allowed:
        raise ValueError("Comment belongs to an unapproved parent post")
    uri = urlsplit(public_url(row.get("commentUrl")))
    if platform == "facebook":
        url = _facebook_comment_url(row, uri, identity, parent)
    else:
        url = _instagram_comment_url(uri, identity, parent)
    return url, parent


def _facebook_comment_url(row, uri, identity, parent):
    if comment_parent_url(row.get("facebookUrl"), "facebook") != parent:
        raise ValueError("Facebook comment parent identities disagree")
    pairs = parse_qsl(uri.query, keep_blank_values=True)
    identifiers = [value for key, value in pairs if key == "comment_id"]
    base = uri._replace(query=urlencode([(k, v) for k, v in pairs if k != "comment_id"]))
    if identifiers != [identity] or comment_parent_url(urlunsplit(base), "facebook") != parent:
        raise ValueError("Facebook comment URL does not match its parent and identity")
    query = [*parse_qsl(urlsplit(parent).query), ("comment_id", identity)]
    return urlunsplit(urlsplit(parent)._replace(query=urlencode(query)))


def _instagram_comment_url(uri, identity, parent):
    if (uri.hostname not in {"instagram.com", "www.instagram.com"} or uri.query
            or uri.path.rstrip("/") != urlsplit(parent).path + "/c/" + identity):
        raise ValueError("Instagram comment URL does not match its parent and identity")
    return parent + "/c/" + identity


def _comment_observation(row, receipt, call, platform, url, parent):
    if row.get("threadingDepth", 0) != 0 or row.get("replies"):
        raise ValueError("Nested comments were not admitted")
    text = row.get("text")
    published = _social_search_date(row.get("date" if platform == "facebook" else "timestamp"))
    if not isinstance(text, str):
        raise ValueError("Comment requires retrieved text")
    if not text.strip():
        return None
    return {"url": url, "text": text[:12000], "published_at": published,
        "observed_at": datetime.now(timezone.utc).isoformat(), "metrics": {},
        "provenance": {"source": call["actor"], "actor_build": call["callOptions"]["build"],
            "provider_run_id": receipt["runId"], "request_arguments_hash": digest(call),
            "requested_url": parent, "content_basis": "comment_text", "scope": "selected_post_comments",
            "source_relation": "requested_destination", "comments_retrieved": True,
            "video_content_analyzed": False, "transcript_retrieved": False,
            "text_truncated": len(text) > 12000, "original_character_count": len(text)}}


def comment_observations(rows, receipt, call):
    platform = next((name for name, actor in COMMENT_ACTORS.items() if actor == call["actor"]), None)
    if platform is None or call["input"].get("includeNestedComments") is not False:
        raise ValueError("Comments require the reviewed top-level comment contract")
    inputs = call["input"]
    selected = ([item["url"] for item in inputs["startUrls"]]
                if platform == "facebook" else inputs["directUrls"])
    allowed = {comment_parent_url(url, platform) for url in selected}
    bounded = {**call, "input": {"resultsLimit": inputs["resultsLimit"] * len(allowed)}}
    results, counts = [], {}
    for row in _social_search_rows(rows, bounded, "resultsLimit"):
        url, parent = _comment_identity(row, platform, allowed)
        counts[parent] = counts.get(parent, 0) + 1
        if counts[parent] > inputs["resultsLimit"]:
            raise ValueError("Comments exceed the admitted limit per post")
        observation = _comment_observation(row, receipt, call, platform, url, parent)
        if observation is not None:
            results.append(observation)
    if len({row["url"] for row in results}) != len(results):
        raise ValueError("Comments repeat a public identity")
    return results
