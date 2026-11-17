"""OpenAI API key transport and binding tests; no credentials or network requests."""

import asyncio
import copy
import hashlib
import json
import sys
from types import ModuleType, SimpleNamespace

import httpx
import oci
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from ai_market_intelligence.runtime import model as api_key_model, model_bridge
from ai_market_intelligence.runtime.config import validate_market_config
from ai_market_intelligence.runtime.config import (
    OPENAI_MODE, LLM_CREDENTIAL_NAME, OPENAI_ENDPOINT,
)
from tests.runtime.test_market_intelligence_research_runtime import market_active_config
from ai_market_intelligence.runtime.research_contracts import MarketUnderstanding, MarketSourceSelection

FAKE_KEY = "fixture-credential-not-a-real-key"


def binding():
    config = market_active_config()
    config["schema_version"] = 2
    config["services"].update(llm_auth_mode=OPENAI_MODE, llm_credential_name=LLM_CREDENTIAL_NAME,
                              model_endpoint=OPENAI_ENDPOINT, guardrails_config={"policies": []})
    return config


def result(content='{"ok":true}', finish="stop", **message):
    return {"choices": [{"finish_reason": finish, "message": {
        "role": "assistant", "content": content, **message,
    }}]}


@pytest.fixture
def transport(monkeypatch):
    original = httpx.Client
    requests = []

    def use(responses):
        queue = iter(responses)

        def handler(request):
            requests.append(request)
            response = next(queue)
            if isinstance(response, Exception):
                raise response
            return response

        def client(**kwargs):
            assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
            return original(**kwargs, transport=httpx.MockTransport(handler))

        monkeypatch.setattr(api_key_model.httpx, "Client", client)
        return requests

    return use


def test_api_key_schema_is_explicit_and_preserves_original_iam_binding():
    config = binding()
    assert validate_market_config(config) == config
    original = market_active_config()
    assert validate_market_config(original) == original
    paused = {"schema_version": 2, "research_enabled": False, "services": {
        key: config["services"][key] for key in ("model_id", "region", "compartment_id", "llm_auth_mode")}}
    assert validate_market_config(paused) == paused
    config["schema_version"] = 1
    with pytest.raises(ValueError):
        validate_market_config(config)


@pytest.mark.parametrize("changes", [
    {"llm_auth_mode": "oci_api_key"}, {"llm_credential_name": "unrelated"},
    {"model_endpoint": "https://example.com/20231130/actions/v1"},
    {"model_endpoint": "https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com/20231130/actions/v1"},
    {"model_endpoint": OPENAI_ENDPOINT + "?token=private"},
    {"guardrails_config": {"policies": ["existing-policy"]}},
    {"api_key": FAKE_KEY},
])
def test_configuration_rejects_secret_or_unreviewed_connection(changes):
    config = binding()
    config["services"].update(changes)
    with pytest.raises(ValueError):
        validate_market_config(config)


def test_native_client_sends_one_strict_request_and_returns_decision(transport):
    calls = transport([httpx.Response(200, json=result())])
    model = api_key_model.OpenAIModel(binding()["services"], FAKE_KEY)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"], "additionalProperties": False}
    answer = asyncio.run(model.with_structured_output(schema, "json_schema").ainvoke(
        [SystemMessage(content="instructions"), HumanMessage(content="input")]))
    assert answer == {"ok": True} and len(calls) == 1
    assert str(calls[0].url) == OPENAI_ENDPOINT + "/chat/completions"
    assert calls[0].headers["authorization"] == "Bearer " + FAKE_KEY
    payload = json.loads(calls[0].content)
    assert payload["response_format"]["json_schema"] == {
        "name": "ai_mi_decision", "strict": True, "schema": schema}
    assert payload["stream"] is False  # Public progress remains the workflow's event stream.
    assert payload["max_completion_tokens"] == 6000 and payload["store"] is False
    assert "temperature" not in payload and "max_tokens" not in payload
    assert FAKE_KEY not in repr(model) and "api_key" not in payload


def test_openai_payload_adapts_optional_fields_without_changing_domain_contract(transport):
    original = MarketUnderstanding.model_json_schema()
    saved = copy.deepcopy(original)
    calls = transport([httpx.Response(200, json=result())])
    api_key_model.OpenAIModel(binding()["services"], FAKE_KEY).complete("s", "q", original)
    generated = json.loads(calls[0].content)["response_format"]["json_schema"]["schema"]
    assert original == saved
    assert set(generated["required"]) == set(original["properties"])
    assert "default" not in generated["properties"]["source_selection"]
    assert generated["properties"]["source_selection"]["enum"] == original["properties"]["source_selection"]["enum"]
    assert generated["additionalProperties"] is False


