"""Structured model calls with bounded data and injected decision instructions."""

import json
import asyncio
import copy
from contextvars import ContextVar

import httpx

from .config import OPENAI_MODE, llm_auth_mode, validate_api_key, validate_llm_binding
from hashlib import sha256
from itertools import zip_longest
from .decisions import PROMPTS
from urllib.parse import urlsplit


def context_size(payload):
    return len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def context_coverage(evidence, selected):
    def counts(rows):
        return {
            stance: sum(
                (row.get("assessment") or {}).get("stance", "unassessed") == stance
                for row in rows
            )
            for stance in ("supports", "refutes", "context", "unclear", "unassessed")
        }

    return {
        "total": len(evidence),
        "included": len(selected),
        "omitted": len(evidence) - len(selected),
        "total_by_stance": counts(evidence),
        "included_by_stance": counts(selected),
    }


def evidence_context(payload, evidence):
    """Bound a decision's view, keeping whole rows and explicit omitted coverage."""
    context = {
        **payload,
        "evidence": evidence,
        "evidence_coverage": context_coverage(evidence, evidence),
    }
    if context_size(context) <= 90000:
        return context
    groups = [
        [
            row
            for row in evidence
            if (row.get("assessment") or {}).get("stance", "unassessed") == stance
        ]
        for stance in ("refutes", "supports", "context", "unclear", "unassessed")
    ]
    selected = []
    context = {
        **payload,
        "evidence": selected,
        "evidence_coverage": context_coverage(evidence, selected),
    }
    if context_size(context) > 90000:
        raise ValueError("Decision metadata exceeds the admitted context")
    for row in (
        row for group in zip_longest(*groups) for row in group if row is not None
    ):
        candidate = [*selected, row]
        proposed = {
            **payload,
            "evidence": candidate,
            "evidence_coverage": context_coverage(evidence, candidate),
        }
        if context_size(proposed) <= 90000:
            context, selected = proposed, candidate
    return context


def content_units_context(payload, evidence, units):
    """Admit optional analysis units without displacing their original observations."""
    context = evidence_context({**payload, "content_units": [],
        "content_unit_coverage": {"total": len(units), "included": 0, "omitted": len(units)}}, evidence)
    allowed = {row["evidence_id"] for row in context["evidence"]}
    parents = set()
    for unit in units:
        if len(context["content_units"]) == 50:
            break
        if not set(unit["evidence_ids"]).issubset(allowed):
            continue
        if unit["is_comments"] and unit["parent_item_id"] not in parents:
            continue
        metadata = {key: unit[key] for key in (
            "unit_id", "item_id", "parent_item_id", "is_comments", "kind",
            "evidence_ids", "content_basis",
        )}
        count = len(context["content_units"]) + 1
        candidate = {**context, "content_units": [*context["content_units"], metadata],
                     "content_unit_coverage": {"total": len(units), "included": count, "omitted": len(units) - count}}
        if context_size(candidate) <= 90000:
            context = candidate
            if not unit["is_comments"]:
                parents.add(unit["item_id"])
    return context


def content_items_context(payload, evidence, items):
    """Keep optional summaries whole, alongside all of their admitted originals."""
    payload = dict(payload)
    blocks = payload.pop("blocks", None)
    if blocks is not None:
        payload.update(blocks=[], block_coverage={"total": len(blocks), "included": 0, "omitted": len(blocks)})
    context = evidence_context({**payload, "content_items": {"items": []},
        "content_item_coverage": {"total": len(items), "included": 0, "omitted": len(items)}}, evidence)
    allowed = {row["evidence_id"] for row in context["evidence"]}
    for block in blocks or []:
        if not set(block["evidence_ids"]).issubset(allowed):
            continue
        selected = [*context["blocks"], block]
        candidate = {**context, "blocks": selected,
                     "block_coverage": {"total": len(blocks), "included": len(selected), "omitted": len(blocks) - len(selected)}}
        if context_size(candidate) <= 90000:
            context = candidate
    for item in items:
        identities = set(item["evidence_ids"]) | set((item.get("comments_analysis") or {}).get("evidence_ids", []))
        if not identities.issubset(allowed):
            continue
        selected = [*context["content_items"]["items"], item]
        candidate = {**context, "content_items": {"items": selected},
                     "content_item_coverage": {"total": len(items), "included": len(selected), "omitted": len(items) - len(selected)}}
        if context_size(candidate) <= 90000:
            context = candidate
    return context


