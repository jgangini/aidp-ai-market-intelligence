"""Bounded domain workflow and verified narrative events, independent of managed business tables."""

from __future__ import annotations

import asyncio
import copy
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from . import decisions
from .contracts import FactualReview
from .connectors import compile_keyword_query
from .actor_tools import actor_tool_specifications
from .model import context_limitations, evidence_context
from .media import model_evidence
from .wire import market_reference_manifest
from .runtime_core import ProviderQueryRejected, digest
from .artifacts import market_artifact_data, artifacts
from .research_contracts import (
    MarketAnalysis,
    MarketArtifact,
    MarketBrief,
    MarketCollection,
    MarketEvidence,
    MarketKeywordGroups,
    MarketPlan,
    MarketPublicBlock,
    MarketRepairBundle,
    MarketRepairReview,
    MarketReportChoice,
    MarketScheduleDraft,
    MarketSourceSelection,
    MarketTask,
    market_moment,
)
from .state import MarketCancelled
from .effects import (
    MarketEffectPending,
    Market_MAX_READ_DESTINATIONS,
    Market_READ_BATCH_SIZE,
)
from .financial import market_bind_financial_facts, market_financial_rows
from .collector import market_discovery_limit, market_publication_matches


class MarketTurn(TypedDict, total=False):
    request_id: str
    question: str
    route: str
    task: dict
    analysis: dict
    block: dict
    response: dict
    delivered: bool


