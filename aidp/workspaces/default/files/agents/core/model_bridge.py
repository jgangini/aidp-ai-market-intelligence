"""Verified structured OCI adapter for current local evaluations."""

from contextvars import ContextVar
from contextlib import ExitStack
from datetime import datetime, timezone
from asyncio import CancelledError, sleep
import hashlib
import json
import os
from random import random
import time
from threading import Lock, local
from uuid import uuid4


LOCAL_MODEL_ATTEMPTS = 3


def update_model_observation(path, record, state, **values):
    record.update(state=state, **values)
    record["history"].append(
        {"state": state, "at": datetime.now(timezone.utc).isoformat()}
    )
    temporary = path.with_suffix(".pending")
    raw = json.dumps(record, ensure_ascii=False)
    for attempt in range(5):
        try:
            temporary.write_text(raw, encoding="utf-8")
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02)


def model_error_metadata(error):
    metadata = {"error_chain": []}
    cause = error
    while cause is not None and len(metadata["error_chain"]) < 5:
        metadata["error_chain"].append(type(cause).__name__)
        status = getattr(cause, "status", None)
        if type(status) is int and 100 <= status <= 599:
            metadata["provider_status"] = status
            metadata.setdefault("provider_statuses", []).append(status)
        cause = cause.__cause__ or cause.__context__
    if cause is not None:
        metadata["error_chain_truncated"] = True
    return metadata


def model_rate_limited(metadata):
    return (
        set(metadata.get("provider_statuses", ())) == {429}
        and not metadata.get("error_chain_truncated")
        and not any("timeout" in name.lower() for name in metadata["error_chain"])
        and not {"CancelledError", "JSONDecodeError"}.intersection(metadata["error_chain"])
    )