def report_context(payload, evidence):
    """Draft from captured content, without promoting earlier model reasons to facts."""
    rows = []
    for observation in evidence:
        row = dict(observation)
        row["source_domain"] = urlsplit(row["url"]).hostname
        row["url"] = (
            "https://evidence.invalid/"
            + sha256(row["evidence_id"].encode()).hexdigest()
        )
        for field, keys in (
            ("assessment", ("stance", "independence")),
            ("relevance", ("state",)),
        ):
            annotation = row.get(field)
            if isinstance(annotation, dict):
                row[field] = {key: annotation[key] for key in keys if key in annotation}
        rows.append(row)
    metadata = dict(payload)
    metadata.pop("remaining_gap", None)
    if "window" in metadata:
        metadata["requested_publication_window"] = metadata.pop("window")
    context = evidence_context(metadata, rows)
    context["captured_observations_at"] = sorted(
        {
            row["observed_at"]
            for row in context["evidence"]
            if isinstance(row.get("observed_at"), str)
        }
    )
    if context_size(context) > 100000:
        raise ValueError("Report capture metadata exceeds the admitted context")
    return context


def context_limitations(context):
    coverage = context["evidence_coverage"]
    limits = []
    if coverage["omitted"]:
        limits.append(
            f"Análisis parcial: se revisaron {coverage['included']} de {coverage['total']} observaciones "
            "en esta respuesta. La selección incluye distintas valoraciones y no representa la frecuencia "
            "de los temas ni permite descartar contradicciones en lo que quedó fuera."
        )
    video_rows = [
        row
        for row in context["evidence"]
        if (row.get("provenance") or {}).get("content_basis") == "title_and_description"
    ]
    limits.extend(page_content_limitations(context["evidence"]))
    if any(
        (row.get("provenance") or {}).get("text_truncated") is True
        for row in video_rows
    ):
        limits.append(
            "Algunas descripciones de video se analizaron parcialmente; no se revisaron los videos completos."
        )
    if any(row.get("published_at") is None for row in video_rows):
        limits.append(
            "Algunas fechas de video no permiten confirmar su pertenencia al periodo exacto solicitado; se conservan como referencias de fecha incierta."
        )
    if any(
        (row.get("provenance") or {}).get("reused_publication_metadata") is True
        for row in context["evidence"]
    ):
        limits.append(
            "Se incluyen títulos y descripciones guardados en consultas de las últimas 24 horas, con su fecha de captura original. No se volvieron a verificar esos contenidos ni sus métricas; la selección no garantiza el resultado más reciente de toda la plataforma."
        )
    return limits


def page_content_limitations(evidence):
    limits = []
    search_rows = [
        row
        for row in evidence
        if (row.get("provenance") or {}).get("content_basis")
        == "search_title_and_snippet"
    ]
    pages = [
        row
        for row in evidence
        if (row.get("provenance") or {}).get("content_basis")
        not in {"title_and_description", "search_title_and_snippet"}
    ]
    truncated = sum(
        (row.get("provenance") or {}).get("text_truncated") is True for row in pages
    )
    if search_rows:
        limits.append(
            "Se revisaron títulos y fragmentos de resultados de búsqueda; esos metadatos no equivalen a leer las páginas enlazadas."
        )
    if truncated:
        unit = "página" if truncated == 1 else "páginas"
        limits.append(
            f"Se analizaron fragmentos acotados de {truncated} {unit}; no se verificó todo su contenido."
        )
    return limits


