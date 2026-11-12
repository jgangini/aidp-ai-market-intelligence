"""AIDP-authenticated current service binding and public content stream; no managed business tables."""

from contextlib import aclosing
import hashlib
import json
from pathlib import Path

from .memory import native_memory, root_graph_config
from .package_identity import installed_identity
from .runtime_core import Scope, digest
from .runtime_versions import runtime_versions
from .collector import MarketCollector
from .config import validate_market_config
from .effects import (
    MarketEffectPending,
    MarketEffects,
    MarketObjectSnapshot,
    MarketRecordedModel,
)
from .services import build_market_services
from .session import MarketSession, MarketSessionPending
from .state import MarketRunStore
from .wire import (
    market_chat_text,
    market_event_packet,
    market_replay_packet,
    market_stream_graph,
    market_wire_line,
)
from .workflow import MarketWorkflow
from .reporting import report_capabilities, validate_delivery


def market_chat_input(text, session_id, trace_id):
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 65536:
        raise ValueError("A bounded chat request is required")
    if text.lstrip().startswith("{"):
        value = json.loads(text)
    else:
        if not isinstance(trace_id, str) or not 1 <= len(trace_id.strip()) <= 128:
            raise ValueError(
                "Ordinary chat requires its native trace ID; API clients may send an explicit request_id"
            )
        value = {
            "request_id": "native:" + digest([session_id, trace_id]),
            "question": text.strip(),
        }
    if not isinstance(value, dict):
        raise ValueError("A chat command must be an object")
    if "delivery" not in value and len(text) > 4096:
        raise ValueError("A bounded chat request is required")
    return market_validate_command(value)


def market_validate_command(value):
    if {"request_id", "question"} <= set(value) <= {
        "request_id", "question", "financial_selections", "delivery"}:
        if (
            not isinstance(value["question"], str)
            or not 1 <= len(value["question"].strip()) <= 500
        ):
            raise ValueError("Question must contain 1–500 characters")
        if "financial_selections" in value:
            value["financial_selections"] = market_financial_selections(
                value["financial_selections"]
            )
        if "delivery" in value:
            value["delivery"] = validate_delivery(value["delivery"])
    elif set(value) not in (
        {"action", "request_id"},
        {"action", "request_id", "after_sequence"},
    ):
        raise ValueError("Unsupported chat command fields")
    elif value["action"] not in {"replay", "cancel"}:
        raise ValueError(
            "Only replay and cancellation are supported operational commands"
        )
    if (
        not isinstance(value["request_id"], str)
        or not 1 <= len(value["request_id"]) <= 128
    ):
        raise ValueError("A stable request ID is required")
    return value


def market_financial_selections(selections):
    from .finance import compile_finance
    from .runtime_core import utc_now

    if (
        not isinstance(selections, dict)
        or not selections
        or set(selections) - {"market_data", "world_bank"}
    ):
        raise ValueError("Financial selection must name a supported source")
    result = {}
    for source, arguments in selections.items():
        if not isinstance(arguments, dict):
            raise ValueError("Financial selection arguments must be structured")
        approved = {
            key: arguments[key]
            for key in ("tickers", "countries", "indicator_codes")
            if key in arguments
        }
        compile_finance(
            "yahoo_history" if source == "market_data" else "world_bank",
            arguments,
            approved,
            20,
            utc_now(),
        )
        result[source] = {"arguments": arguments, "approved_scope": approved}
    return result


