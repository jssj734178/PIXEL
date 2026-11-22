"""LlmModel against a stub llm module. No network."""

from dataclasses import dataclass, field

import pytest

from pixel.models import LlmModel, ModelError, StepRequest, from_config
from pixel.tools import menu


@dataclass
class StubTool:
    name: str
    description: str | None = None
    input_schema: dict = field(default_factory=dict)
    implementation: object = "unset"


@dataclass
class StubCall:
    name: str
    arguments: dict


@dataclass
class StubUsage:
    input: int | None
    output: int | None
    details: dict | None


class StubResponse:
    def __init__(self, calls, text="", usage=None):
        self._calls, self._text = calls, text
        self._usage = usage or StubUsage(100, 20, {"thoughtsTokenCount": 7})

    def tool_calls(self):
        return self._calls

    def text(self):
        return self._text

    def usage(self):
        return self._usage


class StubModel:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.calls = response, exc, []

    def prompt(self, prompt, **kw):
        self.calls.append((prompt, kw))
        if self.exc:
            raise self.exc
        return self.response


class StubLlm:
    Tool = StubTool

    def __init__(self, model):
        self.model, self.asked = model, []

    def get_model(self, name):
        self.asked.append(name)
        if name == "missing":
            raise KeyError(name)
        return self.model


REQ = StepRequest("system text", "prompt text", menu(["notepad"]))


def test_maps_tools_and_reads_calls_and_usage():
    stub = StubModel(StubResponse([StubCall("open_app", {"name": "notepad"})]))
    lib = StubLlm(stub)
    m = LlmModel("some-model", {"thinking_level": "low", "temperature": 0}, _llm=lib)
    reply = m.next_action(REQ)

    assert lib.asked == ["some-model"]
    prompt, kw = stub.calls[0]
    assert prompt == "prompt text" and kw["system"] == "system text"
    assert kw["options"] == {"thinking_level": "low", "temperature": 0}
    tools = kw["tools"]
    assert [t.name for t in tools] == [s.name for s in REQ.tools]
    assert all(t.implementation is None for t in tools)  # nothing can run inside llm
    assert tools[2].input_schema["required"] == ["id", "text"]

    assert reply.calls == [("open_app", {"name": "notepad"})]
    assert (reply.input_tokens, reply.output_tokens, reply.thought_tokens) == (100, 20, 7)
    assert reply.model == "some-model"


def test_text_only_reply():
    stub = StubModel(StubResponse([], text="  I think...  ", usage=StubUsage(5, 3, None)))
    reply = LlmModel("m", {}, _llm=StubLlm(stub)).next_action(REQ)
    assert reply.calls == [] and reply.text == "I think..." and reply.thought_tokens is None


def test_exceptions_become_model_error():
    stub = StubModel(exc=RuntimeError("429 quota exceeded"))
    with pytest.raises(ModelError, match="429 quota exceeded"):
        LlmModel("m", {}, _llm=StubLlm(stub)).next_action(REQ)


def test_unknown_model_is_model_error():
    with pytest.raises(ModelError, match="unknown model 'missing'"):
        LlmModel("missing", {}, _llm=StubLlm(StubModel())).next_action(REQ)


CFG = {"chain": ["a", "b"],
       "providers": {"a": {"model": "x-model", "timeout": 20, "options": {"temperature": 0}},
                     "b": {"model": "y:7b", "api_base": "http://localhost:11434/v1",
                           "local": True, "timeout": 120, "options": {"temperature": 0}},
                     "unused": {"model": "z"}}}


def test_from_config_builds_the_chain_in_order():
    chain = from_config(CFG)
    assert chain.name == "a > b"
    a, b = chain.providers
    assert a.model.name == "x-model"
    assert a.model.options == {"temperature": 0, "timeout": 20}   # llm-gemini takes timeout
    assert b.model.name == "y:7b" and b.local
    assert b.model.options == {"temperature": 0}                  # compat: timeout on the client
    assert "b (y:7b, temperature=0, local)" in chain.describe()
    assert chain.recipients() == "a (falling back to b (local))"


@pytest.mark.parametrize("cfg, msg", [
    ({"providers": {}}, "chain"),
    ({"chain": ["a"], "providers": {}}, "no \\[models.providers"),
    ({"chain": ["a"], "providers": {"a": {}}}, "needs model"),
    ({"chain": ["a", "a"], "providers": {"a": {"model": "m"}}}, "twice"),
])
def test_from_config_rejects_bad_chains(cfg, msg):
    with pytest.raises(ModelError, match=msg):
        from_config(cfg)


def test_simulate_fail_wraps_only_the_named_provider():
    chain = from_config(CFG, simulate=["b:timeout@3"])
    a, b = chain.providers
    assert type(a.model).__name__ == "LlmModel"
    assert type(b.model).__name__ == "FailingModel"
    assert (b.model.kind, b.model.start) == ("timeout", 3)
    assert "simulated timeout from call 3" in chain.describe()


def test_simulate_fail_unknown_provider_or_kind():
    with pytest.raises(ModelError, match="not in the chain"):
        from_config(CFG, simulate=["nope"])
    with pytest.raises(ModelError, match="kind must be one of"):
        from_config(CFG, simulate=["a:melted"])


def test_compat_model_sets_key_and_disables_client_retries():
    from pixel.models import _compat_model
    m = _compat_model("groq", {"model": "llama-x", "api_base": "https://api.example/v1",
                               "key": "groq", "timeout": 30})
    assert m.model_name == "llama-x" and m.api_base == "https://api.example/v1"
    assert m.supports_tools
    assert (m.needs_key, m.key_env_var) == ("groq", "GROQ_API_KEY")
    client = m.get_client("not-a-real-key")
    assert client.max_retries == 0 and client.timeout == 30
    assert str(client.base_url).startswith("https://api.example/v1")

    local = _compat_model("ollama", {"model": "q:7b", "api_base": "http://localhost:11434/v1"})
    assert local.needs_key is None and local.key_env_var is None   # never the OpenAI key
    assert "reasoning_effort" not in local.Options.model_fields


def test_compat_reasoning_models_accept_reasoning_effort():
    from pixel.models import _compat_model
    m = _compat_model("groq", {"model": "openai/gpt-oss-120b", "api_base": "https://x/v1",
                               "key": "groq", "reasoning": True})
    opts = m.Options(reasoning_effort="low", temperature=0)
    assert opts.reasoning_effort == "low"
