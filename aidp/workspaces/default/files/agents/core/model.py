"""Structured model calls with bounded data and injected decision instructions."""

import json
import asyncio
import copy

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


class OpenAIModelError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"OpenAI API key inference returned HTTP {status}")


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
        payload = {
            "model": self.model_id,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}],
            "max_completion_tokens": self.max_tokens,
            "store": False,
            "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "ai_mi_decision", "strict": True, "schema": openai_schema(schema),
            }},
        }
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
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise ValueError("OpenAI model response exceeds the admitted size")
                    if on_response:
                        on_response(response.headers.get("x-request-id"))
        except httpx.TimeoutException:
            raise TimeoutError("OpenAI API key inference timed out; usage is uncertain") from None
        except httpx.RequestError:
            raise RuntimeError("OpenAI API key inference transport failed; usage is uncertain") from None
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
    for key in ("default", "uniqueItems", "minLength", "maxLength"):
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