class MarketAgentRuntime:
    def setup(self):
        self.source_sha256 = installed_identity()
        self.config = validate_market_config(
            json.loads(
                Path(__file__).with_name("runtime.json").read_text(encoding="utf-8")
            )
        )
        self.services = None

    async def invoke(self, user_query, **kwargs):
        if installed_identity() != self.source_sha256:
            raise ValueError(
                "Executable package changed after setup; refresh before invoking"
            )
        from aidputils.agents.toolkit.agent_helper import pre_invoke_setup

        native_config = pre_invoke_setup(**kwargs)
        from langchain_core.messages import AIMessage

        if not kwargs.get("subject_id"):
            raise ValueError("An authenticated AIDP subject is required")
        if user_query in ("inspect_runtime", "configured_paused"):
            result = {
                "mode": "market_runtime_probe"
                if user_query == "inspect_runtime"
                else (
                    "configured_active"
                    if self.config["research_enabled"]
                    else "configured_paused"
                ),
                "schema_version": 1,
                "versions": runtime_versions(),
                "source_sha256": self.source_sha256,
                "source_identity_version": 2,
                "research_enabled": self.config["research_enabled"],
                "model_calls": 0,
                "apify_starts": 0,
                "business_tables_required": False,
                "capabilities": {"report": report_capabilities()},
                "native_argument_names": sorted(kwargs),
                "authenticated_session_bound": bool(
                    native_config.get("configurable", {}).get("thread_id")
                ),
            }

            async def metadata():
                yield AIMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    additional_kwargs={
                        "ai_mi_event": "result",
                        "ai_mi_response": result,
                    },
                )

            messages = metadata()
        elif user_query == "verify_transport":

            async def transport():
                yield AIMessage(
                    content=market_wire_line(
                        "heartbeat", mode="transport_fixture", synthetic=True
                    )
                )
                yield AIMessage(
                    content=market_wire_line(
                        "response",
                        response={
                            "mode": "transport_fixture",
                            "synthetic": True,
                            "source_keys": ["facebook", "instagram"],
                            "role_key": "financial_analysis",
                            "model_calls": 0,
                            "apify_starts": 0,
                        },
                    )
                )

            messages = transport()
        else:
            if not self.config["research_enabled"]:
                command = market_chat_input(
                    user_query,
                    native_config.get("configurable", {}).get("thread_id"),
                    kwargs.get("trace_id"),
                )

                async def paused():
                    yield AIMessage(
                        content=market_wire_line(
                            "response",
                            request_id=command["request_id"],
                            response={
                                "status": "paused",
                                "research_enabled": False,
                                "model_calls": 0,
                                "apify_starts": 0,
                                "markdown": "La investigación está pausada en este agente TEST. No se consultaron fuentes ni se generó un informe. "
                                "El administrador debe completar la validación y habilitar la investigación antes de realizar consultas.",
                                "references": [],
                                "artifacts": [],
                            },
                        )
                    )

                messages = paused()
            else:
                messages = self.research_messages(user_query, native_config, kwargs)
        diagnostics = {"inspect_runtime", "configured_paused", "verify_transport", "check_services"}
        if isinstance(user_query, str) and not user_query.lstrip().startswith("{") and user_query not in diagnostics:
            messages = self.chat_messages(messages)
        return messages

    async def chat_messages(self, messages):
        from langchain_core.messages import AIMessage

        async with aclosing(messages):
            async for message in messages:
                text = market_chat_text(json.loads(message.content))
                if text is not None:
                    yield AIMessage(content=text)

    async def research_messages(self, user_query, native_config, kwargs):
        from aidputils.agents.toolkit.agent_helper import (
            pre_tool_setup,
            post_tool_setup,
        )
        from langchain_core.messages import AIMessage

        token = pre_tool_setup(**kwargs)
        try:
            if self.services is None:
                self.services = build_market_services(self.config["services"])
            if user_query == "check_services":
                tools = await self.services["mcp"].get_tools(
                    server_name="AiMarketIntelligenceApify", use_cached=False
                )
                required = {"call-actor", "get-actor-run", "get-dataset-items"}
                if not required <= {tool.name for tool in tools}:
                    raise ValueError(
                        "The native MCP does not expose the reviewed capabilities"
                    )
                yield AIMessage(
                    content=json.dumps(
                        {
                            "mode": "market_services_ready",
                            "mcp_verified": True,
                            "exact_runtime_verified": True,
                            "model_calls": 0,
                            "apify_starts": 0,
                            "business_tables_required": False,
                            "source_sha256": self.source_sha256,
                            "source_identity_version": 2,
                        }
                    )
                )
                return
            async with aclosing(
                self.research(user_query, native_config, kwargs)
            ) as stream:
                async for text in stream:
                    yield AIMessage(content=text)
        finally:
            post_tool_setup(token, kwargs=kwargs)

    def request_store(self, scope, request):
        config = self.config["services"]
        return MarketRunStore(
            MarketObjectSnapshot(
                self.services["storage"],
                config["namespace"],
                config["bucket"],
                scope,
                request["revision"],
                request["request_id"],
                "request",
                session_binding=request.get("session_binding"),
            ),
            request["revision"],
        )

    async def research(self, user_query, native_config, kwargs):
        binding = self.config["services"]
        session_id = native_config.get("configurable", {}).get("thread_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("An authenticated native session is required")
        scope = Scope(
            binding["tenant_id"],
            hashlib.sha256(str(kwargs["subject_id"]).encode()).hexdigest(),
            binding["generation_id"],
        )
        command = market_chat_input(user_query, session_id, kwargs.get("trace_id"))
        session = MarketSession(
            self.services["storage"],
            binding["namespace"],
            binding["bucket"],
            scope,
            session_id,
        )
        if "action" in command:
            request = session.lookup(command["request_id"])
            store = self.request_store(scope, request)
            if command["action"] == "cancel":
                store.begin(request["request_id"], request["question"], delivery=request.get("delivery"))
                response, event = store.cancel()
                value, _ = store.read()
                session.finish(request["request_id"], value["brief"])
                if event is not None:
                    yield market_event_packet(store, event)
                yield market_wire_line(
                    "response", request_id=request["request_id"], response=response
                )
            else:
                yield market_replay_packet(
                    store, request["request_id"], command.get("after_sequence", 0)
                )
            return
        try:
            request = session.reserve(
                command["request_id"],
                command["question"],
                binding["candidate_revision"],
                selections=command.get("financial_selections"),
                delivery=command.get("delivery"),
            )
        except MarketSessionPending as error:
            yield market_wire_line(
                "response",
                request_id=command["request_id"],
                response={
                    "status": "pending",
                    "markdown": str(error),
                    "references": [],
                    "artifacts": [],
                    "pending_request_id": error.request_id,
                    "resumable": True,
                    "continuation_safe": False,
                },
            )
            return
        store = self.request_store(scope, request)
        prior_operations = self.recovery_operations(session, scope, request)
        effects = MarketEffects(
            MarketObjectSnapshot(
                self.services["storage"],
                binding["namespace"],
                binding["bucket"],
                scope,
                request["revision"],
                request["request_id"],
                "effects",
                session_binding=request.get("session_binding"),
            ),
            request["revision"],
            request["request_id"],
            prior_operations=prior_operations,
        )
        collector = MarketCollector(
            self.services["provider"],
            effects,
            explicit_urls=market_question_urls(request["question"]),
            search_context=binding["search_context"],
            financial_selections=request.get("selections"),
        )
        workflow = MarketWorkflow(
            store,
            MarketRecordedModel(self.services["model"], effects),
            collector,
            enabled_sources=binding["enabled_sources"],
            conversation_context={
                **(request["context"] or {}),
                "scope_defaults": binding["search_context"],
                "financial_selections": request.get("selections"),
            },
            prior_report=self.saved_report(session, scope, request),
            report_loader=lambda: self.saved_reports(session, scope, request),
        )
        graph = workflow.build(
            native_memory(
                scope.generation_id,
                "ai_market_intelligence",
                request_id=request["request_id"],
            )
        )
        config = root_graph_config(native_config)
        config["configurable"]["thread_id"] = digest(
            [scope.__dict__, session_id, request["request_id"]]
        )
        try:
            async with aclosing(
                market_stream_graph(
                    graph,
                    store,
                    {
                        "request_id": request["request_id"],
                        "question": request["question"],
                        **({"delivery": request["delivery"]} if request.get("delivery") else {}),
                    },
                    config,
                )
            ) as stream:
                async for text in stream:
                    packet = json.loads(text)
                    if packet["kind"] == "response":
                        if packet["response"].get("status") == "pending":
                            packet["response"]["continuation_safe"] = self.capture_continuation_safe(effects)
                            text = market_wire_line("response", request_id=request["request_id"],
                                                    response=packet["response"])
                        self.finish_response(session, request, store, effects)
                    yield text
        except MarketEffectPending as error:
            yield market_wire_line(
                "response",
                request_id=request["request_id"],
                response={
                    "status": "pending",
                    "markdown": str(error),
                    "references": [],
                    "artifacts": [],
                    "continuation_safe": False,
                },
            )
            return

    @staticmethod
    def capture_continuation_safe(effects):
        recorded, _ = effects.read()
        pending = [row for row in (recorded or {}).get("operations", {}).values()
                   if row["state"] not in {"completed", "rejected"}]
        if not pending:
            return False
        for row in pending:
            receipt = row.get("receipt") or {}
            diagnostic = (row.get("result") or {}).get("diagnostic")
            if (row["kind"] != "apify" or row["state"] != "running"
                    or not isinstance(receipt.get("runId"), str) or not receipt["runId"]
                    or receipt.get("status") not in {"READY", "RUNNING", "SUCCEEDED"}
                    or diagnostic is not None and diagnostic.get("retryable") is not True):
                return False
        return True

    @staticmethod
    def finish_response(session, request, store, effects):
        value, _ = store.read()
        if value["response"] is not None:
            coverage = value["response"].get("coverage", {})
            session.finish(
                request["request_id"],
                value["brief"],
                report_available=value["response"]["status"] in {"complete", "partial"}
                and value["response"].get("kind", "research_report")
                == "research_report",
                reused_report_id=coverage.get("reused_request_id")
                if coverage.get("transformation_verified")
                else None,
            )
        elif value["status"] == "failed":
            recorded, _ = effects.read()
            if all(
                row["state"] in {"completed", "rejected"}
                for row in (recorded or {}).get("operations", {}).values()
            ):
                session.finish(request["request_id"], value["brief"])

    def recovery_operations(self, session, scope, request):
        previous = session.previous_request(request["question"], request["request_id"])
        if previous is None:
            return None
        previous_state, _ = self.request_store(scope, previous).read()
        if (
            not previous_state
            or previous_state["status"] != "cancelled"
            or not any(
                event["type"] == "run.failed" for event in previous_state["events"]
            )
        ):
            return None
        binding = self.config["services"]
        snapshot = MarketObjectSnapshot(
            self.services["storage"],
            binding["namespace"],
            binding["bucket"],
            scope,
            previous["revision"],
            previous["request_id"],
            "effects",
            session_binding=previous.get("session_binding"),
        )
        old, _ = snapshot.read()
        expected = MarketEffects(
            snapshot, previous["revision"], previous["request_id"]
        ).binding
        if old and old.get("binding") == expected:
            return old["operations"]
        return None

    def saved_report(self, session, scope, request):
        pointer = (request.get("context") or {}).get("report")
        candidates = (
            [pointer] if pointer else session.finished_requests(request["request_id"])
        )
        for candidate in candidates:
            previous = session.lookup(candidate["request_id"])
            value, _ = self.request_store(scope, previous).read()
            if (
                value
                and (value.get("response") or {}).get("status")
                in {"complete", "partial"}
                and value["response"].get("kind", "research_report")
                == "research_report"
            ):
                session.remember_report(previous["request_id"])
                return value
        return None

    def saved_reports(self, session, scope, request):
        reports = []
        for candidate in session.finished_requests(request["request_id"])[:8]:
            previous = session.lookup(candidate["request_id"])
            value, _ = self.request_store(scope, previous).read()
            if (
                value
                and (value.get("response") or {}).get("status")
                in {"complete", "partial"}
                and value["response"].get("kind", "research_report")
                == "research_report"
            ):
                reports.append(value)
        return reports


def market_question_urls(question):
    import re
    from .runtime_core import public_url

    return [
        public_url(url.rstrip(".,;:!?"))
        for url in re.findall(r'https://[^\s<>"\)]+', question)
    ]
