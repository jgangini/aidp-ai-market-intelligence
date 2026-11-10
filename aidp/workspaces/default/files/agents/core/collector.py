"""Reviewed MCP collection with durable starts and receipt-bound URL discovery."""

import asyncio
import copy
import re
import unicodedata
from math import ceil
from time import monotonic
import httpx
from urllib.parse import urlsplit

from .actor_tools import actor_tool
from .media import media_limitations
from .runtime_core import (
    ConnectorUnavailable,
    ProviderReceiptUnverified,
    ProviderResponseInvalid,
    ProviderResultUnavailable,
    ProviderStartRejected,
    ProviderStartResponseError,
    ProviderStatusUnavailable,
    digest,
    public_url,
)
from .social import social_url, social_destination_identity
from .social_search import comment_parent_url
from .research_contracts import MarketCollection, MarketEvidence, market_moment
from .documents import market_document_links, market_fetch_pdf
from .passages import market_project_passages
from .effects import (
    MarketReadBudgetExceeded,
    Market_MAX_READ_DESTINATIONS,
    Market_READ_BATCH_SIZE,
)


def market_discovery_limit(source, brief):
    return (
        1
        if source == "financial_document" and brief.get("retrieval_mode") == "financial"
        else Market_READ_BATCH_SIZE
    )


def market_publication_matches(source, brief, published):
    if published is None:
        return True
    moment = market_moment(published)
    mode = brief.get("retrieval_mode", "conversation")
    if source == "x" and mode == "documentary":
        return moment <= market_moment(brief["end"])
    if (
        source in {"web", "financial_document", "market_data", "world_bank"}
        and mode != "conversation"
    ):
        return True
    return market_moment(brief["start"]) <= moment <= market_moment(brief["end"])


def _social_words(value):
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return re.findall(r"[a-z0-9]+", "".join(c for c in normalized if not unicodedata.combining(c)))


def _social_hashtag(task, brief):
    for value in (brief.get("entity", ""), task["query"]):
        words = _social_words(value)
        slug = "".join(words)
        if 1 <= len(words) <= 3 and 4 <= len(slug) <= 80 and not slug.isdigit():
            return slug
    return None


def _social_operation_key(profile, brief, target, limit):
    return profile + "-" + digest([target, brief["start"], brief["end"], limit])


def _merge_social_captures(key, captures):
    evidence = {}
    for capture in captures:
        for item in capture["evidence"]:
            identity = (item["content_basis"], item["url"])
            evidence.setdefault(identity, item)
    limits = list(dict.fromkeys(text for c in captures for text in c["limitations"]))
    states = {c["status"] for c in captures}
    if "pending" in states:
        status = "pending"
    elif states == {"unavailable"}:
        status = "unavailable"
    else:
        status = "partial" if states != {"complete"} or not evidence else "complete"
    return MarketCollection(status=status, evidence=list(evidence.values())[:50],
                            limitations=limits[:12], operation_id=key).model_dump(mode="json")


def _comment_result(result, source, parent):
    if result is None:
        return None
    admitted = []
    for row in result["items"]:
        provenance = row.get("provenance") or {}
        try:
            belongs = comment_parent_url(provenance.get("requested_url"), source) == parent
        except (TypeError, ValueError):
            belongs = False
        if belongs and provenance.get("content_basis") == "comment_text":
            admitted.append(row)
    coverage = dict(result.get("coverage") or {})
    if len(admitted) != len(result["items"]):
        coverage["failed_pages"] = [*coverage.get("failed_pages", []), {"reason": "unapproved_comment_parent"}]
    return {**result, "items": admitted, "coverage": coverage}


async def _capture_social(collector, key, source, profile, brief, *, parent=None, **parameters):
    try:
        result, receipt = await collector.invoke_tool(key, profile, brief, **parameters)
    except ConnectorUnavailable as error:
        return MarketCollection(status="unavailable", evidence=[], limitations=[str(error)],
                                operation_id=key).model_dump(mode="json"), []
    except Exception:
        saved = collector.effects.operation(key)
        if saved is None or saved["state"] != "uncertain" or saved.get("receipt") is not None:
            raise
        result, receipt = None, None
    if parent is not None:
        result = _comment_result(result, source, parent)
    collection = collector.collection(source, source, key, result, receipt, brief)
    return collection, (result or {}).get("items", [])


def _comment_targets(rows, evidence, source, task, brief):
    admitted = {item["url"] for item in evidence if item["published_at"] is not None}
    subject = _social_words(brief.get("entity") or task["query"])
    if not subject:
        return []
    candidates = {}
    for row in rows:
        count = (row.get("metrics") or {}).get("comments")
        if row["url"] not in admitted or type(count) is not int or count <= 0:
            continue
        provenance = row.get("provenance") or {}
        words = set(_social_words(row["text"] + " " + str(provenance.get("channel_name") or "")))
        if not set(subject).issubset(words):
            continue
        try:
            target = comment_parent_url(row["url"], source)
        except ValueError:
            continue
        candidates[target] = count
    return sorted(candidates, key=lambda url: (-candidates[url], url))[:2]