def test_openai_generation_constraints_keep_domain_rejections():
    schema = MarketSourceSelection.model_json_schema()
    schema["properties"]["candidate_ids"]["uniqueItems"] = True
    projected = api_key_model.openai_schema(schema)
    assert "uniqueItems" not in projected["properties"]["candidate_ids"]
    assert schema["properties"]["candidate_ids"]["uniqueItems"] is True
    for result in ({"candidate_ids": ["a", "a"]}, {"candidate_ids": ["a" * 501]}):
        with pytest.raises(ValueError):
            MarketSourceSelection.model_validate(result)


def test_openai_schema_handles_field_names_and_constants_without_mutation():
    original = {"type": "object", "properties": {"default": {"type": "boolean", "const": False},
        "properties": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}], "default": None}},
        "required": ["default"], "additionalProperties": False}
    generated = api_key_model.openai_schema(original)
    assert generated["properties"]["default"]["enum"] == [False]
    assert set(generated["required"]) == {"default", "properties"}
    assert original["properties"]["default"]["const"] is False


@pytest.mark.parametrize("length", [0, 500, 501, 585])
def test_openai_planning_request_preserves_domain_length_contract(transport, length):
    from jsonschema import Draft202012Validator
    from ai_market_intelligence.runtime import decisions
    from ai_market_intelligence.runtime.research_contracts import MarketPlan

    contract = MarketPlan.model_json_schema()
    decisions.configure_contract("market_plan", contract, {"enabled_sources": ["web"]})
    original = copy.deepcopy(contract)
    calls = transport([httpx.Response(200, json=result())])
    api_key_model.OpenAIModel(binding()["services"], FAKE_KEY).complete("system", "question", contract)
    generated = json.loads(calls[0].content)["response_format"]["json_schema"]["schema"]
    plan = {"tasks": [{"task_id": "web_fixture", "source": "web", "role": "market_analysis",
        "query": {"groups": [["Fixture"]]}, "urls": [], "purpose": "a" * length}]}
    errors = list(Draft202012Validator(generated).iter_errors(plan))
    normalized = decisions.normalize_result("market_plan", plan, {}, contract)
    assert bool(errors) == (length != 500)
    if length == 500:
        assert MarketPlan.model_validate(normalized).tasks[0].purpose == "a" * length
    else:
        with pytest.raises(ValueError):
            MarketPlan.model_validate(normalized)
    assert contract == original
    keywords = generated["$defs"]["MarketTask"]["properties"]["query"]["properties"]["groups"]["items"]["items"]
    assert keywords["minLength"] == 1 and keywords["maxLength"] == 40


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_status_is_safe_and_transport_never_retries_or_follows_redirect(transport, status):
    calls = transport([httpx.Response(status, text=FAKE_KEY, headers={"location": "https://example.com"})])
    model = api_key_model.OpenAIModel(binding()["services"], FAKE_KEY)
    with pytest.raises(api_key_model.OpenAIModelError) as caught:
        model.complete("system", "question", {})
    assert caught.value.status == status and len(calls) == 1
    assert FAKE_KEY not in str(caught.value)


@pytest.mark.parametrize("body", [result(finish="length"), result(refusal="no"),
    result(tool_calls=[{"name": "unknown"}]), result("not JSON"), result("[]"), result('{"x":NaN}'),
    {"choices": []}, {"choices": [result()["choices"][0]] * 2}])
def test_incomplete_or_invalid_decision_is_not_accepted(body):
    with pytest.raises(ValueError, match="complete structured decision"):
        api_key_model.completion_text(json.dumps(body).encode())


def test_response_limit_is_enforced(transport):
    calls = transport([httpx.Response(200, content=b"x" * (api_key_model.MAX_RESPONSE_BYTES + 1))])
    with pytest.raises(ValueError, match="size"):
        api_key_model.OpenAIModel(binding()["services"], FAKE_KEY).complete("s", "q", {})
    assert len(calls) == 1


def test_timeout_remains_uncertain_without_retry_or_error_body(transport):
    calls = transport([httpx.ReadTimeout(FAKE_KEY)])
    with pytest.raises(TimeoutError) as caught:
        api_key_model.OpenAIModel(binding()["services"], FAKE_KEY).complete("s", "q", {})
    assert len(calls) == 1 and FAKE_KEY not in str(caught.value)


