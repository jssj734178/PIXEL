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


def test_from_config_picks_navigation_model_and_its_options():
    m = from_config({"navigation": "x-model",
                     "options": {"x-model": {"temperature": 0}, "other": {"a": 1}}})
    assert m.name == "x-model" and m.options == {"temperature": 0}


def test_from_config_without_model_fails():
    with pytest.raises(ModelError):
        from_config({"options": {}})