async def _collect_social_topics(collector, key, task, brief):
    source = task["source"]
    hashtag = _social_hashtag(task, brief) if source == "instagram" else None
    plans = [(key + "-search", source + "_search", task["query"], 10 if hashtag else 20)]
    if hashtag:
        plans.append((_social_operation_key("instagram_hashtag", brief, hashtag, 10), "instagram_hashtag", hashtag, 10))
    captures, posts = [], []
    for operation, profile, query, limit in plans:
        capture, rows = await _capture_social(collector, operation, source, profile, brief,
                                       query=query, max_items=limit)
        captures.append(capture)
        posts.extend(rows)
        if capture["status"] in {"pending", "unavailable"}:
            return _merge_social_captures(key, captures)
    result = _merge_social_captures(key, captures)
    targets = _comment_targets(posts, result["evidence"], source, task, brief)
    for target in targets:
        operation = _social_operation_key(source + "_comments", brief, target, 5)
        capture, _ = await _capture_social(collector, operation, source,
                                    source + "_comments", brief, parent=target,
                                    urls=[target], max_items=5)
        captures.append(capture)
        if capture["status"] == "pending":
            break
    return _merge_social_captures(key, captures)


class MarketCollector:
    def __init__(
        self,
        provider,
        effects,
        *,
        explicit_urls=(),
        search_context=None,
        financial_selections=None,
        poll_attempts=None,
        poll_seconds=5,
    ):
        if (
            poll_attempts is not None
            and (type(poll_attempts) is not int or not 1 <= poll_attempts <= 24)
            or not 0 <= poll_seconds <= 10
            or poll_attempts is None and poll_seconds < 1
        ):
            raise ValueError("Provider polling requires finite bounds")
        self.provider, self.effects = provider, effects
        self.explicit_urls = {public_url(url) for url in explicit_urls}
        self.search_context = copy.deepcopy(search_context)
        self.financial_selections = copy.deepcopy(financial_selections or {})
        self.poll_attempts, self.poll_seconds = poll_attempts, poll_seconds
        self.before_effect = lambda: None
        self.source_selector = None
        self.passage_selector = None
        self.document_follow_allowed = lambda source: True

    def admit(self, key, kind, arguments, *, read_urls=()):
        self.before_effect()
        operation = self.effects.claim(key, kind, arguments, read_urls=read_urls)
        if operation["claimed"]:
            try:
                self.before_effect()
            except BaseException:
                self.effects.update(key, operation["claim"], state="rejected")
                raise
        return operation

    async def operation(self, key, call, *, read_urls=()):
        admitted = self.admit(key, "apify", call, read_urls=read_urls)
        if admitted["state"] == "completed":
            return admitted["result"], admitted["receipt"]
        if admitted["state"] == "rejected":
            raise ConnectorUnavailable(
                "El inicio anterior fue rechazado; no se repitió."
            )
        receipt = admitted["receipt"]
        if not admitted["claimed"] and receipt is None:
            return None, None
        if admitted["claimed"]:
            try:
                receipt = await self.provider.start(call)
            except ProviderStartRejected as error:
                self.effects.update(
                    key,
                    admitted["claim"],
                    state="rejected",
                    result={"diagnostic": error.diagnostic},
                )
                raise ConnectorUnavailable(
                    "El proveedor rechazó el inicio; no se creó otra captura."
                ) from None
            except ProviderStartResponseError as error:
                self.effects.update(
                    key,
                    admitted["claim"],
                    state="uncertain",
                    result={"diagnostic": error.diagnostic},
                )
                raise
            except BaseException as error:
                self.effects.update(key, admitted["claim"], state="uncertain", result={
                    "diagnostic": {"stage": "provider_start", "error_type": type(error).__name__}
                })
                raise
            self.effects.update(
                key, admitted["claim"], state="running", receipt=receipt
            )
        return await self.finish(key, admitted["claim"], receipt, call)

    async def finish(self, key, token, receipt, call):
        attempts, deadline = self.polling_window(call)
        for attempt in range(attempts):
            if attempt:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(self.poll_seconds, remaining))
            current = await self.read_capture(key, token, receipt, attempt)
            if current is None:
                continue
            receipt = current
            saved_result = self.effects.operation(key).get("result") or {}
            diagnostic = saved_result.get("diagnostic")
            self.effects.update(key, token, state="running", receipt=receipt,
                                result={"diagnostic": diagnostic} if diagnostic else None)
            if receipt["status"] == "SUCCEEDED":
                if not receipt.get("datasetId"):
                    return None, receipt
                try:
                    observation = await self.provider.observations(
                        receipt, call, allow_partial_pages=True
                    )
                except (ProviderResultUnavailable, ValueError) as error:
                    code = (
                        error.code
                        if isinstance(error, ProviderResultUnavailable)
                        else "invalid_dataset"
                    )
                    result = {
                        "items": [],
                        "coverage": {
                            "provider_terminal_status": receipt["status"],
                            "provider_result_error": code,
                        },
                    }
                    result = self.complete_capture(key, token, receipt, result)
                    return result, receipt
                if observation.cost != receipt["spent_microusd"]:
                    return None, receipt
                result = {"items": observation.items, "coverage": observation.coverage}
                result = self.complete_capture(key, token, receipt, result)
                return result, receipt
            if receipt["status"] not in {"READY", "RUNNING"}:
                result = {
                    "items": [],
                    "coverage": {"provider_terminal_status": receipt["status"]},
                }
                result = self.complete_capture(key, token, receipt, result)
                return result, receipt
        return None, receipt

    async def read_capture(self, key, token, receipt, attempt):
        try:
            current = await self.provider.read(receipt["runId"])
            if current["runId"] != receipt["runId"]:
                raise ValueError("The provider returned a receipt for another admitted run")
            return current
        except Exception as error:
            retryable = isinstance(error, (ProviderReceiptUnverified, ProviderStatusUnavailable))
            diagnostic = {
                "stage": "provider_read", "error_type": type(error).__name__,
                "retryable": retryable, "attempt": attempt + 1,
            }
            if isinstance(error, ProviderResponseInvalid):
                diagnostic.update(error.diagnostic)
            elif isinstance(error, ProviderStatusUnavailable) and error.reason in {
                "mcp_error_envelope", "sdk_status_invocation_failed",
            }:
                diagnostic["reason"] = error.reason
                if error.reason == "sdk_status_invocation_failed":
                    diagnostic.update(response_type="str", response_shape="text")
            status = getattr(error, "status", None)
            if type(status) is int and 400 <= status <= 599:
                diagnostic["http_status"] = status
            self.effects.update(key, token, state="running", receipt=receipt,
                                result={"diagnostic": diagnostic})
            if not retryable:
                raise
            return None

    def complete_capture(self, key, token, receipt, result):
        saved_result = self.effects.operation(key).get("result") or {}
        if saved_result.get("diagnostic"):
            result = {**result, "diagnostic": saved_result["diagnostic"]}
        self.effects.update(key, token, state="completed", receipt=receipt, result=result)
        return result

    def polling_window(self, call):
        if self.poll_attempts is not None:
            return self.poll_attempts, float("inf")
        timeout = call.get("callOptions", {}).get("timeout")
        if type(timeout) is not int or not 1 <= timeout <= 1800:
            raise ValueError("Automatic polling requires the admitted actor timeout")
        budget = min(timeout + 15, 240)
        return ceil(budget / self.poll_seconds) + 1, monotonic() + budget

    def tool_arguments(
        self,
        profile,
        brief,
        *,
        query="",
        urls=(),
        financial=None,
        search_mode="research",
        max_items=20,
    ):
        tool = actor_tool(profile)
        arguments = {"max_items": min(max_items, tool.maximum_items)}
        if profile in {"website", "facebook_posts", "instagram_posts", "facebook_comments", "instagram_comments"}:
            arguments["urls"] = list(urls)
        elif profile not in {"world_bank", "yahoo_history"}:
            arguments["query"] = query
        if profile == "x_search":
            arguments["sort"] = "Latest"
        options = {"approved_urls": urls, "unlimited": True}
        if financial is not None:
            arguments.update(
                {
                    key: value
                    for key, value in financial["arguments"].items()
                    if value is not None and value != []
                }
            )
            options["approved_finance"] = financial["approved_scope"]
        if profile == "web_search_serp":
            options["approved_search_context"] = self.search_context
            if self.search_context is not None and brief.get("search_country_code"):
                options["approved_search_context"] = {
                    **self.search_context,
                    "country_code": brief["search_country_code"],
                }
            arguments["search_mode"] = search_mode
        if profile == "website":
            options.update(
                usage_cost_verified=True, require_all_urls=True, render_javascript=True
            )
        if profile == "x_search":
            options["reference_search"] = brief.get("retrieval_mode") == "documentary"
        return tool, arguments, {"start": brief["start"], "end": brief["end"]}, options

    def call(self, profile, brief, **parameters):
        tool, arguments, window, authority = self.tool_arguments(
            profile, brief, **parameters
        )
        return tool.compile(arguments, window, **authority)

    async def invoke_tool(self, key, profile, brief, **parameters):
        tool, arguments, window, authority = self.tool_arguments(
            profile, brief, **parameters
        )
        reads = arguments["urls"] if profile == "website" else ()
        result, receipt = await tool.ainvoke(
            arguments,
            window=window,
            executor=lambda call: self.operation(key, call, read_urls=reads),
            **authority,
        )
        if (
            result is not None
            and (result.get("coverage") or {}).get("provider_result_error")
            == "invalid_dataset"
        ):
            raise ConnectorUnavailable(
                "El proveedor entregó datos incompatibles con el contrato de esta fuente; se conserva el recibo sin publicar evidencia ni repetir la captura."
            )
        if (
            result is not None
            and (result.get("coverage") or {}).get("provider_result_error")
            == ProviderResultUnavailable.code
        ):
            raise ProviderResultUnavailable()
        if (result is not None and (result.get("coverage") or {}).get("provider_result_error")
                == "instagram_destination_unavailable"):
            raise ConnectorUnavailable(
                "El proveedor no pudo recuperar el destino público de Instagram seleccionado; "
                "eso no demuestra que el perfil no exista ni que no haya publicaciones."
            )
        return result, receipt

    def maximum_targets(self, source, brief):
        if source in {"facebook", "instagram"}:
            return market_discovery_limit(source, brief)
        return min(
            market_discovery_limit(source, brief),
            max(
                0, Market_MAX_READ_DESTINATIONS - len(self.effects.reserved_read_urls())
            ),
        )

    def has_read_attempt(self, operation_key):
        value, _ = self.effects.read()
        return any(
            key.startswith(operation_key + "-") and row.get("read_urls")
            for key, row in (value or {}).get("operations", {}).items()
        )

    def has_unread_candidates(self, key, task, brief):
        if task["source"] in {"facebook", "instagram"}:
            used = self.selected_social_destinations(task["source"])
            search = self.effects.operation(key + "-discover")
            candidates = task["urls"] or (
                [item["url"] for item in self.discovery_options(task["source"], search["result"]["items"])]
                if search and search["state"] == "completed" else []
            )
            return any(social_destination_identity(url, task["source"]) not in used for url in candidates)
        if task["urls"]:
            return bool(
                self.maximum_targets(task["source"], brief)
                and not self.has_unresolved_read()
                and set(task["urls"]) - self.used_read_urls()
            )
        search = self.effects.operation(key + "-discover")
        if (
            not search
            or search["state"] != "completed"
            or self.has_unresolved_read()
            or not self.maximum_targets(task["source"], brief)
        ):
            return False
        used = self.used_read_urls()
        return any(
            row["url"] not in used
            for row in self.discovery_options(task["source"], search["result"]["items"])
        )

    def used_read_urls(self):
        value, _ = self.effects.read()
        used = set(self.effects.reserved_read_urls())
        for row in (value or {}).get("operations", {}).values():
            if row.get("read_urls") and row["state"] == "completed":
                result = row.get("result") or {}
                used.update(item["url"] for item in result.get("items", []))
                if result.get("url"):
                    used.add(result["url"])
        return used

    def selected_social_destinations(self, source):
        value, _ = self.effects.read()
        used = set()
        for row in (value or {}).get("operations", {}).values():
            if row["kind"] != "discovery_selection" or row["state"] != "completed":
                continue
            for url in (row.get("result") or {}).get("targets", []):
                try:
                    used.add(social_destination_identity(url, source))
                except ValueError:
                    continue
        return used

    def has_unresolved_read(self):
        value, _ = self.effects.read()
        return any(
            row.get("read_urls") and row["state"] not in {"completed", "rejected"}
            for row in (value or {}).get("operations", {}).values()
        )

    async def collect(
        self,
        *,
        request_id,
        run_id,
        operation_key,
        task,
        brief,
        previous_operation_id,
        discovery_key=None,
    ):
        if previous_operation_id not in (None, operation_key):
            raise ValueError("Pending collection belongs to another operation")
        source = task["source"]
        try:
            if task["urls"] and not self.explicit_targets_match(source, task["urls"]):
                raise ValueError(
                    "A model-proposed destination has no explicit or discovered authority"
                )
            if source in {"facebook", "instagram"} and not task["urls"]:
                return await _collect_social_topics(self, operation_key, task, brief)
            if source in {"x", "youtube"}:
                return await self.collect_keyword(operation_key, task, brief)
            if source in {"market_data", "world_bank"}:
                selection = self.financial_selections.get(source)
                if selection is None:
                    raise ConnectorUnavailable(
                        "Falta una selección financiera explícita de entidad, indicador y periodo."
                    )
                profile = "yahoo_history" if source == "market_data" else "world_bank"
                result, receipt = await self.invoke_tool(
                    operation_key + "-financial", profile, brief, financial=selection
                )
                return self.collection(
                    source, source, operation_key, result, receipt, brief
                )
            if source == "web" and task["urls"]:
                return await self.read_explicit_web(operation_key, task, brief)
            return await self.discover_and_read(
                operation_key, task, brief, discovery_key=discovery_key
            )
        except ConnectorUnavailable as error:
            return MarketCollection(
                status="unavailable",
                evidence=[],
                limitations=[str(error)],
                operation_id=operation_key,
            ).model_dump(mode="json")

    async def collect_keyword(self, key, task, brief):
        source = task["source"]
        result, receipt = await self.invoke_tool(
            key + "-search", source + "_search", brief, query=task["query"]
        )
        return self.collection(source, source, key, result, receipt, brief)

    def explicit_targets_match(self, source, urls):
        if source not in {"facebook", "instagram"}:
            return {public_url(url) for url in urls}.issubset(self.explicit_urls)
        allowed = set()
        for url in self.explicit_urls:
            try:
                allowed.add(social_url(url, source))
            except ValueError:
                continue
        return {social_url(url, source) for url in urls}.issubset(allowed)

    async def read_explicit_web(self, key, task, brief):
        saved = self.effects.operation(key + "-selection")
        selected = (
            saved["result"]["targets"]
            if saved is not None and saved["state"] == "completed"
            else [url for url in task["urls"] if url not in self.used_read_urls()][
                : self.maximum_targets("web", brief)
            ]
        )
        targets = self.remember_targets(key, task, [], selected)
        if not targets:
            return MarketCollection(
                status="pending" if self.has_unresolved_read() else "partial",
                evidence=[],
                limitations=[
                    "El cupo o las lecturas anteriores impiden nuevas lecturas de los enlaces aportados; no se sustituyeron por una búsqueda."
                ],
                operation_id=key,
            ).model_dump(mode="json")
        return await self.read_documents(key, "web", "web", targets, brief)

    async def discover_and_read(self, key, task, brief, *, discovery_key=None):
        source = task["source"]
        targets = list(task["urls"])
        discovery = source
        discovered = []
        if targets and source in {"facebook", "instagram"}:
            saved = self.effects.operation(key + "-selection")
            used = self.selected_social_destinations(source)
            if saved and saved["state"] == "completed":
                targets = saved["result"]["targets"]
            else:
                selected = []
                for url in targets:
                    identity = social_destination_identity(url, source)
                    if identity not in used:
                        selected.append(url)
                        used.add(identity)
                targets = selected
            targets = self.remember_targets(key, task, [], targets)
            if not targets:
                return MarketCollection(status="partial", evidence=[], operation_id=key, limitations=[
                    "Los destinos públicos aportados ya se intentaron; no se repitieron ni se sustituyeron por búsquedas."
                ]).model_dump(mode="json")
        if not targets:
            if (
                self.maximum_targets(source, brief) == 0
                and self.effects.operation(key + "-selection") is None
            ):
                raise MarketReadBudgetExceeded(
                    "Se alcanzó el límite de diez páginas o documentos de esta consulta; no se inició otra búsqueda web."
                )
            if self.search_context is None:
                raise ConnectorUnavailable(
                    "Falta el país e idioma de búsqueda del encargo."
                )
            query = task["query"] + (
                f" site:{source}.com" if source in {"facebook", "instagram"} else ""
            )
            if len(query) > 500:
                raise ConnectorUnavailable(
                    "La consulta supera el tamaño de búsqueda admitido; acota el texto."
                )
            navigation = brief.get("retrieval_mode") in {"documentary", "financial"}
            search, receipt = await self.invoke_tool(
                (discovery_key or key) + "-discover",
                "web_search_serp",
                brief,
                query=query,
                search_mode="navigation" if navigation else "research",
            )
            if search is None:
                return self.collection(source, "web", key, None, receipt, brief)
            discovery = "web"
            targets = await self.select_discovery_targets(
                task, brief, search["items"], operation_key=key
            )
            discovered = search["items"]
        if not targets:
            raise ConnectorUnavailable(
                "El selector no eligió destinos pertinentes entre los resultados de la búsqueda."
                if discovered else
                "La búsqueda acotada no devolvió destinos públicos utilizables para esta fuente."
            )
        if source in {"web", "financial_document"}:
            pdf_candidates = {
                row["url"]
                for row in discovered
                if row.get("text", "").lstrip().casefold().startswith("[pdf]")
            }
            collection = await self.read_documents(
                key, source, discovery, targets, brief, pdf_candidates=pdf_candidates
            )
            if source == "financial_document":
                collection = await self.follow_document(
                    key, source, discovery, targets, brief, collection
                )
            return self.with_discovery_limits(collection, discovered)
        profile = (
            source + "_posts" if source in {"facebook", "instagram"} else "website"
        )
        result, receipt = await self.invoke_tool(
            key + "-read", profile, brief, urls=targets
        )
        return self.with_discovery_limits(
            self.page_collection(
                source,
                discovery,
                key,
                result,
                receipt,
                brief,
                targets if profile == "website" else (),
            ),
            discovered,
        )

    @staticmethod
    def with_discovery_limits(collection, discovered):
        if any(
            (row.get("provenance") or {}).get("excluded_unusable_positions")
            for row in discovered
        ):
            collection["limitations"] = [
                "La búsqueda incluyó destinos no admitidos; se excluyeron sin visitarlos ni perder los candidatos compatibles.",
                *collection["limitations"],
            ][:12]
            if collection["status"] == "complete":
                collection["status"] = "partial"
        if any(
            (row.get("provenance") or {}).get("excluded_http_positions")
            for row in discovered
        ):
            notice = "La búsqueda incluyó enlaces HTTP excluidos; esos destinos no se leyeron ni se convirtieron a HTTPS."
            collection["limitations"] = [notice, *collection["limitations"]][:12]
            if collection["status"] == "complete":
                collection["status"] = "partial"
        return collection

    @classmethod
    def discovery_targets(cls, source, items):
        return [
            item["url"]
            for item in cls.discovery_options(source, items)[:Market_READ_BATCH_SIZE]
        ]

    async def select_discovery_targets(self, task, brief, items, *, operation_key=None):
        options = self.discovery_options(task["source"], items)
        if operation_key is not None:
            saved = self.effects.operation(operation_key + "-selection")
            if saved is not None and saved["state"] == "completed":
                return self.remember_targets(
                    operation_key, task, items, saved["result"]["targets"]
                )
        social = task["source"] in {"facebook", "instagram"}
        used = self.selected_social_destinations(task["source"]) if social else self.used_read_urls()
        options = [item for item in options if (
            social_destination_identity(item["url"], task["source"]) if social else item["url"]
        ) not in used]
        limit = self.maximum_targets(task["source"], brief)
        if not limit:
            return []
        if not options or self.source_selector is None:
            targets = [item["url"] for item in options[:limit]]
            return self.remember_targets(operation_key, task, items, targets)
        self.before_effect()
        selected = await self.source_selector(task, brief, options)
        candidates = {item["candidate_id"]: item["url"] for item in options}
        if not isinstance(selected, list) or len(selected) > limit:
            raise ValueError("Source selection escaped the admitted search candidates")
        if any(
            not isinstance(identity, str) or identity not in candidates
            for identity in selected
        ) or len(selected) != len(set(selected)):
            raise ValueError("Source selection escaped the admitted search candidates")
        self.before_effect()
        return self.remember_targets(
            operation_key, task, items, [candidates[identity] for identity in selected]
        )

    def remember_targets(self, key, task, items, targets):
        if key is None:
            return targets
        operation = self.admit(
            key + "-selection",
            "discovery_selection",
            {"task": task, "candidates_sha256": digest(items)},
        )
        if operation["state"] == "completed":
            return operation["result"]["targets"]
        self.effects.update(
            key + "-selection",
            operation["claim"],
            state="completed",
            result={"targets": targets},
        )
        return targets

    @staticmethod
    def discovery_url(source, row):
        try:
            url = (
                social_url(row["url"], source)
                if source in {"facebook", "instagram"}
                else public_url(row["url"])
            )
        except ValueError:
            return None
        host = urlsplit(url).hostname
        social = any(
            host == domain or host.endswith("." + domain)
            for domain in (
                "facebook.com",
                "instagram.com",
                "x.com",
                "twitter.com",
                "youtube.com",
                "tiktok.com",
                "linkedin.com",
            )
        )
        return None if source in {"web", "financial_document"} and social else url

    @classmethod
    def discovery_options(cls, source, items):
        targets = []
        seen = set()
        for row in items:
            url = cls.discovery_url(source, row)
            identity = social_destination_identity(url, source) if url and source in {"facebook", "instagram"} else url
            if url is None or identity in seen:
                continue
            text = row.get("text", "")
            if not isinstance(text, str):
                raise ValueError("Discovery preview must be text")
            targets.append(
                {
                    "candidate_id": "candidate-" + str(len(targets) + 1),
                    "url": url,
                    "text": text[:1500],
                    "preview_truncated": len(text) > 1500,
                }
            )
            seen.add(identity)
            if len(targets) == 10:
                break
        return targets

    def page_collection(self, source, discovery, key, result, receipt, brief, targets):
        collection = self.collection(source, discovery, key, result, receipt, brief)
        if result is not None and targets:
            retrieved = {
                public_url(
                    (row.get("provenance") or {}).get("requested_url") or row["url"]
                )
                for row in result["items"]
            }
            missing = set(targets) - retrieved
            if missing:
                collection["status"] = "partial"
                collection["limitations"].append(
                    f"No se recuperaron {len(missing)} de las {len(targets)} páginas solicitadas."
                )
        return collection

    async def pdf_operation(self, document_key, url, *, discovered_from=None):
        arguments = {
            "url": url,
            **(
                {"discovered_from": discovered_from}
                if discovered_from is not None
                else {}
            ),
        }
        operation = self.admit(
            document_key, "public_document", arguments, read_urls=[url]
        )
        if operation["state"] == "completed":
            return operation["result"]
        if operation["state"] == "rejected":
            raise ConnectorUnavailable(
                "El PDF no pudo recuperarse; se conserva el rechazo y no se repitió la lectura."
            )
        if not operation["claimed"]:
            return None
        proof = (
            {"discovered_from": discovered_from}
            if discovered_from is not None
            else None
        )
        self.effects.update(
            document_key, operation["claim"], state="running", result=proof
        )
        try:
            document = await market_fetch_pdf(url)
        except (ValueError, httpx.HTTPStatusError) as error:
            self.effects.update(
                document_key,
                operation["claim"],
                state="rejected",
                result={
                    **(proof or {}),
                    "diagnostic": {"error_type": type(error).__name__},
                },
            )
            raise ConnectorUnavailable(
                "El PDF no pudo recuperarse; se conserva el rechazo y no se repitió la lectura."
            ) from None
        except BaseException:
            self.effects.update(
                document_key, operation["claim"], state="uncertain", result=proof
            )
            raise
        document = {
            **document,
            "observed_at": operation["submitted_at"],
            **(
                {"discovered_from": discovered_from}
                if discovered_from is not None
                else {}
            ),
        }
        self.effects.update(
            document_key,
            operation["claim"],
            state="completed",
            receipt={
                "url": document["url"],
                "sha256": document["sha256"],
                "byte_count": document["byte_count"],
            },
            result=document,
        )
        return document

    async def follow_document(self, key, source, discovery, targets, brief, collection):
        if collection["status"] == "pending" or brief.get("retrieval_mode") not in {
            "documentary",
            "financial",
        }:
            return collection
        retrieved = {row["url"] for row in collection["evidence"]}
        retrieved.update(
            row["requested_url"]
            for row in collection["evidence"]
            if row.get("requested_url")
        )
        links = market_document_links(collection["evidence"], set(targets) | retrieved)
        if not links:
            return collection
        notice = "Sólo se siguió un salto documental dentro del límite global de diez destinos; los enlaces restantes no se leyeron."
        if len(targets) >= Market_MAX_READ_DESTINATIONS or not self.maximum_targets(
            source, brief
        ):
            return {
                **collection,
                "status": "partial",
                "limitations": [
                    "Se agotó el límite de diez destinos; no se siguieron los enlaces PDF observados.",
                    *collection["limitations"],
                ][:12],
            }
        url = next(iter(links))
        if self.effects.operation(
            key + "-linked-pdf-" + digest(url)
        ) is None and not self.document_follow_allowed(source):
            return {
                **collection,
                "status": "partial",
                "limitations": [
                    "Se reservó una lectura para la tarea web pendiente; no se siguió el enlace PDF.",
                    *collection["limitations"],
                ][:12],
            }
        try:
            linked = await self.read_documents(
                key + "-linked", source, discovery, [url], brief, proofs=links
            )
        except ConnectorUnavailable as error:
            return {
                **collection,
                "status": "partial",
                "limitations": [str(error), *collection["limitations"]][:12],
            }
        rows = [*collection["evidence"], *linked["evidence"]]
        limits = list(
            dict.fromkeys([*collection["limitations"], *linked["limitations"], notice])
        )
        if len(rows) > 50:
            limits.insert(
                0,
                "El límite conjunto dejó fuera páginas documentales; no se analizaron esas páginas.",
            )
        return MarketCollection(
            status="pending" if linked["status"] == "pending" else "partial",
            evidence=rows[:50],
            limitations=limits[:12],
            operation_id=key,
        ).model_dump(mode="json")

    async def read_documents(
        self, key, source, discovery, targets, brief, *, proofs=None, pdf_candidates=()
    ):
        html = [
            url
            for url in targets
            if not urlsplit(url).path.lower().endswith(".pdf")
            and url not in pdf_candidates
        ]
        evidence = []
        limitations = []
        status = "complete"
        for start in range(0, len(html), Market_READ_BATCH_SIZE):
            batch = html[start : start + Market_READ_BATCH_SIZE]
            result, receipt = await self.invoke_tool(
                key + "-read" + ("-" + str(start) if start else ""),
                "website",
                brief,
                urls=batch,
            )
            result = await self.project_website(result, source, brief)
            collection = self.page_collection(
                source, discovery, key, result, receipt, brief, batch
            )
            evidence.extend(collection["evidence"])
            limitations.extend(collection["limitations"])
            status = collection["status"]
            if status == "pending" and len(targets) > Market_READ_BATCH_SIZE:
                return MarketCollection(
                    status=status,
                    evidence=evidence,
                    limitations=limitations[:12],
                    operation_id=key,
                ).model_dump(mode="json")
        for url in targets:
            if url in html:
                continue
            document_key = key + "-pdf-" + digest(url)
            try:
                document = await self.pdf_operation(
                    document_key, url, discovered_from=(proofs or {}).get(url)
                )
            except ConnectorUnavailable as error:
                status = "pending" if status == "pending" else "partial"
                limitations.append(str(error))
                continue
            if document is None:
                status = "pending"
                limitations.append(
                    "Una lectura documental conserva su resultado pendiente; no se repitió."
                )
                continue
            pages, partial = await market_project_passages(
                document["pages"],
                self.passage_selector,
                {
                    "source": source,
                    "brief": brief,
                    "url": document["url"],
                    "document_sha256": document["sha256"],
                },
            )
            if partial:
                limitations.append(
                    "Se seleccionaron fragmentos del documento según el encargo; no se revisó todo el contenido admitido."
                )
            for page in pages:
                if page["text"].strip():
                    evidence.append(
                        MarketEvidence(
                            evidence_id=digest(
                                [
                                    source,
                                    document_key,
                                    url,
                                    document["sha256"],
                                    page["page"],
                                    page.get("passage_id"),
                                ]
                            )
                            if partial
                            else digest(
                                [
                                    source,
                                    document_key,
                                    url,
                                    document["sha256"],
                                    page["page"],
                                ]
                            ),
                            source=source,
                            discovery_channel=discovery,
                            url=document["url"],
                            requested_url=document.get("source_url", url),
                            text=page["text"],
                            observed_at=document["observed_at"],
                            published_at=None,
                            receipt_id=document_key,
                            content_basis="pdf_text",
                            locator="Página "
                            + str(page["page"])
                            + ("; " + page["locator"] if page.get("locator") else ""),
                            document_sha256=document["sha256"],
                            limitations=document["limitations"],
                        ).model_dump(mode="json")
                    )
            limitations.extend(document["limitations"])
        if len(evidence) > 50:
            limitations.insert(
                0,
                f"El límite conjunto de 50 evidencias dejó fuera {len(evidence) - 50} páginas documentales con texto; no se analizaron esas páginas.",
            )
            evidence = evidence[:50]
        return MarketCollection(
            status="partial" if status == "complete" and limitations else status,
            evidence=evidence,
            limitations=list(dict.fromkeys(limitations))[:12],
            operation_id=key,
        ).model_dump(mode="json")

    async def project_website(self, result, source, brief):
        if result is None:
            return None
        items = []
        partial = False
        for row in result["items"]:
            pages, omitted = await market_project_passages(
                [{"text": row["text"]}],
                self.passage_selector,
                {"source": source, "brief": brief, "url": row["url"]},
                complete_html=source == "web",
            )
            partial = partial or omitted
            for page in pages:
                provenance = {**(row.get("provenance") or {})}
                if page.get("locator"):
                    provenance["locator"] = page["locator"]
                if omitted:
                    provenance["text_truncated"] = True
                items.append({**row, "text": page["text"], "provenance": provenance})
        return {
            **result,
            "items": items,
            "coverage": {
                **(result.get("coverage") or {}),
                "document_projection": partial,
            },
        }

    def collection(self, source, discovery, key, result, receipt, brief):
        if result is None:
            return MarketCollection(
                status="pending",
                evidence=[],
                operation_id=key,
                limitations=[
                    "La captura está pendiente; se conservará su identidad y no se iniciará otra al reanudar."
                ],
            ).model_dump(mode="json")
        evidence = []
        for row in result["items"]:
            item = self.evidence_row(source, discovery, row, receipt, brief)
            if item is not None:
                evidence.append(item)
        limits = []
        if len(evidence) < len(result["items"]):
            limits.append(
                f"Se excluyeron {len(result['items']) - len(evidence)} resultados fuera del periodo solicitado; eso no demuestra ausencia de publicaciones en la plataforma."
            )
        if result.get("coverage", {}):
            coverage = result["coverage"]
            if coverage.get("failed_pages") or coverage.get("provider_terminal_status"):
                limits.append(
                    "El proveedor no completó toda la recuperación solicitada."
                )
            if any(
                row.get("reason") == "access_challenge"
                for row in coverage.get("failed_pages", [])
            ):
                limits.append(
                    "Una página devolvió una verificación de acceso, no el contenido solicitado."
                )
            if coverage.get("omitted_textless_items"):
                limits.append(
                    f"Se excluyeron {coverage['omitted_textless_items']} publicaciones sin texto recuperado; no se analizaron sus imágenes ni videos."
                )
            if coverage.get("document_projection"):
                limits.append(
                    "Se seleccionaron fragmentos del documento según el encargo; no se revisó todo el contenido admitido."
                )
            if coverage.get("unavailable_social_destinations"):
                limits.append(
                    "El proveedor no pudo recuperar algunos destinos de Instagram; "
                    "se conservan las otras publicaciones verificables, sin inferir ausencia en la plataforma."
                )
            if coverage.get("unavailable_search_routes"):
                limits.append(
                    "El proveedor no recuperó algunas rutas de búsqueda social; se conservan las publicaciones obtenidas sin inferir ausencia en la plataforma."
                )
        return MarketCollection(
            status="partial" if limits or not evidence else "complete",
            evidence=evidence,
            limitations=limits,
            operation_id=key,
        ).model_dump(mode="json")

    def evidence_row(self, source, discovery, row, receipt, brief):
        published = row.get("published_at")
        if not market_publication_matches(source, brief, published):
            return None
        provenance = row.get("provenance") or {}
        if provenance.get("provider_run_id") != receipt["runId"]:
            raise ValueError("An observation has no matching admitted provider receipt")
        financial = (
            row.get("metrics") if source in {"market_data", "world_bank"} else None
        )
        limits = self.evidence_limitations(
            source, brief, financial, published, provenance
        )
        identity = [source, row["url"], receipt["runId"], row["text"]]
        if provenance.get("locator"):
            identity.append(provenance["locator"])
        return MarketEvidence(
            evidence_id=digest(identity),
            source=source,
            discovery_channel=discovery,
            url=row["url"],
            text=row["text"],
            published_at=published,
            observed_at=row["observed_at"],
            receipt_id=receipt["runId"],
            data_last_updated=provenance.get("data_last_updated"),
            requested_url=provenance.get("requested_url"),
            author_url=provenance.get("author_url") or provenance.get("channel_url"),
            author_name=provenance.get("channel_name"),
            source_relation=provenance.get("source_relation", "unknown"),
            source_title=provenance.get("source_title"),
            source_description=provenance.get("source_description"),
            media=provenance.get("media", []),
            media_omitted_count=provenance.get("media_omitted_count", 0),
            content_basis="financial_series"
            if financial is not None
            else provenance.get(
                "content_basis",
                "page_text" if source in {"web", "financial_document"} else "post_text",
            ),
            locator=provenance.get("locator"),
            financial=financial,
            limitations=limits,
        ).model_dump(mode="json")

    @staticmethod
    def evidence_limitations(source, brief, financial, published, provenance):
        limits = (
            []
            if financial is not None
            or brief.get("retrieval_mode") in {"documentary", "financial"}
            else ["Muestra acotada; no representa toda la conversación pública."]
        )
        if source == "x" and brief.get("retrieval_mode") == "documentary":
            limits.append(
                "Referencias públicas por palabras clave con fechas diversas; no representan una ventana temporal ni la frecuencia de experiencias de todos los clientes."
            )
        if provenance.get("search_mode") in {
            "facebook_keyword_posts",
            "instagram_keyword_posts",
            "instagram_hashtag_posts",
        }:
            limits.append(
                "El periodo solicitado se comprueba sobre las fechas recibidas; el buscador no garantiza cobertura completa ni orden cronológico."
            )
        if provenance.get("content_basis") == "comment_text":
            limits.append(
                "Comentario de su autor en una publicación seleccionada; no atribuirlo a la marca ni generalizarlo a todos sus clientes."
            )
        if provenance.get("text_truncated") is True:
            limits.append(
                "El texto se analizó parcialmente por el límite de contenido; no se revisó el documento o publicación completos."
            )
        if published is None and financial is None:
            limits.append(
                "La fecha de publicación del documento no está confirmada."
                if brief.get("retrieval_mode") in {"documentary", "financial"}
                else "La fecha de publicación no está confirmada dentro del periodo solicitado."
            )
        if provenance.get("source_relation") == "collaboration":
            limits.append(
                "Publicación en colaboración; no atribuir autoría exclusiva a la cuenta consultada."
            )
        limits.extend(media_limitations(provenance))
        return limits