def evidence_batches(payload, evidence):
    batches, batch = [], []

    def size(rows):
        return len(
            json.dumps(
                {**payload, "evidence": rows}, ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
        )

    for observation in evidence:
        if batch and (len(batch) == 8 or size([*batch, observation]) > 90000):
            batches.append(batch)
            batch = []
        if size([observation]) > 90000:
            raise ValueError("One observation exceeds the admitted decision context")
        batch.append(observation)
    return [*batches, batch] if batch else batches or [[]]


class DecisionModel:
    def __init__(self, llm, *, prompts=None):
        self.llm = llm
        self.prompts = prompts if prompts is not None else dict(PROMPTS)

    async def ask(self, name, schema, payload):
        from langchain_core.messages import HumanMessage, SystemMessage

        data = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(data.encode("utf-8")) > 100000:
            raise ValueError(
                "Decision context exceeds the admitted size; no model called"
            )
        return await self.llm.with_structured_output(
            schema, method="json_schema"
        ).ainvoke(
            [SystemMessage(content=self.prompts[name]), HumanMessage(content=data)]
        )


MAX_RESPONSE_BYTES = 1024 * 1024
PUBLIC_TEXT_DELTA = ContextVar("market_public_text_delta", default=None)


class _PublicTextProjection:
    """Decode only a root text string; the whole decision is validated at completion."""

    def __init__(self, emit):
        self.emit = emit
        self.stack = []
        self.state = "root"
        self.role = None
        self.key = None
        self.key_chars = []
        self.escape = False
        self.hex_chars = None
        self.high_surrogate = None
        self.seen_text = False
        self.text_closed = False
        self.length = 0
        self.tail = ""

    def _text_char(self, char, output):
        value = ord(char)
        if 0xD800 <= value <= 0xDBFF:
            if self.high_surrogate is not None:
                raise ValueError("Invalid public Unicode sequence")
            self.high_surrogate = value
            return
        if self.high_surrogate is not None:
            if not 0xDC00 <= value <= 0xDFFF:
                raise ValueError("Invalid public Unicode sequence")
            char = chr(0x10000 + ((self.high_surrogate - 0xD800) << 10) + value - 0xDC00)
            self.high_surrogate = None
        elif 0xDC00 <= value <= 0xDFFF:
            raise ValueError("Invalid public Unicode sequence")
        self.length += 1
        if self.length > 20000 or "<" in char or "```" in self.tail + char:
            raise ValueError("Invalid public prose stream")
        self.tail = (self.tail + char)[-2:]
        output.append(char)

    def _escaped_char(self, char):
        if self.hex_chars is not None:
            if char not in "0123456789abcdefABCDEF":
                raise ValueError("Invalid public JSON escape")
            self.hex_chars += char
            if len(self.hex_chars) == 4:
                decoded = chr(int(self.hex_chars, 16))
                self.hex_chars = None
                return decoded
            return None
        self.escape = False
        if char == "u":
            self.hex_chars = ""
            return None
        escapes = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
        if char not in escapes:
            raise ValueError("Invalid public JSON escape")
        return escapes[char]

    def _close_string(self):
        role = self.role
        self.role = None
        if role == "key":
            self.key = json.loads('"' + "".join(self.key_chars[:-1]) + '"')
            self.state = "colon"
        elif len(self.stack) == 1:
            self.state = "comma"
        if role == "text":
            if self.high_surrogate is not None:
                raise ValueError("Incomplete public Unicode sequence")
            self.text_closed = True

    def _string_char(self, char, output):
        if self.role == "key":
            self.key_chars.append(char)
            if len(self.key_chars) > 512:
                raise ValueError("Public decision key exceeds its size bound")
        if self.escape or self.hex_chars is not None:
            decoded = self._escaped_char(char)
            if decoded is not None and self.role == "text":
                self._text_char(decoded, output)
            return
        if char == "\\":
            self.escape = True
        elif char == '"':
            self._close_string()
        elif ord(char) < 32:
            raise ValueError("Invalid public JSON string")
        elif self.role == "text":
            self._text_char(char, output)

    def _open_string(self):
        self.role = "key" if len(self.stack) == 1 and self.state == "key" else "other"
        if self.role == "key":
            self.key_chars = []
        elif len(self.stack) == 1 and self.state == "value" and self.key == "text":
            if self.seen_text:
                raise ValueError("Duplicate public text field")
            self.seen_text = True
            self.role = "text"

    def _close_container(self, char):
        if not self.stack or self.stack.pop() != {"]": "[", "}": "{"}[char]:
            raise ValueError("Invalid public JSON nesting")
        if len(self.stack) == 1:
            self.state = "comma"
        elif not self.stack:
            self.state = "done"

    def _structural_char(self, char):
        if self.state == "root":
            if char != "{":
                raise ValueError("Public decision must be a JSON object")
            self.stack = ["{"]
            self.state = "key"
        elif char == '"':
            self._open_string()
        elif char in "[{":
            self.stack.append(char)
        elif char in "]}":
            self._close_container(char)
        elif len(self.stack) == 1:
            self._root_member_char(char)

    def _root_member_char(self, char):
        if char == ":" and self.state == "colon":
            self.state = "value"
        elif char == ",":
            self.state = "key"
        elif self.key == "text" and self.state == "value":
            raise ValueError("Public text must be a JSON string")

    def feed(self, fragment):
        output = []
        for char in fragment:
            if self.role is not None:
                self._string_char(char, output)
            elif not char.isspace():
                self._structural_char(char)
        if output:
            self.emit("".join(output))

    def finish(self):
        if self.state != "done" or not self.seen_text or not self.text_closed:
            raise ValueError("Incomplete public text decision")


def _sse_data(response):
    buffer = bytearray()
    fields = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise ValueError("OpenAI model response exceeds the admitted size")
        buffer.extend(chunk)
        offset = 0
        while (end := buffer.find(b"\n", offset)) >= 0:
            line = bytes(buffer[offset:end]).rstrip(b"\r").decode("utf-8")
            offset = end + 1
            if not line:
                if fields:
                    yield "\n".join(fields)
                    fields = []
            elif line.startswith("data:"):
                fields.append(line[5:].removeprefix(" "))
            elif not line.startswith(":"):
                raise ValueError("Unexpected OpenAI SSE field")
        del buffer[:offset]
    if buffer or fields:
        raise ValueError("OpenAI stream ended inside an event")


class _CompletionStream:
    def __init__(self, sink):
        self.projection = _PublicTextProjection(sink)
        self.content = []
        self.role = None
        self.stopped = False
        self.identity = None
        self.usage = {}

    def accept(self, chunk):
        self._identify_chunk(chunk)
        choices = chunk.get("choices")
        if choices == [] and self.stopped and isinstance(chunk.get("usage"), dict):
            self.usage = chunk["usage"]
            return
        if self.stopped or not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("Unexpected OpenAI stream choices")
        choice = choices[0]
        if not isinstance(choice, dict) or type(choice.get("index")) is not int or choice["index"] != 0:
            raise ValueError("Unexpected OpenAI stream choice index")
        self._accept_choice(choice)

    def _identify_chunk(self, chunk):
        if not isinstance(chunk, dict) or chunk.get("object") != "chat.completion.chunk":
            raise ValueError("Unexpected OpenAI stream chunk")
        current = chunk.get("id")
        if not isinstance(current, str) or not current or self.identity not in (None, current):
            raise ValueError("OpenAI stream changed completion identity")
        self.identity = current

    def _accept_choice(self, choice):
        self._accept_delta(choice.get("delta"))
        finish = choice.get("finish_reason")
        if finish is not None:
            if finish != "stop" or self.role != "assistant":
                raise ValueError("OpenAI stream did not complete its decision")
            self.stopped = True

    def _accept_delta(self, delta):
        if not isinstance(delta, dict) or any(delta.get(key) for key in ("refusal", "tool_calls", "function_call")):
            raise ValueError("OpenAI stream is not public prose")
        if delta.get("role") not in (None, "assistant"):
            raise ValueError("Unexpected OpenAI stream role")
        self.role = delta.get("role") or self.role
        text = delta.get("content")
        if text is not None:
            if self.role != "assistant" or not isinstance(text, str):
                raise ValueError("Unexpected OpenAI stream content")
            self.content.append(text)
            self.projection.feed(text)

    def finish(self):
        if not self.stopped:
            raise ValueError("OpenAI stream has no completed decision")
        self.projection.finish()
        return json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "role": self.role, "content": "".join(self.content)}}], "usage": self.usage}).encode("utf-8")