class StructuredBridge:
    """Use the worker's counted call and final-response validation with strict JSON."""

    def __init__(self, tracked, binding, *, observations_dir=None):
        self.tracked, self.binding = tracked, binding
        self.observations_dir, self.observation_id, self.sequence = (
            observations_dir,
            uuid4().hex,
            0,
        )
        self.schema = ContextVar("market_decision_schema")
        self.observation = ContextVar("market_model_observation", default=None)
        self._clients = local()
        self._client_lock = Lock()
        self._sessions = ExitStack()
        self._closed = False
        self._tracked_close = getattr(tracked, "close", None)
        tracked.close = self.close
        if hasattr(tracked, "_complete"):
            # Only the request construction differs; complete() still owns admission,
            # counting, errors and _final_text() validates the actual OCI response.
            tracked._complete = self.request

    def close(self):
        with self._client_lock:
            self._closed = True
            sessions = self._sessions.pop_all()
        try:
            sessions.close()
        finally:
            if self._tracked_close is not None:
                self._tracked_close()

    def retry_capacity_available(self):
        settings = getattr(self.tracked, "settings", None)
        calls = getattr(self.tracked, "calls", None)
        limit = settings.get("max_calls") if isinstance(settings, dict) else None
        if type(calls) is int and type(limit) is int:
            return calls < limit
        return True  # complete() remains the admission boundary when capacity is unknown.

    def prepare_observation(self, schema, messages, invocation_id, attempt):
        if self.observations_dir is None:
            return None, None
        self.observations_dir.mkdir(parents=True, exist_ok=True)
        self.sequence += 1
        path = self.observations_dir / f"{self.observation_id}-{self.sequence}.json"
        record = {
            "input_sha256": hashlib.sha256(messages[1].content.encode()).hexdigest(),
            "schema_sha256": hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest(),
            "invocation_id": invocation_id,
            "attempt": attempt,
            "maximum_attempts": LOCAL_MODEL_ATTEMPTS,
            "client_request_id": uuid4().hex,
            "worker_pid": os.getpid(),
            "history": [],
        }
        with path.open("x", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False)
        update_model_observation(path, record, "CALL_PREPARED")
        return path, record

    def record_failure(self, path, record, error, calls_before):
        if path is None:
            return
        metadata = model_error_metadata(error)
        if "response" in record:
            state = "LOCAL_VALIDATION_FAILED"
        elif (model_rate_limited(metadata)
              and any(row["state"] == "HTTP_SUBMISSION_INTENT" for row in record["history"])):
            state = "HTTP_RATE_LIMITED"
        elif (type(calls_before) is int and self.tracked.calls == calls_before
              and not any(row["state"] == "CALL_ADMITTED" for row in record["history"])):
            state = "LOCAL_ADMISSION_REJECTED"
        else:
            state = "FAILED_UNKNOWN_USAGE"
        update_model_observation(path, record, state, error_type=type(error).__name__, **metadata)

    def can_retry(self, record, attempt):
        return (
            record is not None and record["state"] == "HTTP_RATE_LIMITED"
            and attempt < LOCAL_MODEL_ATTEMPTS and self.retry_capacity_available()
        )

    async def wait_for_retry(self, path, record, attempt):
        delay = 2 ** (attempt - 1) + random()
        update_model_observation(path, record, "HTTP_RATE_LIMITED", retry_delay_seconds=delay)
        try:
            await sleep(delay)
        except CancelledError:
            update_model_observation(path, record, "HTTP_RATE_LIMITED", retry_wait_cancelled=True)
            raise
        if not self.retry_capacity_available():
            update_model_observation(path, record, "HTTP_RATE_LIMITED", retry_stopped="model_budget")
            return False
        return True

    def request(self, system, question):
        import oci
        from oci.generative_ai_inference import models

        tracked, settings = self.tracked, self.tracked.settings
        observation = self.observation.get()
        if observation is not None:
            path, record = observation
            update_model_observation(path, record, "CALL_ADMITTED")
        if self._closed:
            raise ValueError("The model adapter is closed")
        if getattr(self._clients, "client", None) is None:
            config = oci.config.from_file(tracked.config_file, tracked.profile)
            identity = hashlib.sha256(
                json.dumps(
                    [config[key] for key in ("tenancy", "user", "fingerprint")],
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if identity != self.binding["identity_sha256"] or self.binding[
                "guardrails_config"
            ] != {"policies": []}:
                raise ValueError(
                    "Local adapter identity or guardrail parity is not verified"
                )
            self._clients.client = oci.generative_ai_inference.GenerativeAiInferenceClient(
                {**config, "region": settings["region"]},
                service_endpoint=self.binding["model_endpoint"],
                retry_strategy=oci.retry.NoneRetryStrategy(),
                timeout=(10, 120),
            )
            with self._client_lock:
                if self._closed:
                    self._clients.client.base_client.session.close()
                    raise ValueError("The model adapter closed before submission")
                self._sessions.callback(self._clients.client.base_client.session.close)
        request = models.GenericChatRequest(
            temperature=0,
            max_tokens=settings["max_tokens"],
            reasoning_effort=settings["reasoning_effort"],
            is_stream=False,
            messages=[
                models.SystemMessage(content=[models.TextContent(text=system)]),
                models.UserMessage(content=[models.TextContent(text=question)]),
            ],
            response_format=models.JsonSchemaResponseFormat(
                json_schema=models.ResponseJsonSchema(
                    name="ai_mi_decision", schema=self.schema.get(), is_strict=True
                )
            ),
        )
        options = {"retry_strategy": oci.retry.NoneRetryStrategy()}
        if observation is not None:
            path, record = observation
            update_model_observation(path, record, "HTTP_SUBMISSION_INTENT")
            options["opc_request_id"] = record["client_request_id"]
        response = self._clients.client.chat(
            models.ChatDetails(
                compartment_id=settings["compartment_id"],
                serving_mode=models.OnDemandServingMode(model_id=settings["model_id"]),
                chat_request=request,
            ),
            **options,
        )
        if observation is not None:
            update_model_observation(
                path,
                record,
                "HTTP_RESPONSE_RECEIVED",
                provider_request_id=(getattr(response, "headers", None) or {}).get(
                    "opc-request-id"
                ),
            )
        return tracked._final_text(response.data.chat_response)

    def with_structured_output(self, schema, method):
        if method != "json_schema":
            raise ValueError(
                "Only the production structured-output contract is supported"
            )
        parent = self

        class Bound:
            async def ainvoke(self, messages):
                if len(messages) != 2 or [message.type for message in messages] != [
                    "system",
                    "human",
                ]:
                    raise ValueError("Unexpected production decision message contract")
                token = parent.schema.set(schema)
                invocation_id = uuid4().hex
                try:
                    for attempt in range(1, LOCAL_MODEL_ATTEMPTS + 1):
                        path, record = parent.prepare_observation(schema, messages, invocation_id, attempt)
                        observation_token = parent.observation.set((path, record)) if path is not None else None
                        calls_before = getattr(parent.tracked, "calls", None)
                        try:
                            raw = await parent.tracked.complete(
                                messages[0].content, messages[1].content
                            )
                            if path is not None:
                                update_model_observation(
                                    path, record, "COMPLETE_RESPONSE", response=raw
                                )
                            return json.loads(raw)
                        except CancelledError as error:
                            parent.record_failure(path, record, error, calls_before)
                            raise
                        except Exception as error:
                            parent.record_failure(path, record, error, calls_before)
                            if not parent.can_retry(record, attempt):
                                raise
                            if not await parent.wait_for_retry(path, record, attempt):
                                raise
                        finally:
                            if observation_token is not None:
                                parent.observation.reset(observation_token)
                finally:
                    parent.schema.reset(token)

        return Bound()
