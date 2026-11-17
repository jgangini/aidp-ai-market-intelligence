"""Public prose is projected from real incremental JSON content, never private fields."""

import json
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import httpx
import pytest

from ai_market_intelligence.runtime import model as runtime_model
from ai_market_intelligence.runtime.research_contracts import MarketPublicBlock
from tests.runtime.test_market_intelligence_api_key_model import FAKE_KEY, binding


def chunk(content=None, *, finish=None, role=None, usage=None, **delta):
    if content is not None:
        delta["content"] = content
    if role is not None:
        delta["role"] = role
    return {"id": "fixture-completion", "object": "chat.completion.chunk",
            "choices": [] if usage is not None else [{"index": 0, "delta": delta, "finish_reason": finish}],
            "usage": usage}


def event(value):
    return b"data: " + (value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()) + b"\n\n"


class Pieces(httpx.SyncByteStream):
    def __init__(self, fragments):
        self.fragments = fragments

    def __iter__(self):
        yield from self.fragments


def read_stream(fragments, output):
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(200, request=request, stream=Pieces(fragments))
    try:
        return runtime_model.completion_text(runtime_model._stream_completion(response, output.append))
    finally:
        response.close()


@pytest.mark.parametrize("ascii_json", [True, False])
def test_public_projection_ignores_private_and_nested_fields_with_split_unicode(ascii_json):
    expected = 'Hola "lector" 🕷️\nfin'
    body = json.dumps({"limitations": ["private"], "other": {"text": "private nested"},
                       "text": expected, "evidence_ids": ["private-id"]}, ensure_ascii=ascii_json)
    payload = event(chunk(role="assistant", reasoning_content="private reasoning"))
    payload += b"".join(event(chunk(char)) for char in body)
    payload += event(chunk(finish="stop")) + event(chunk(usage={"total_tokens": 19})) + event(b"[DONE]")
    output = []
    parsed = json.loads(read_stream([payload[n:n+3] for n in range(0, len(payload), 3)], output))
    assert "".join(output) == expected and len(output) > 2
    assert parsed["text"] == expected
    assert not any("private" in delta for delta in output)


@pytest.mark.parametrize("text", ['<script>bad</script>', 'a```code', '\ud800x', '\udc00'])
def test_public_projection_rejects_unsafe_or_invalid_prose(text):
    projection = runtime_model._PublicTextProjection(lambda text: None)
    with pytest.raises(ValueError):
        for char in json.dumps({"text": text}):
            projection.feed(char)
        projection.finish()


@pytest.mark.parametrize("tail", [
    b"", event(chunk(finish="length")) + event(b"[DONE]"),
    event(chunk(refusal="not public")) + event(b"[DONE]"),
    event(chunk(tool_calls=[{"function": {"arguments": "private"}}])) + event(b"[DONE]"),
    event(b"[DONE]"), event(chunk(finish="stop")) + b"data: [DONE]",
])
def test_incomplete_or_refused_stream_never_becomes_a_completed_decision(tail):
    output = []
    prefix = event(chunk('{"text":"Provisional","evidence_ids":[],"limitations":[]}', role="assistant"))
    with pytest.raises(ValueError):
        read_stream([prefix, tail], output)
    assert output == ["Provisional"]


def test_public_projection_rejects_duplicate_root_text_instead_of_replacing_visible_text():
    projection = runtime_model._PublicTextProjection(lambda text: None)
    with pytest.raises(ValueError, match="Duplicate"):
        projection.feed('{"text":"first","text":"second"}')


def test_public_projection_is_bounded_and_never_emits_the_overflow_character():
    output = []
    projection = runtime_model._PublicTextProjection(output.append)
    projection.feed('{"text":"'+'a'*20000)
    with pytest.raises(ValueError, match="prose"):
        projection.feed('b"}')
    assert "".join(output) == 'a'*20000


@pytest.mark.parametrize("mutate", [
    lambda row: {**row, "id": "another-completion"},
    lambda row: {**row, "choices": row["choices"]*2},
    lambda row: {**row, "choices": [{**row["choices"][0], "index": 1}]},
    lambda row: {**row, "choices": [{**row["choices"][0], "delta": {"role": "user", "content": "private"}}]},
])
def test_foreign_completion_choice_or_role_cannot_contribute_public_text(mutate):
    output = []
    prefix = event(chunk(role="assistant"))
    with pytest.raises(ValueError):
        read_stream([prefix, event(mutate(chunk('{"text":"private"}')))], output)
    assert output == []


def test_actual_http_produces_multiple_public_fragments_before_provider_finishes():
    seen = Event()
    release = Event()
    output = []
    received = []
    headers = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.send_header("x-request-id", "fixture-request")
            self.end_headers()
            for value in (chunk(role="assistant"), chunk('{"text":"Hola '), chunk('mundo')):
                self.wfile.write(event(value))
                self.wfile.flush()
            if not release.wait(10):
                return
            for value in (chunk('","evidence_ids":["id"],"limitations":[]}'),
                          chunk(finish="stop"), chunk(usage={"total_tokens": 42}), b"[DONE]"):
                self.wfile.write(event(value))
                self.wfile.flush()

    def delta(text):
        output.append(text)
        if len(output) == 2:
            seen.set()

    model = runtime_model.OpenAIModel(binding()["services"], FAKE_KEY)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    model.endpoint = f"http://127.0.0.1:{server.server_port}/chat/completions"

    def invoke():
        token = runtime_model.PUBLIC_TEXT_DELTA.set(delta)
        try:
            return model.complete("public instructions", "public question", MarketPublicBlock.model_json_schema(),
                on_response=headers.append, on_completion=lambda text, usage: (json.loads(text), usage))
        finally:
            runtime_model.PUBLIC_TEXT_DELTA.reset(token)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(invoke)
            try:
                assert seen.wait(8), "Public deltas must arrive while the provider response is open"
                assert not pending.done() and "".join(output) == "Hola mundo"
                assert headers == ["fixture-request"]
            finally:
                release.set()
            result, usage = pending.result(timeout=8)
        assert result == {"text": "Hola mundo", "evidence_ids": ["id"], "limitations": []}
        assert usage == {"total_tokens": 42}
        assert len(received) == 1 and received[0]["stream"] is True
        assert received[0]["stream_options"] == {"include_usage": True}
        assert received[0]["model"] == model.model_id
        assert "reasoning_effort" not in received[0] and "temperature" not in received[0]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_private_schema_cannot_accidentally_enter_public_streaming():
    model = runtime_model.OpenAIModel(binding()["services"], FAKE_KEY)
    token = runtime_model.PUBLIC_TEXT_DELTA.set(lambda text: pytest.fail("private output"))
    try:
        with pytest.raises(ValueError, match="prose schema"):
            model.complete("s", "q", {"type": "object", "properties": {"secret": {"type": "string"}}})
    finally:
        runtime_model.PUBLIC_TEXT_DELTA.reset(token)