def _stream_completion(response, sink):
    completion = _CompletionStream(sink)
    for data in _sse_data(response):
        if data == "[DONE]":
            return completion.finish()
        completion.accept(json.loads(data, parse_constant=reject_nonfinite))
    raise ValueError("OpenAI stream ended before its completion marker")


def _completion_body(response, sink):
    if sink is not None:
        return _stream_completion(response, sink)
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("OpenAI model response exceeds the admitted size")
    return body


class OpenAIModelError(RuntimeError):
    def __init__(self, status=None):
        self.status = status
        super().__init__(f"OpenAI API key inference returned HTTP {status}" if status is not None
                         else "OpenAI API key inference transport failed; usage is uncertain")


class OpenAIModel:
    def __init__(self, binding, api_key, *, max_tokens=6000):
        if llm_auth_mode(binding) != OPENAI_MODE:
            raise ValueError("An explicit OpenAI API key connection is required")
        validate_llm_binding(binding)
        self._api_key = validate_api_key(api_key)
        self.endpoint = binding["model_endpoint"].rstrip("/") + "/chat/completions"
        self.model_id = binding["model_id"]
        self.max_tokens = max_tokens

    def complete(self, system, question, schema, *, request_id=None, on_response=None, on_completion=None):
        sink = PUBLIC_TEXT_DELTA.get()
        if sink is not None and (
            not callable(sink) or schema.get("properties", {}).get("text", {}).get("type") != "string"
        ):
            raise ValueError("Public streaming requires its prose schema and callback")
        payload = {
            "model": self.model_id,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}],
            "max_completion_tokens": self.max_tokens,
            "store": False,
            "stream": sink is not None,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "ai_mi_decision", "strict": True, "schema": openai_schema(schema),
            }},
        }
        if sink is not None:
            payload["stream_options"] = {"include_usage": True}
        headers = {"Authorization": "Bearer " + self._api_key}
        if request_id:
            headers["X-Client-Request-Id"] = request_id
        # No transport retries or redirects: each submission belongs to its caller's ledger.
        try:
            with httpx.Client(timeout=httpx.Timeout(120, connect=10), follow_redirects=False,
                              trust_env=False) as client:
                with client.stream("POST", self.endpoint, json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        raise OpenAIModelError(response.status_code)
                    if on_response:
                        on_response(response.headers.get("x-request-id"))
                    body = _completion_body(response, sink)
        except httpx.TimeoutException:
            raise TimeoutError("OpenAI API key inference timed out; usage is uncertain") from None
        except httpx.RequestError:
            raise OpenAIModelError() from None
        text = completion_text(body)
        if on_completion:
            usage = json.loads(body).get("usage", {})
            return on_completion(text, usage if isinstance(usage, dict) else {})
        return text

    def with_structured_output(self, schema, method):
        if method != "json_schema":
            raise ValueError("Only structured JSON schema decisions are supported")
        parent = self

        class Bound:
            async def ainvoke(self, messages):
                if len(messages) != 2 or [message.type for message in messages] != ["system", "human"]:
                    raise ValueError("Unexpected production decision message contract")
                raw = await asyncio.to_thread(parent.complete, messages[0].content, messages[1].content, schema)
                return json.loads(raw)

        return Bound()


def completion_text(body):
    try:
        data = json.loads(body)
        choices = data["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError
        choice = choices[0]
        message = choice["message"]
        if (choice["finish_reason"] != "stop" or message.get("role") != "assistant" or message.get("refusal")
                or message.get("tool_calls") or message.get("function_call")):
            raise ValueError
        text = message["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError
        parsed = json.loads(text, parse_constant=reject_nonfinite)
        if not isinstance(parsed, dict):
            raise ValueError
        return text
    except (ValueError, TypeError, KeyError, IndexError):
        raise ValueError("OpenAI model did not return one complete structured decision") from None


def reject_nonfinite(value):
    raise ValueError("Nonfinite JSON number")


def openai_schema(schema):
    """Adapt generation constraints; the workflow still validates its original contract."""
    result = copy.deepcopy(schema)
    for key in ("default", "uniqueItems"):
        result.pop(key, None)
    if "const" in result:
        result["enum"] = [result.pop("const")]
    for key in ("properties", "$defs"):
        if key in result:
            result[key] = {name: openai_schema(child) for name, child in result[key].items()}
    if result.get("type") == "object":
        result["required"] = list(result.get("properties", {}))
        result["additionalProperties"] = False
    if isinstance(result.get("items"), dict):
        result["items"] = openai_schema(result["items"])
    if "anyOf" in result:
        result["anyOf"] = [openai_schema(child) for child in result["anyOf"]]
    return result