class MarketWorkflow:
    def __init__(
        self,
        store,
        model,
        collector,
        *,
        enabled_sources,
        clock=None,
        conversation_context=None,
        prior_report=None,
        report_loader=None,
    ):
        if not enabled_sources or not set(enabled_sources).issubset(
            {
                "web",
                "x",
                "youtube",
                "facebook",
                "instagram",
                "market_data",
                "world_bank",
                "financial_document",
            }
        ):
            raise ValueError("Explicit source capabilities are required")
        self.store, self.model, self.collector = store, model, collector
        self._capture_tasks = {}
        self._capture_slots = asyncio.Semaphore(2)
        self._capture_paused = False
        self._capture_context = ContextVar("market_capture_task", default=None)
        self._worker_source = ContextVar("market_worker_source", default=None)
        self._worker_writer = ContextVar("market_worker_writer", default=None)
        self._decision_slots = asyncio.Semaphore(2)
        self.enabled_sources = tuple(enabled_sources)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.conversation_context = copy.deepcopy(conversation_context or {})
        self.conversation_context.pop("report", None)
        self.prior_report = copy.deepcopy(prior_report)
        self.report_loader = report_loader
        if prior_report is not None:
            if prior_report["scope"] != store.scope.__dict__ or prior_report.get(
                "session_binding"
            ) != getattr(store.snapshot, "session_binding", None):
                raise ValueError(
                    "A saved report belongs to another authenticated session"
                )
            self.conversation_context["prior_report"] = {
                "question": prior_report["question"],
                "brief": prior_report["brief"],
                "status": prior_report["response"]["status"],
                "artifact_kinds": [
                    a["kind"] for a in prior_report["response"].get("artifacts", [])
                ],
                "default_only": True,
            }
        self.conversation_context["history_lookup_available"] = (
            report_loader is not None
        )
        if hasattr(collector, "before_effect"):
            collector.before_effect = self.check_cancelled
        if hasattr(collector, "source_selector"):
            collector.source_selector = self.select_sources
        if hasattr(collector, "passage_selector"):
            collector.passage_selector = self.select_passages
        if hasattr(collector, "document_follow_allowed"):
            collector.document_follow_allowed = self.document_follow_allowed

    async def ask(self, name, schema, payload, *, allowed_evidence=None):
        self.check_cancelled()
        payload = model_evidence(payload)
        contract = schema.model_json_schema()
        decisions.configure_contract(name, contract, payload, allowed_evidence)
        async with self._decision_slots:
            self.check_cancelled()
            result = await self.model.ask(name, contract, payload)
        self.check_cancelled()
        result = decisions.normalize_result(name, result, payload, contract)
        return schema.model_validate(result).model_dump(mode="json")

    def check_cancelled(self):
        if self.current()["cancelled"]:
            raise MarketCancelled("The request was cancelled")

    def cancellation(self):
        from langgraph.config import get_stream_writer

        _, event = self.store.cancel()
        if event is not None:
            get_stream_writer()(event)
        return {"route": "deliver"}

    def current(self):
        value, _ = self.store.read()
        if value is None:
            raise ValueError("Request not initialized")
        return value

    def emit(self, event_type, **values):
        from langgraph.config import get_stream_writer

        event = self.store.append(event_type, **values)
        (self._worker_writer.get() or get_stream_writer())(event)
        return event

    def build(self, checkpointer=None):
        from langgraph.graph import StateGraph, START, END

        builder = StateGraph(MarketTurn)
        nodes = {
            "bind": self.bind,
            "understand": self.understand,
            "plan": self.plan,
            "collect": self.collect,
            "consolidate": self.consolidate,
            "deliver": self.deliver,
            "reuse_report": self.reuse_report,
            "schedule_proposal": self.schedule_proposal,
        }
        defaults = {
            "plan": "collect",
            "consolidate": "deliver",
            "reuse_report": "deliver",
            "schedule_proposal": "deliver",
        }

        def guarded(name, handler):
            from inspect import isawaitable

            async def execute(state):
                try:
                    if name not in {"bind", "deliver"}:
                        self.check_cancelled()
                    updates = handler(state)
                    if isawaitable(updates):
                        updates = await updates
                    if name != "deliver":
                        self.check_cancelled()
                    return {
                        **({"route": defaults[name]} if name in defaults else {}),
                        **updates,
                    }
                except MarketCancelled:
                    await self.close_captures()
                    return self.cancellation()
                except MarketEffectPending:
                    await self.close_captures()
                    return {
                        "route": "deliver",
                        "response": {
                            "status": "pending",
                            "markdown": "Una solicitud anterior al modelo conserva un resultado incierto. No se repitió la llamada.",
                            "references": [],
                            "artifacts": [],
                            "resumable": True,
                        },
                    }
                except (ValueError, TimeoutError) as error:
                    await self.close_captures()
                    if name in {"bind", "deliver"}:
                        raise
                    self.emit(
                        "run.failed",
                        status="failed",
                        text="La investigación se detuvo antes de publicar contenido sin verificar. Se conservaron las evidencias y los recibos.",
                        coverage={
                            "stage": getattr(error, "market_stage", name),
                            "error_type": type(error).__name__,
                            "resumable": True,
                        },
                    )
                    self.store.save(status="failed")
                    return {
                        "route": "deliver",
                        "response": {
                            "status": "failed",
                            "markdown": "No se completó la investigación. Las capturas realizadas se conservaron para diagnóstico y recuperación.",
                            "references": [],
                            "artifacts": [],
                            "failure": {
                                "stage": getattr(error, "market_stage", name),
                                "error_type": type(error).__name__,
                            },
                            "resumable": True,
                        },
                    }
                except asyncio.CancelledError:
                    await self.close_captures()
                    raise
                except Exception:
                    await self.close_captures()
                    raise

            return execute

        for name, handler in nodes.items():
            builder.add_node(name, guarded(name, handler))
        builder.add_edge(START, "bind")
        for name, targets in {
            "bind": [
                "understand",
                "plan",
                "collect",
                "consolidate",
                "deliver",
                "reuse_report",
                "schedule_proposal",
            ],
            "understand": ["plan", "deliver", "reuse_report", "schedule_proposal"],
            "collect": ["consolidate", "deliver"],
            **{
                name: [destination, "deliver"] for name, destination in defaults.items()
            },
        }.items():
            builder.add_conditional_edges(name, lambda state: state["route"], targets)
        builder.add_edge("deliver", END)
        return builder.compile(checkpointer=checkpointer).with_config(
            {
                "recursion_limit": 96
                + 6 * (Market_MAX_READ_DESTINATIONS - Market_READ_BATCH_SIZE)
            }
        )

    def bind(self, state):
        self._capture_paused = False
        value = self.store.begin(
            state["request_id"], state["question"], started_at=self.clock().isoformat()
        )
        if value["plan"] is not None and "completed_tasks" not in value:
            self.store.save(completed_tasks=sorted(self.completed_tasks(value)))
            value = self.current()
        route = (
            "deliver"
            if value["response"] is not None
            else (
                "understand"
                if value["brief"] is None
                else (
                    "plan"
                    if value["plan"] is None
                    else (
                        "consolidate"
                        if len(self.completed_tasks(value)) >= len(value["plan"]["tasks"])
                        else "collect"
                    )
                )
            )
        )
        if (
            value["response"] is None
            and value["brief"]
            and value["brief"].get("intent", "research") != "research"
        ):
            route = value["brief"]["intent"]
        return {"route": route, "analysis": {}, "block": {}, "delivered": False}

    async def understand(self, state):
        brief = await decisions.understand(
            self.ask,
            {
                "question": state["question"],
                "now": self.current()["started_at"],
                "enabled_sources": self.enabled_sources,
                "conversation_context": self.conversation_context,
                "source_query_contracts": self.query_contracts(),
            },
        )
        brief.pop("period_is_explicit")
        brief.pop("source_selection")
        if (
            not brief["clarification"]
            and brief["intent"] == "research"
            and brief["retrieval_mode"] in {"documentary", "financial"}
        ):
            captured = market_moment(self.current()["started_at"]).astimezone(
                timezone.utc
            )
            brief.update(
                start=(captured - timedelta(days=1)).isoformat(),
                end=captured.isoformat(),
            )
        if not set(brief["sources"]).issubset(self.enabled_sources):
            raise ValueError("Intake selected a source outside runtime capabilities")
        self.store.save(brief=brief)
        if brief["clarification"] and brief["intent"] == "research":
            self.emit(
                "clarification.required", text=brief["clarification"], status="pending"
            )
            self.store.save(
                status="needs_clarification",
                response={
                    "status": "needs_clarification",
                    "markdown": brief["clarification"],
                    "references": [],
                    "artifacts": [],
                },
            )
            return {"route": "deliver"}
        return {"route": brief["intent"] if brief["intent"] != "research" else "plan"}

    async def resolve_report(self):
        reports = {}
        loaded = self.report_loader() if self.report_loader else []
        for report in [self.prior_report, *loaded]:
            if report is None:
                continue
            if report["scope"] != self.store.scope.__dict__ or report.get(
                "session_binding"
            ) != getattr(self.store.snapshot, "session_binding", None):
                raise ValueError(
                    "A saved report belongs to another authenticated session"
                )
            reports[report["request_id"]] = report
        if not reports:
            return None
        choice = self.current().get("report_selection")
        if choice is None:
            metadata = [
                {
                    "request_id": key,
                    "question": report["question"][:2000],
                    "brief": {
                        field: report["brief"].get(field)
                        for field in ("entity", "market", "objective", "start", "end")
                    },
                }
                for key, report in reports.items()
            ]
            choice = await decisions.select_report(
                self.ask,
                {
                    "question": self.current()["question"],
                    "brief": {**self.current()["brief"], "clarification": None},
                    "reports": metadata,
                    "default_request_id": self.prior_report["request_id"]
                    if self.prior_report
                    else None,
                },
            )
            if choice["request_id"] is not None and choice["request_id"] not in reports:
                raise ValueError(
                    "Report selection escaped the authenticated conversation"
                )
            self.store.save(report_selection=choice)
        if choice["request_id"] is not None and choice["request_id"] not in reports:
            raise ValueError(
                "The selected report is no longer available; selection cannot change during recovery"
            )
        return reports.get(choice["request_id"])

    async def reuse_report(self, state):
        prior = await self.resolve_report()
        if prior is None or not prior.get("response"):
            text = "No se identificó un informe propio compatible con el encargo. Indica qué informe quieres resumir o comparar; no se inició otra investigación."
            self.emit("clarification.required", text=text, status="pending")
            self.store.save(
                status="needs_clarification",
                response={
                    "status": "needs_clarification",
                    "markdown": text,
                    "references": [],
                    "artifacts": [],
                },
            )
            return {}
        response = self.public_report_metadata(prior["response"])
        ids = {reference["evidence_id"] for reference in response["references"]}
        ids.update(
            identity
            for artifact in response["artifacts"]
            for identity in artifact["evidence_ids"]
        )
        evidence = {
            identity: MarketEvidence.model_validate(
                prior["evidence"][identity]
            ).model_dump(mode="json")
            for identity in ids
        }
        self.store.save(evidence=evidence)
        context = evidence_context(
            {
                "question": state["question"],
                "brief": self.current()["brief"],
                "prior_brief": prior["brief"],
                "prior_report": response["markdown"],
                "limitations": response.get("limitations", []),
                "artifacts": response["artifacts"],
            },
            model_evidence(list(evidence.values())),
        )
        admitted = {row["evidence_id"] for row in context["evidence"]}
        summary = await decisions.summarize(
            self.ask, {**context, "allowed_evidence_ids": sorted(admitted)}
        )
        if ids:
            self.check_block(summary, admitted)
        elif summary["evidence_ids"]:
            raise ValueError("A report without references cannot invent new citations")
        review = await decisions.verify_summary(
            self.ask,
            {
                "block": summary,
                "question": state["question"],
                "brief": self.current()["brief"],
                "prior_brief": prior["brief"],
                "prior_report": response["markdown"],
                "artifacts": response["artifacts"],
                "limitations": response.get("limitations", []),
                "evidence": [
                    evidence[identity] for identity in summary["evidence_ids"]
                ],
            },
        )
        accepted = review["accepted"]
        if not accepted:
            summary = {
                "text": "La transformación solicitada no superó la verificación y no se publicó. El informe original permanece disponible en la conversación.",
                "evidence_ids": [],
                "limitations": [
                    "La respuesta no incluye una transformación verificada del informe seleccionado."
                ],
            }
            response = {**response, "references": [], "artifacts": []}
        limits = list(
            dict.fromkeys(
                [
                    *response.get("limitations", []),
                    *summary["limitations"],
                    "Se reutiliza el informe con sus fechas originales; no se realizaron nuevas capturas.",
                ]
            )
        )
        event_limits = limits[:20]
        self.emit(
            "draft.upsert" if accepted else "source.coverage",
            text=summary["text"],
            evidence_ids=summary["evidence_ids"],
            block_id="report-followup",
            limitations=event_limits,
        )
        artifacts = [
            MarketArtifact.model_validate(artifact).model_dump(mode="json")
            for artifact in response["artifacts"]
        ]
        for artifact in artifacts:
            self.store.artifact(artifact)
            self.emit(
                "artifact.upsert",
                role="analysis_visualization",
                artifact_ids=[artifact["artifact_id"]],
                evidence_ids=artifact["evidence_ids"],
                status="final",
                text=artifact["title"],
            )
        final = {
            "status": response["status"] if accepted else "partial",
            "kind": "report_followup",
            "markdown": summary["text"],
            "references": response["references"],
            "artifacts": artifacts,
            "limitations": limits,
            "coverage": {
                **response.get("coverage", {}),
                "reused_report": prior["run_id"],
                "reused_request_id": prior["request_id"],
                "transformation_verified": accepted,
                "no_new_capture": True,
            },
        }
        self.emit(
            "run.completed",
            status="partial" if final["status"] == "partial" else "final",
            text=final["markdown"],
            evidence_ids=summary["evidence_ids"],
            artifact_ids=[a["artifact_id"] for a in artifacts],
            coverage=final["coverage"],
            limitations=event_limits,
            final_response=final,
        )
        return {"response": final}

    async def schedule_proposal(self, state):
        value = self.current()
        proposal = await decisions.extract_proposal(
            self.ask,
            {
                "question": value["question"],
                "brief": value["brief"],
                "conversation_context": self.conversation_context,
                "enabled_sources": list(self.enabled_sources),
            },
        )
        if not set(proposal["sources"]).issubset(self.enabled_sources):
            raise ValueError("Proposal expanded the enabled sources")
        for field in ("frequency", "timezone", "destination"):
            if proposal[field] is not None and (
                not proposal[field].strip()
                or proposal[field].casefold() not in value["question"].casefold()
            ):
                raise ValueError(
                    "Proposal scheduling details require literal user text"
                )
        text = (
            "Propuesta de investigación recurrente pendiente de confirmación.\n\n"
            f"- Tema: {proposal['entity'] or 'por confirmar'}.\n"
            f"- Objetivo: {proposal['objective'] or 'por confirmar'}.\n"
            f"- Fuentes propuestas: {', '.join(proposal['sources']) or 'por confirmar'}.\n"
            f"- Frecuencia: {proposal['frequency'] or 'por confirmar'}.\n"
            f"- Zona horaria: {proposal['timezone'] or 'por confirmar'}.\n"
            f"- Destino del aviso: {proposal['destination'] or 'por confirmar'}.\n"
            "- Entrega: informe Markdown con evidencias y artefactos.\n\n"
            "No se creó ninguna tarea ni se envió ningún aviso."
        )
        complete = all(
            proposal[field] for field in ("entity", "objective", "frequency")
        )
        response = {
            "status": "complete" if complete else "partial",
            "kind": "schedule_proposal",
            "markdown": text,
            "references": [],
            "artifacts": [],
            "proposal": {
                **proposal,
                "request_text": value["question"],
                "approved": False,
                "effects_created": 0,
            },
        }
        self.emit(
            "run.completed",
            status="final" if complete else "partial",
            text=text,
            coverage={"proposal_only": True, "effects_created": 0},
            final_response=response,
        )
        return {"response": response}

    async def plan(self, state):
        value = self.current()
        plan = await decisions.plan(
            self.ask,
            {
                "question": value["question"],
                "brief": value["brief"],
                "now": value["started_at"],
                "enabled_sources": value["brief"]["sources"],
                "maximum_initial_tasks": len(value["brief"]["sources"]),
                "maximum_queries_per_source": 2,
                "source_query_contracts": self.query_contracts(),
                "available_tools": actor_tool_specifications(value["brief"]["sources"]),
            },
        )
        if {task["source"] for task in plan["tasks"]} != set(value["brief"]["sources"]):
            raise ValueError("Plan must cover every selected source exactly once")
        for task in plan["tasks"]:
            task["role"] = (
                "financial_analysis"
                if task["source"] in {"financial_document", "market_data", "world_bank"}
                else "market_analysis"
            )
        if value["brief"].get("retrieval_mode") == "financial":
            plan["tasks"].sort(key=lambda task: task["source"] != "financial_document")
        self.store.save(plan=plan)
        return {}

    async def select_sources(self, task, brief, candidates):
        if not candidates:
            return []
        result = await decisions.select_sources(
            self.ask,
            {
                "question": self.current()["question"],
                "brief": brief,
                "task": task,
                "candidates": candidates,
                "remaining_gap": self.current()["domain_outcomes"]
                .get(task["source"], {})
                .get("gap"),
                "maximum_targets": self.collector.maximum_targets(task["source"], brief)
                if hasattr(self.collector, "maximum_targets")
                else market_discovery_limit(task["source"], brief),
            },
        )
        return result["candidate_ids"]

    async def select_passages(self, context, candidates):
        value = self.current()
        captured_task = self._capture_context.get()
        task = captured_task or value["plan"]["tasks"][value["task_index"]]
        result = await decisions.select_passages(
            self.ask,
            {
                **context,
                "question": value["question"],
                "task": {
                    **task,
                    "query": value["active_query"]
                    if captured_task is None and value["active_query"] is not None
                    else task["query"],
                },
                "candidates": candidates,
                "maximum_targets": 2,
            },
        )
        return result["candidate_ids"]

    @staticmethod
    def collection_keys(value, task):
        discovery_key = digest(
            [value["run_id"], task, value["brief"]["start"], value["brief"]["end"]]
        )
        batch = (
            value.get("read_batches", {}).get(discovery_key, 0)
            if task["source"] == "web"
            else 0
        )
        return discovery_key, discovery_key + ("-batch-" + str(batch) if batch else "")

    @staticmethod
    def completed_tasks(value):
        if "completed_tasks" in value:
            return set(value["completed_tasks"])
        return {task["task_id"] for task in value["plan"]["tasks"][:value["task_index"]]}

    @staticmethod
    def source_group(source):
        if source in {"x", "facebook", "instagram", "youtube"}:
            return "social"
        if source in {"financial_document", "market_data"}:
            return "finance"
        return "economics" if source == "world_bank" else "web"

    def capture_order(self, value):
        groups = {}
        priority = []
        completed = self.completed_tasks(value)
        for index, task in enumerate(value["plan"]["tasks"]):
            if task["task_id"] in completed:
                continue
            if task["source"] == "financial_document":
                priority.append(index)
            else:
                groups.setdefault(self.source_group(task["source"]), []).append(index)
        return priority + [indexes[offset] for offset in range(max(map(len, groups.values()), default=0))
                           for indexes in groups.values() if offset < len(indexes)]

    async def capture_collection(self, value, task, discovery_key, operation_key):
        async with self._capture_slots:
            if self._capture_paused:
                return None
            token = self._capture_context.set(task)
            try:
                collection = await self.retrieve_collection(value, task, discovery_key, operation_key)
                if collection["status"] == "pending":
                    self._capture_paused = True
                return collection
            except BaseException:
                self._capture_paused = True
                raise
            finally:
                self._capture_context.reset(token)

    async def close_captures(self):
        self._capture_paused = True
        futures = [entry[2] for entry in self._capture_tasks.values()]
        for future in futures:
            if not future.done():
                future.cancel()
        if futures:
            await asyncio.gather(*futures, return_exceptions=True)
        self._capture_tasks.clear()

    def source_graph(self):
        from inspect import isawaitable
        from langgraph.graph import StateGraph, START, END

        builder = StateGraph(MarketTurn)
        def stage(name, handler):
            async def execute(state):
                try:
                    result = handler(state)
                    return await result if isawaitable(result) else result
                except (ValueError, TimeoutError) as error:
                    error.market_stage = name
                    raise
            return execute

        for name, handler in {"collect": self.collect_source, "analyze": self.analyze,
                              "draft": self.draft, "verify": self.verify,
                              "advance": self.advance}.items():
            builder.add_node(name, stage(name, handler))
        builder.add_edge(START, "collect")
        builder.add_conditional_edges("collect", lambda state: state["route"],
                                      {"analyze": "analyze", "end": END})
        builder.add_edge("analyze", "draft")
        builder.add_edge("draft", "verify")
        builder.add_edge("verify", "advance")
        builder.add_conditional_edges("advance", lambda state: state["route"],
                                      {"collect": "collect", "end": END})
        return builder.compile()

    async def run_source(self, graph, task, writer):
        token = self._worker_source.set(task["source"])
        writer_token = self._worker_writer.set(writer)
        try:
            return await graph.ainvoke({"task": task})
        finally:
            self._worker_writer.reset(writer_token)
            self._worker_source.reset(token)

    async def collect(self, state):
        from langgraph.config import get_stream_writer

        value = self.current()
        if len(self.completed_tasks(value)) == len(value["plan"]["tasks"]):
            return {"route": "consolidate"}
        graph = self.source_graph()
        writer = get_stream_writer()
        for index in self.capture_order(value):
            task = value["plan"]["tasks"][index]
            future = asyncio.create_task(self.run_source(graph, task, writer))
            self._capture_tasks[task["task_id"]] = (index, task, future)
        try:
            await asyncio.gather(*(entry[2] for entry in self._capture_tasks.values()))
        except BaseException:
            await self.close_captures()
            raise
        self._capture_tasks.clear()
        value = self.current()
        if len(self.completed_tasks(value)) < len(value["plan"]["tasks"]):
            return {"route": "deliver", "response": {
                "status": "pending", "markdown": "La investigación conserva una operación pendiente.",
                "references": [], "artifacts": [], "resumable": True}}
        return {"route": "consolidate"}

    async def collect_source(self, state):
        self.check_cancelled()
        value = self.current()
        task = copy.deepcopy(state["task"])
        progress = value.get("task_progress", {}).get(task["task_id"], {})
        if progress.get("query") is not None:
            task["query"] = progress["query"]
        task = MarketTask.model_validate(task).model_dump(mode="json")
        discovery_key, operation_key = self.collection_keys(value, task)
        collection = await self.capture_collection(value, task, discovery_key, operation_key)
        if collection is None or collection["status"] == "pending":
            return {"route": "end"}
        if operation_key in self.current().get("query_rejections", []):
            return self.close_rejected_query(self.current(), task, operation_key)
        return {"route": "analyze", "task": task, "analysis": {}, "block": {}}

    async def retrieve_collection(self, value, task, discovery_key, operation_key):
        prior = value["collections"].get(operation_key)
        if prior and prior["status"] != "pending":
            collection = prior
        else:
            self.emit(
                "source.coverage",
                role=task["role"],
                text="Consultando la fuente seleccionada; los hallazgos se mostrarán después de contrastarlos.",
                coverage={"source": task["source"], "group_key": self.source_group(task["source"]),
                          "phase": "retrieving", "operation_key": operation_key},
            )
            try:
                collected = await self.collector.collect(
                    request_id=value["request_id"],
                    run_id=value["run_id"],
                    operation_key=operation_key,
                    task=task,
                    brief=value["brief"],
                    previous_operation_id=prior["operation_id"] if prior else None,
                    **(
                        {"discovery_key": discovery_key}
                        if operation_key != discovery_key
                        else {}
                    ),
                )
            except ProviderQueryRejected:
                collection = MarketCollection(status="unavailable", evidence=[],
                    limitations=["La consulta propuesta no es compatible con esta fuente; no se ejecutó ni se sustituyó por otra. Se conservan los hallazgos previamente verificados."],
                    operation_id=operation_key).model_dump(mode="json")
                self.store.merge_collection(operation_key, collection, query_rejected=True)
                return collection
            collection = MarketCollection.model_validate(collected).model_dump(
                mode="json"
            )
            self.save_collection(value, task, operation_key, collection)
        if collection["status"] != "pending":
            self.emit(
                "source.coverage",
                role=task["role"],
                text=(
                    "Información recuperada de la fuente; pendiente de análisis y verificación."
                    if collection["evidence"]
                    else "La fuente no devolvió evidencia utilizable en esta consulta."
                ),
                coverage={
                    "source": task["source"],
                    "phase": "reused" if prior and prior["status"] != "pending" else "retrieved",
                    "operation_key": operation_key,
                    "collection_status": collection["status"],
                    "evidence_count": len(collection["evidence"]),
                },
                limitations=collection["limitations"][:20],
            )
        if collection["status"] == "pending":
            self.emit(
                "source.coverage",
                role=task["role"],
                status="pending",
                text="La fuente conserva una operación pendiente; se retomará esa misma captura.",
                coverage={"source": task["source"], "state": "pending", "phase": "pending"},
                limitations=collection["limitations"],
            )
        return collection

    def save_collection(self, value, task, operation_key, collection):
        if any(row["source"] != task["source"] for row in collection["evidence"]):
            raise ValueError("Collector returned evidence from another source")
        if any(
            not market_publication_matches(
                task["source"], value["brief"], row["published_at"]
            )
            for row in collection["evidence"]
        ):
            raise ValueError(
                "Collector returned evidence outside the requested publication window"
            )
        self.store.merge_collection(operation_key, collection)

    def close_rejected_query(self, value, task, operation_key):
        notice = "La consulta propuesta no es compatible con esta fuente; no se ejecutó ni se sustituyó por otra. Se conservan los hallazgos previamente verificados."
        outcome = value["domain_outcomes"].get(task["source"], {})
        self.store.merge_source("domain_outcomes", task["source"], {
            **outcome, "status": "partial",
            "gap": " ".join(filter(None, [outcome.get("gap"), notice])),
        })
        self.store.progress_task(task, completed=True)
        return {"route": "end"}

    async def analyze(self, state):
        value = self.current()
        evidence = [
            row
            for row in value["evidence"].values()
            if row["source"] == state["task"]["source"]
        ]
        if state["task"]["source"] == "web":
            _, key = self.collection_keys(value, state["task"])
            latest = {
                row["evidence_id"]
                for row in value["collections"].get(key, {}).get("evidence", [])
            }
            evidence.sort(key=lambda row: row["evidence_id"] not in latest)
        context = evidence_context(
            {
                "question": value["question"],
                "brief": value["brief"],
                "task": state["task"],
                "refinement_allowed": self.can_refine(state["task"]),
                "source_query_contract": self.query_contracts().get(
                    state["task"]["source"], {}
                ),
                "computed_artifacts": market_artifact_data(
                    artifacts(evidence, status="provisional")
                ),
            },
            model_evidence(evidence),
        )
        allowed = {row["evidence_id"] for row in context["evidence"]}
        analysis = await decisions.analyze(self.ask, context, allowed_evidence=allowed)
        for finding in analysis["findings"]:
            if not finding["supports"] or not set(
                finding["supports"] + finding["refutes"]
            ).issubset(allowed):
                raise ValueError(
                    "A finding requires supporting evidence from its own domain collection"
                )
        market_financial_rows(
            analysis["financial_facts"],
            {identity: value["evidence"][identity] for identity in allowed},
        )
        analysis.update(
            evidence_coverage=context["evidence_coverage"],
            context_limitations=context_limitations(context),
        )
        if any(
            fact.get("value_kind") == "reported_text"
            for fact in analysis["financial_facts"]
        ):
            analysis["context_limitations"].append(
                "Los importes cualificados se conservan como texto atribuido; no se convirtieron en puntos exactos ni variaciones calculadas."
            )
        return {"analysis": analysis}

    def query_contracts(self):
        contracts = {
            "x": {
                "scope": "global_topic",
                "forbidden_operators": [
                    "from:",
                    "since:",
                    "until:",
                    "since_time:",
                    "until_time:",
                ],
                "date_filters": "controller_only",
                "maximum_window_days": 31,
                "documentary_reference": "Top keyword matches before capture; diverse publication dates, at most20 posts, not a population time window",
            },
            "youtube": {
                "scope": "title_and_description",
                "date_filters": "controller_only",
                "maximum_window_days": 7,
            },
            "facebook": {
                "scope": "public_keyword_posts",
                "boolean_or": False,
                "date_filters": "client_only",
            },
            "instagram": {
                "scope": "public_posts_from_selected_destinations",
                "discovery": "web_search_then_public_post_reader",
                "content_basis": "captions_and_post_text_not_comments_or_media",
                "boolean_or": False,
                "date_filters": "provider_and_client",
            },
            "web": {
                "scope": "keywords",
                "forbidden_operators": [
                    "before:",
                    "after:",
                    "since:",
                    "until:",
                    "daterange:",
                ],
                "date_filters": "controller_only",
                "maximum_queries": 2,
                "refinement_policy": "once_for_a_gap_after_current_candidates_with_global_budget_remaining",
                "read_policy": "progressive_batches_of_two_up_to_ten_global_destinations",
            },
            "financial_document": {
                "scope": "document_search",
                "date_filters": "controller_only",
            },
        }
        return {
            source: contract
            for source, contract in contracts.items()
            if source in self.enabled_sources
        }

    async def draft(self, state):
        if not state["analysis"]["findings"]:
            return {"block": {}}
        ids = sorted(
            {
                identity
                for finding in state["analysis"]["findings"]
                for identity in finding["supports"] + finding["refutes"]
            }
        )
        block = await decisions.draft(self.ask, self.draft_context(state, ids))
        self.check_block(block, ids)
        return {"block": block}

    def draft_context(self, state, ids):
        value = self.decision_value()
        evidence = [value["evidence"][identity] for identity in ids]
        facts = state["analysis"]["financial_facts"]
        return {
            "question": value["question"],
            "brief": value["brief"],
            "findings": state["analysis"]["findings"],
            "evidence": evidence,
            "retrieved_inventory": self.retrieved_inventory(value),
            "financial_facts": facts,
            "computed_artifacts": market_artifact_data(
                artifacts(
                    [*evidence, *market_financial_rows(facts, value["evidence"])],
                    status="provisional",
                )
            ),
        }

    def check_block(self, block, allowed):
        if not block["evidence_ids"] or not set(block["evidence_ids"]).issubset(
            allowed
        ):
            raise ValueError("Public prose must cite admitted evidence")
        # The UI constructs links from the evidence manifest, never from model-proposed destinations.
        if any(
            marker in block["text"].lower()
            for marker in ("http:", "https:", "javascript:", "data:")
        ):
            raise ValueError("Public prose cannot invent link destinations")

    @staticmethod
    def retrieved_inventory(value):
        rows = list(value["evidence"].values())
        return {
            "observations": len(rows),
            "distinct_urls": len({row["url"] for row in rows}),
            "by_source": {
                source: len({row["url"] for row in rows if row["source"] == source})
                for source in sorted({row["source"] for row in rows})
            },
        }

    def decision_value(self):
        value = self.current()
        source = self._worker_source.get()
        value["evidence"] = {
            identity: row for identity, row in sorted(value["evidence"].items())
            if source is None or row["source"] == source
        }
        return value

    def review_context(
        self,
        block,
        financial_facts=(),
        *,
        units=None,
        review_issues=(),
        rejected_block=None,
    ):
        value = self.decision_value()
        evidence = [value["evidence"][identity] for identity in block["evidence_ids"]]
        uncited = [
            row
            for identity, row in value["evidence"].items()
            if identity not in block["evidence_ids"]
        ]
        context = evidence_context(
            {
                **({"block": block} if units is None else {"units": units}),
                "question": value["question"],
                "brief": value["brief"],
                **(
                    {"review_issues": review_issues, "rejected_block": rejected_block}
                    if review_issues
                    else {}
                ),
                "cited_evidence_ids": block["evidence_ids"],
                "retrieved_inventory": self.retrieved_inventory(value),
                "financial_facts": financial_facts,
                "computed_artifacts": market_artifact_data(
                    artifacts(
                        [
                            *evidence,
                            *market_financial_rows(financial_facts, value["evidence"]),
                        ],
                        status="provisional",
                    )
                ),
            },
            model_evidence([*evidence, *uncited]),
        )
        omitted = [
            row
            for row in evidence
            if row["evidence_id"]
            not in {item["evidence_id"] for item in context["evidence"]}
        ]
        return context, omitted

    async def review_block(
        self, block, financial_facts=(), *, review_issues=(), rejected_block=None
    ):
        value = self.current()
        context, omitted = self.review_context(
            block,
            financial_facts,
            review_issues=review_issues,
            rejected_block=rejected_block,
        )
        if omitted:
            return {
                "accepted": False,
                "issues": [
                    {
                        "claim": block["text"][:2000],
                        "evidence_id": omitted[0]["evidence_id"],
                        "quoted_text": omitted[0]["text"][:250],
                        "reason": "El contexto acotado no incluye todas las evidencias citadas; no se admite una revisión parcial del soporte.",
                    }
                ],
            }
        review = await decisions.verify(self.ask, context)
        for issue in review["issues"]:
            row = value["evidence"].get(issue["evidence_id"])
            if row is None or issue["quoted_text"] not in row["text"]:
                return {**review, "accepted": False}
        return review

    async def supported(self, block, financial_facts=()):
        return (await self.review_block(block, financial_facts))["accepted"]

    async def reviewed_draft(self, state):
        block = state["block"]
        if not block:
            return block, False
        facts = state["analysis"]["financial_facts"]
        review = await self.review_block(block, facts)
        if review["accepted"]:
            return block, True
        ids = sorted(
            {
                identity
                for finding in state["analysis"]["findings"]
                for identity in finding["supports"] + finding["refutes"]
            }
        )
        payload = {
            **self.draft_context(state, ids),
            "rejected_block": block,
            "review_issues": review["issues"],
            "revision": 1,
        }
        inventory = sorted(
            row["evidence_id"]
            for row in self.current()["evidence"].values()
            if row["source"] == state["task"]["source"]
        )
        if not self.store.reserve_draft_repair(
            state["task"]["source"],
            digest(payload),
            evidence_revision=digest(inventory),
        ):
            return block, False
        return await self.repair_draft(state, payload, ids)

    async def repair_draft(self, state, payload, ids):
        if state["task"]["source"] in {"web", "x", "youtube", "facebook", "instagram"}:
            return await self.repair_units(payload, ids)
        corrected = await decisions.draft(self.ask, payload)
        self.check_block(corrected, ids)
        return corrected, await self.supported(
            corrected, state["analysis"]["financial_facts"]
        )

    async def repair_units(self, payload, ids):
        proposed = await decisions.repair_units(self.ask, payload)
        return await self.review_units(payload, proposed["units"], ids)

    async def review_units(self, payload, units, ids):
        units = MarketRepairBundle.model_validate({"units": units}).model_dump(mode="json")["units"]
        identities = [unit["unit_id"] for unit in units]
        if len(set(identities)) != len(identities):
            raise ValueError("Repair units require distinct identities")
        for unit in units:
            self.check_block(unit, ids)
        if not units:
            return {}, False
        combined = self.combine_units(units)
        context, omitted = self.review_context(
            combined,
            payload.get("financial_facts", ()),
            units=units,
            review_issues=payload.get("review_issues", ()),
            rejected_block=payload.get("rejected_block"),
        )
        if omitted:
            return {}, False
        result = await decisions.verify_repair(self.ask, context)
        reviews = result["reviews"]
        if len(reviews) != len(units) or {r["unit_id"] for r in reviews} != set(
            identities
        ):
            raise ValueError("Every repair unit requires exactly one explicit verdict")
        accepted = {review["unit_id"] for review in reviews if review["accepted"]}
        verified = [unit for unit in units if unit["unit_id"] in accepted]
        if not verified:
            return {}, False
        block = self.combine_units(verified)
        block.update(
            repair_unit_ids=sorted(accepted), repair_partial=len(verified) != len(units),
            _verified_units=verified,
        )
        return block, True

    @staticmethod
    def combine_units(units):
        return MarketPublicBlock(
            text="\n\n".join(unit["text"] for unit in units),
            evidence_ids=sorted({i for unit in units for i in unit["evidence_ids"]}),
            limitations=list(
                dict.fromkeys(limit for unit in units for limit in unit["limitations"])
            ),
        ).model_dump(mode="json")

    @staticmethod
    def public_report_metadata(response):
        response = copy.deepcopy(response)
        replacements = {}
        for source, outcome in (
            response.get("coverage", {}).get("domain_outcomes", {}).items()
        ):
            if outcome.get("gap"):
                safe = "La evidencia recuperada no permitió verificar todos los aspectos del encargo."
                replacements[source + ": " + outcome["gap"]] = source + ": " + safe
                outcome["gap"] = safe
        response["limitations"] = [
            replacements.get(limit, limit) for limit in response.get("limitations", [])
        ]
        return response

    async def verify(self, state):
        block, accepted = await self.reviewed_draft(state)
        context_limits = state["analysis"]["context_limitations"]
        partial_repair = (
            "Se excluyeron afirmaciones que no superaron la revisión."
            if block.get("repair_partial")
            else None
        )
        gap = (
            " ".join(
                filter(
                    None, [state["analysis"]["gap"], *context_limits, partial_repair]
                )
            )
            or None
        )
        outcome = {
            "status": "complete" if accepted and not gap else "partial",
            "gap": gap
            or (
                None
                if accepted
                else "No se obtuvo un hallazgo publicable verificado en esta fuente."
            ),
            "context_limitations": context_limits,
            "repair_unit_ids": block.get("repair_unit_ids", []),
            "_verified_units": block.get("_verified_units", []),
            "evidence_coverage": state["analysis"]["evidence_coverage"],
        }
        value = self.current()
        self.store.merge_source("domain_outcomes", state["task"]["source"], outcome)
        if accepted:
            value = self.current()
            block_id = "source-" + state["task"]["source"]
            previous = value["blocks"].get(block_id)
            if not previous or any(
                previous[field] != block[field]
                for field in ("text", "evidence_ids", "limitations")
            ):
                self.emit(
                    "draft.corrected" if previous else "draft.upsert",
                    role=state["task"]["role"],
                    text=block["text"],
                    evidence_ids=block["evidence_ids"],
                    block_id=block_id,
                    supersedes=[previous["event_id"]] if previous else [],
                    limitations=[*context_limits, *block["limitations"]][:20],
                    coverage={
                        "source": state["task"]["source"],
                        "representative": False,
                        "evidence_coverage": state["analysis"]["evidence_coverage"],
                    },
                )
            facts = [
                fact
                for fact in state["analysis"]["financial_facts"]
                if fact["evidence_id"] in block["evidence_ids"]
            ]
            self.store.merge_source("financial_facts", state["task"]["source"], facts)
            self.publish_artifacts(self.current(), "provisional")
        else:
            self.withdraw_stale_block(state)
        return {"block": block}

    def withdraw_stale_block(self, state):
        source = state["task"]["source"]
        block_id = "source-" + source
        previous = self.current()["blocks"].get(block_id)
        if previous is None or previous["status"] == "failed":
            return
        old_total = (
            previous.get("coverage", {}).get("evidence_coverage", {}).get("total", 0)
        )
        coverage = state["analysis"]["evidence_coverage"]
        if coverage["total"] <= old_total:
            return
        self.emit(
            "draft.corrected",
            role=state["task"]["role"],
            status="failed",
            block_id=block_id,
            text="El borrador anterior se retira de la conclusión: se amplió la evidencia y aún no hay una síntesis verificada que la integre.",
            evidence_ids=previous["evidence_ids"],
            supersedes=[previous["event_id"]],
            coverage={
                "source": source,
                "representative": False,
                "evidence_coverage": coverage,
            },
            limitations=[
                "Las conclusiones y gráficos del borrador retirado no forman parte del resultado consolidado."
            ],
        )

    @staticmethod
    def published_blocks(value):
        return [
            block
            for _, block in sorted(value["blocks"].items())
            if block["type"] != "draft.compacted" and block["status"] != "failed"
        ]

    def block_artifacts(self, value, status):
        ids = sorted(
            {
                identity
                for block in self.published_blocks(value)
                for identity in block["evidence_ids"]
            }
        )
        rows = [value["evidence"][identity] for identity in ids]
        facts = [
            fact
            for group in value.get("financial_facts", {}).values()
            for fact in group
            if fact["evidence_id"] in ids
        ]
        return artifacts(
            [*rows, *market_financial_rows(facts, value["evidence"])], status=status
        )

    def publish_artifacts(self, value, status):
        artifacts = self.block_artifacts(value, status)
        for artifact in artifacts:
            self.store.artifact(artifact)
            self.emit(
                "artifact.upsert",
                role="analysis_visualization",
                artifact_ids=[artifact["artifact_id"]],
                evidence_ids=artifact["evidence_ids"],
                status=status,
                text=artifact["title"],
            )
        return artifacts

    def continue_web_reads(self, value, task, analysis):
        if self._capture_paused:
            return False
        if not hasattr(self.collector, "has_unread_candidates"):
            return False
        if not (analysis["gap"] or not analysis["findings"] or task["urls"]):
            return False
        discovery_key, operation_key = self.collection_keys(value, task)
        if not (
            self.collector.has_read_attempt(operation_key)
            and self.collector.has_unread_candidates(
                discovery_key, task, value["brief"]
            )
        ):
            return False
        self.store.advance_read_batch(discovery_key)
        return True

    def advance(self, state):
        value = self.current()
        task = state["task"]
        analysis = state["analysis"]
        if task["source"] == "web" and self.continue_web_reads(value, task, analysis):
            return {"route": "collect"}
        needs_more = (
            task["source"] != "web" or analysis["gap"] or not analysis["findings"]
        )
        if needs_more and self.can_refine(task) and analysis["refinement_query"]:
            self.store.progress_task(task, query=analysis["refinement_query"], gap=analysis["gap"])
            return {"route": "collect"}
        self.store.progress_task(task, completed=True)
        return {"route": "end"}

    def can_refine(self, task):
        if self._capture_paused:
            return False
        if task["source"] in self.current()["refinements"]:
            return False
        if task["source"] == "web":
            value = self.current()
            return (
                not task["urls"]
                and not getattr(self.collector, "explicit_urls", ())
                and task["source"] not in value["refinements"]
                and hasattr(self.collector, "maximum_targets")
                and not (
                    hasattr(self.collector, "has_unresolved_read")
                    and self.collector.has_unresolved_read()
                )
                and self.collector.maximum_targets("web", value["brief"]) > 0
            )
        if task["source"] in {"web", "financial_document"} and hasattr(
            self.collector, "maximum_targets"
        ):
            if (
                self.collector.maximum_targets(task["source"], self.current()["brief"])
                == 0
            ):
                return False
            if not self.document_follow_allowed(task["source"]):
                return False
        return task["source"] not in {"market_data", "world_bank"} and not task["urls"]

    def document_follow_allowed(self, source):
        if source != "financial_document" or not hasattr(
            self.collector, "maximum_targets"
        ):
            return True
        value = self.current()
        pending = [task for task in value["plan"]["tasks"]
                   if task["task_id"] not in self.completed_tasks(value)]
        return (
            not any(other["source"] == "web" for other in pending)
            or self.collector.maximum_targets("web", value["brief"]) > 1
        )

    async def final_block(self, value, blocks, ids, artifacts):
        if ids:
            final = await decisions.consolidate(
                self.ask,
                {
                    "question": value["question"],
                    "brief": value["brief"],
                    "blocks": blocks,
                    "retrieved_inventory": self.retrieved_inventory(value),
                    "evidence": [value["evidence"][identity] for identity in ids],
                    "artifacts": market_artifact_data(artifacts),
                },
            )
            self.check_block(final, ids)
            facts = [
                fact
                for group in value.get("financial_facts", {}).values()
                for fact in group
                if fact["evidence_id"] in final["evidence_ids"]
            ]
            review = await self.review_block(final, facts)
            if not review["accepted"]:
                await self.review_preserved_blocks(blocks, final, review["issues"])
                blocks = self.published_blocks(self.current())
                ids = sorted(
                    {identity for block in blocks for identity in block["evidence_ids"]}
                )
                preserved = "\n\n".join(block["text"] for block in blocks)
                final = {
                    "text": preserved
                    if len(preserved) <= 20000
                    else "La síntesis adicional no superó la verificación. Los hallazgos completos se conservan en los bloques verificados adjuntos.",
                    "evidence_ids": ids,
                    "limitations": [
                        "No se aceptó una síntesis adicional; se conservan los hallazgos previamente verificados."
                    ],
                }
                if not preserved:
                    final["text"] = (
                        "No fue posible publicar hallazgos verificados con los resultados recuperados."
                    )
                    final["limitations"] = [
                        "La revisión final no admitió los hallazgos; no se conservaron afirmaciones ni referencias verificadas."
                    ]
        else:
            final = {
                "text": "No se encontró información verificable que responda al encargo en las fuentes consultadas."
                if value["evidence"]
                else "No se pudo recuperar evidencia suficiente para verificar el encargo en las fuentes consultadas.",
                "evidence_ids": [],
                "limitations": [
                    "Revisar las fuentes y los datos que faltan antes de ampliar la investigación."
                ],
            }
            if any(
                outcome.get("gap")
                == "No se obtuvo un hallazgo publicable verificado en esta fuente."
                for outcome in value["domain_outcomes"].values()
            ):
                final["text"] = (
                    "No fue posible publicar hallazgos verificados con los resultados recuperados."
                )
        return final

    @staticmethod
    def report_text(value, text):
        brief = value["brief"]
        period_label = (
            "Periodo de publicaciones consultado"
            if brief["retrieval_mode"] == "conversation"
            else "Ventana operativa de búsqueda"
        )
        labels = {
            "x": "X", "facebook": "Facebook", "instagram": "Instagram",
            "youtube": "YouTube", "web": "web",
            "financial_document": "documentos financieros",
            "market_data": "datos de mercado", "world_bank": "Banco Mundial",
        }
        sources = sorted({labels[row["source"]] for row in value["evidence"].values()})
        scope = (
            f"{period_label}: {brief['start']} a {brief['end']}. "
            f"Fuentes con evidencia: {', '.join(sources) or 'ninguna'}. "
            f"Evidencias recuperadas: {len(value['evidence'])}. "
            "El alcance se limita a estos resultados."
        )
        if len(text) + len(scope) + 2 > 20000:
            text = (
                "La síntesis excede el tamaño de entrega. Los hallazgos completos "
                "se conservan en los bloques verificados adjuntos."
            )
        return text + "\n\n" + scope

    async def review_preserved_blocks(self, blocks, rejected, issues):
        for previous in blocks:
            token = self._worker_source.set(previous["coverage"]["source"])
            try:
                await self.review_preserved_source(previous, rejected, issues)
            finally:
                self._worker_source.reset(token)

    async def review_preserved_source(self, previous, rejected, issues):
        value = self.current()
        block = {
            field: previous[field]
            for field in ("text", "evidence_ids", "limitations")
        }
        facts = [
            fact
            for group in value.get("financial_facts", {}).values()
            for fact in group
            if fact["evidence_id"] in block["evidence_ids"]
        ]
        source = previous["coverage"]["source"]
        units = value["domain_outcomes"].get(source, {}).get("_verified_units", [])
        combined = self.combine_units(units) if units else None
        if combined and all(combined[field] == block[field] for field in ("text", "evidence_ids")):
            corrected, accepted = await self.review_units(
                {"financial_facts": facts, "review_issues": issues, "rejected_block": rejected},
                units, previous["evidence_ids"],
            )
            self.correct_preserved_block(previous, corrected, accepted)
            return
        review = await self.review_block(
            block, facts, review_issues=issues, rejected_block=rejected
        )
        if review["accepted"]:
            return
        payload, omitted = self.review_context(
            block, facts, review_issues=review["issues"], rejected_block=block
        )
        inventory = sorted(
            row["evidence_id"]
            for row in value["evidence"].values()
            if row["source"] == source
        )
        accepted = False
        if not omitted and self.store.reserve_draft_repair(
            source, digest(payload), evidence_revision=digest(inventory)
        ):
            block, accepted = await self.repair_units(
                payload, previous["evidence_ids"]
            )
        self.correct_preserved_block(previous, block, accepted)

    def correct_preserved_block(self, previous, block, accepted):
        value = self.current()
        source = previous["coverage"]["source"]
        gap = "La síntesis final detectó afirmaciones sin soporte; se revisó el borrador previo."
        self.store.save(
            domain_outcomes={
                **value["domain_outcomes"],
                source: {
                    **value["domain_outcomes"][source],
                    "status": "partial",
                    "gap": gap,
                    "repair_unit_ids": block.get("repair_unit_ids", []) if accepted else [],
                    "_verified_units": block.get("_verified_units", []) if accepted else [],
                },
            }
        )
        self.emit(
            "draft.corrected",
            role=previous["role_key"],
            status="partial" if accepted else "failed",
            block_id=previous["block_id"],
            text=block["text"]
            if accepted
            else "Se retira el borrador: no superó la revisión de soporte.",
            evidence_ids=block["evidence_ids"]
            if accepted
            else previous["evidence_ids"],
            supersedes=[previous["event_id"]],
            coverage=previous["coverage"],
            limitations=block["limitations"] if accepted else [gap],
        )

    def final_response(self, value, final, ids, artifacts):
        pending_or_incomplete = any(
            collection["status"] != "complete"
            for collection in value["collections"].values()
        ) or any(
            value["domain_outcomes"].get(task["source"], {}).get("status") != "complete"
            for task in value["plan"]["tasks"]
        )
        outcomes = copy.deepcopy(value["domain_outcomes"])
        for outcome in outcomes.values():
            outcome.pop("_verified_units", None)
            if outcome["gap"]:
                outcome["gap"] = (
                    "La evidencia recuperada no permitió verificar todos los aspectos del encargo."
                )
        gaps = [
            source + ": " + outcome["gap"]
            for source, outcome in outcomes.items()
            if outcome["gap"]
        ]
        context_limits = [
            limit
            for outcome in outcomes.values()
            for limit in outcome.get("context_limitations", [])
        ]
        collection_limits = [
            limit
            for collection in value["collections"].values()
            for limit in collection["limitations"]
        ]
        evidence_limits = [
            limit for row in value["evidence"].values() for limit in row["limitations"]
        ]
        block_limits = [
            limit
            for block in self.published_blocks(value)
            for limit in block["limitations"]
        ]
        limits = list(
            dict.fromkeys(
                [
                    *final["limitations"],
                    *block_limits,
                    *gaps,
                    *context_limits,
                    *collection_limits,
                    *evidence_limits,
                ]
            )
        )
        reference_ids = sorted(
            set(final["evidence_ids"])
            | {
                identity
                for artifact in artifacts
                for identity in artifact["evidence_ids"]
            }
        )
        read_progress = {}
        if hasattr(self.collector, "effects"):
            attempted = set(self.collector.effects.reserved_read_urls())
            retrieved = {
                row.get("requested_url") or row["url"]
                for row in value["evidence"].values()
            }
            read_progress = {
                "maximum_destinations": Market_MAX_READ_DESTINATIONS,
                "attempted_destinations": len(attempted),
                "retrieved_destinations": len(attempted & retrieved),
                "unretrieved_destinations": len(attempted - retrieved),
            }
        return {
            "status": "partial" if pending_or_incomplete or not ids else "complete",
            "kind": "research_report",
            "markdown": final["text"],
            "references": market_reference_manifest(value["evidence"], reference_ids),
            "artifacts": artifacts,
            "limitations": limits,
            "blocks": list(value["blocks"].values()),
            "coverage": {
                "sources": sorted({task["source"] for task in value["plan"]["tasks"]}),
                "evidence_count": len(value["evidence"]),
                "representative": False,
                "domain_outcomes": outcomes,
                **({"read_progress": read_progress} if read_progress else {}),
            },
        }

    async def consolidate(self, state):
        value = self.current()
        blocks = self.published_blocks(value)
        ids = sorted(
            {identity for block in blocks for identity in block["evidence_ids"]}
        )
        artifacts = self.block_artifacts(value, "final")
        final = await self.final_block(value, blocks, ids, artifacts)
        value = self.current()
        blocks = self.published_blocks(value)
        ids = sorted(
            {identity for block in blocks for identity in block["evidence_ids"]}
        )
        artifacts = self.publish_artifacts(value, "final")
        final["text"] = self.report_text(value, final["text"])
        response = self.final_response(value, final, ids, artifacts)
        event_limits = response["limitations"]
        if len(event_limits) > 20:
            event_limits = [
                *event_limits[:19],
                f"{len(event_limits) - 19} limitaciones adicionales conservadas en la respuesta final.",
            ]
        if blocks:
            self.emit(
                "draft.compacted",
                status="final",
                text=final["text"],
                evidence_ids=final["evidence_ids"],
                block_id="consolidated",
                supersedes=[block["event_id"] for block in blocks],
                limitations=event_limits,
            )
        self.emit(
            "run.completed",
            status="partial" if response["status"] == "partial" else "final",
            text=final["text"],
            evidence_ids=final["evidence_ids"],
            artifact_ids=[artifact["artifact_id"] for artifact in artifacts],
            coverage=response["coverage"],
            limitations=event_limits,
            final_response=response,
        )
        return {"response": response}

    async def deliver(self, state):
        await self.close_captures()
        value = self.current()
        response = value["response"] or state.get("response")
        if response is None:
            raise ValueError(
                "No persisted final response or explicit pending result is available"
            )
        return {"response": response, "delivered": True}