def test_network_failure_uses_known_provider_error_without_retry_or_private_message(transport):
    calls = transport([httpx.ConnectError(FAKE_KEY)])
    with pytest.raises(api_key_model.OpenAIModelError) as caught:
        api_key_model.OpenAIModel(binding()["services"], FAKE_KEY).complete("s", "q", {})
    assert caught.value.status is None
    assert len(calls) == 1 and FAKE_KEY not in str(caught.value)


def test_local_bridge_reuses_transport_with_counted_429_and_private_key(monkeypatch, tmp_path, transport):
    calls = transport([httpx.Response(429, text="throttled"), httpx.Response(200, json=result())])
    identity = {"tenancy": "fixture", "user": "fixture", "fingerprint": "fixture"}
    monkeypatch.setattr(oci.config, "from_file", lambda *args: identity)
    monkeypatch.setenv("MARKET_LLM_API_KEY", FAKE_KEY)
    pauses = []

    async def sleep(delay):
        pauses.append(delay)

    monkeypatch.setattr(model_bridge, "sleep", sleep)
    services = binding()["services"]
    services["identity_sha256"] = hashlib.sha256(json.dumps(["fixture"] * 3, separators=(",", ":")).encode()).hexdigest()

    class Tracked:
        _complete = None
        config_file = profile = "fixture"
        calls = 0
        completed_responses = 0
        settings = {"max_tokens": 100, "max_calls": 3}

        async def complete(self, system, question):
            self.calls += 1
            return await asyncio.to_thread(self._complete, system, question)

        def _final_text(self, response):
            self.completed_responses += 1
            return response.choices[0].message.content[0].text

    tracked = Tracked()
    bridge = model_bridge.StructuredBridge(tracked, services, observations_dir=tmp_path)
    try:
        answer = asyncio.run(bridge.with_structured_output({}, "json_schema").ainvoke(
            [SystemMessage(content="s"), HumanMessage(content="q")]))
    finally:
        bridge.close()
    records = [json.loads(path.read_bytes()) for path in sorted(tmp_path.glob("*.json"))]
    assert answer == {"ok": True} and tracked.calls == len(calls) == len(records) == 2
    assert tracked.completed_responses == 1
    assert records[0]["state"] == "HTTP_RATE_LIMITED" and len(pauses) == 1
    assert records[1]["state"] == "COMPLETE_RESPONSE"
    assert FAKE_KEY not in json.dumps(records)
    assert len({row.headers["x-client-request-id"] for row in calls}) == 2


def test_native_factory_reads_separate_llm_credential_and_keeps_storage_identity(monkeypatch, transport):
    from ai_market_intelligence.runtime import services

    config = binding()["services"]
    config["runtime_profile"]["packages"].update(services.TARGET_GRAPH_PACKAGES)
    monkeypatch.setattr(services, "runtime_versions", lambda *args: config["runtime_profile"])
    config["identity_sha256"] = hashlib.sha256(json.dumps(["fixture"] * 3, separators=(",", ":")).encode()).hexdigest()
    reads = []

    def secret(*, name, key):
        reads.append((name, key))
        return FAKE_KEY if name == LLM_CREDENTIAL_NAME else "fixture"

    def forbidden(**kwargs):
        pytest.fail("API key mode must not construct the IAM inference client")

    def mcp_tools(**kwargs):
        return [SimpleNamespace(name=row["tool"]["name"]) for row in kwargs["allowed_tools"]]

    for name, attrs in {
        "aidputils": {"secrets": SimpleNamespace(get=secret)},
        "aidputils.agents.toolkit.agent_helper": {"GenAIChatInvoker": forbidden, "GenerativeAiInferenceV2Client": forbidden},
        "aidputils.agents.toolkit.tool_helper": {"build_structured_tools_from_allowed_mcp_tools": mcp_tools},
        "aidputils.agents.tools.mcp.mcp_service": {"get_mcp_client": lambda **kwargs: object()},
    }.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    signer = object()
    storage = object()
    monkeypatch.setattr(oci.signer, "Signer", lambda *args, **kwargs: signer)

    def storage_client(settings, *, signer):
        assert settings["user"] == "fixture" and settings["key_content"] == "fixture"
        return storage

    monkeypatch.setattr(oci.object_storage, "ObjectStorageClient", storage_client)
    calls = transport([httpx.Response(200, json=result())])
    native = services.build_market_services(config)
    assert native["storage"] is storage
    assert reads.count((LLM_CREDENTIAL_NAME, "api_key")) == 1
    assert (config["credential_name"], "private_key") in reads
    native["model"].prompts["probe"] = "Return the requested decision."
    answer = asyncio.run(native["model"].ask("probe", {"type": "object"}, {}))
    assert answer == {"ok": True} and len(calls) == 1
