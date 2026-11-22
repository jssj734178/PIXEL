"""The fallback chain and error classes, with fake providers. No network."""

import pytest

from pixel.models import (Chain, FailingModel, ModelError, ModelReply, Provider, StepRequest,
                          classify, parse_simulate)
from pixel.tools import menu

REQ = StepRequest("system", "prompt", menu(["notepad"]))


# ---------- fake exceptions shaped like httpx / openai / llm ones ----------

class RateLimitError(Exception):          # openai
    status_code = 429


class InternalServerError(Exception):     # openai 5xx
    status_code = 503


class APIConnectionError(Exception):      # openai
    pass


class APITimeoutError(APIConnectionError):  # openai: a timeout is also a connection error
    pass


class ReadTimeout(Exception):             # httpx
    pass


class ConnectError(Exception):            # httpx
    pass


class NeedsKeyException(Exception):       # llm
    pass


class WithResponse(Exception):
    def __init__(self, status):
        super().__init__("http error")
        self.response = type("R", (), {"status_code": status})()


def wrapped(inner):
    """The way LlmModel reports a failure: ModelError raised from the original."""
    try:
        raise ModelError(f"m: {type(inner).__name__}: {inner}") from inner
    except ModelError as exc:
        return exc


@pytest.mark.parametrize("exc, kind", [
    (RateLimitError("slow down"), "rate_limit"),
    (WithResponse(429), "rate_limit"),
    (Exception("You exceeded your current quota, please check your plan"), "rate_limit"),
    (Exception("Resource has been exhausted (e.g. check quota)."), "rate_limit"),
    (Exception("This model is currently experiencing high demand. Spikes in demand are "
               "usually temporary. Please try again later."), "overloaded"),
    (InternalServerError("boom"), "overloaded"),
    (Exception("503 UNAVAILABLE"), "overloaded"),
    (ReadTimeout("The read operation timed out"), "timeout"),
    (APITimeoutError("Request timed out."), "timeout"),
    (ConnectError("[Errno 11001] getaddrinfo failed"), "network"),
    (APIConnectionError("Connection error."), "network"),
    (ConnectionRefusedError("refused"), "network"),
    (NeedsKeyException("No key found - add one using 'llm keys set groq'"), "auth"),
    (WithResponse(401), "auth"),
    (Exception("API key not valid. Please pass a valid API key."), "auth"),
    (Exception("unknown model 'gemini-9': Unknown model"), "bad_model"),
    (WithResponse(404), "bad_model"),
    (Exception("The model `x` has been decommissioned"), "bad_model"),
    (ValueError("something odd"), "other"),
])
def test_classify(exc, kind):
    assert classify(exc) == kind
    assert classify(wrapped(exc)) == kind   # also through LlmModel's wrapping


# ---------- the chain ----------

class Fake:
    def __init__(self, name, *results):
        self.name = name
        self.results = list(results)
        self.requests = []

    def next_action(self, req):
        self.requests.append(req)
        r = self.results.pop(0) if self.results else ModelReply(model=self.name)
        if isinstance(r, BaseException):
            raise r
        return r


def ok(name):
    return ModelReply(calls=[("finish", {"summary": "x"})], model=name)


def chain(*fakes, **kw):
    sleeps = []
    c = Chain([Provider(f.name, f) for f in fakes], sleep=sleeps.append, **kw)
    return c, sleeps


def test_first_provider_answers():
    a, b = Fake("a", ok("a")), Fake("b")
    c, _ = chain(a, b)
    r = c.next_action(REQ)
    assert r.provider == "a" and r.attempts == [] and b.requests == []


def test_rate_limit_moves_the_same_request_on_and_takes_the_provider_out():
    a = Fake("a", wrapped(RateLimitError("429")))
    b = Fake("b", ok("b"), ok("b"))
    c, sleeps = chain(a, b)
    r = c.next_action(REQ)
    assert r.provider == "b" and b.requests == [REQ]           # the same step, unchanged
    assert [(x.provider, x.kind) for x in r.attempts] == [("a", "rate_limit")]
    assert sleeps == []                                         # no same-provider retry
    r2 = c.next_action(REQ)                                     # next step skips a
    assert len(a.requests) == 1 and r2.provider == "b"
    assert r2.attempts[0].skipped and r2.attempts[0].kind == "rate_limit"


@pytest.mark.parametrize("exc", [InternalServerError("x"), NeedsKeyException("no key"),
                                 WithResponse(404)])
def test_overload_auth_and_bad_model_are_sticky(exc):
    a, b = Fake("a", wrapped(exc)), Fake("b", ok("b"), ok("b"))
    c, _ = chain(a, b)
    c.next_action(REQ)
    c.next_action(REQ)
    assert len(a.requests) == 1


def test_timeout_moves_on_but_is_tried_again_next_step():
    a = Fake("a", wrapped(ReadTimeout("timed out")), ok("a"))
    b = Fake("b", ok("b"))
    c, sleeps = chain(a, b)
    assert c.next_action(REQ).provider == "b"
    assert sleeps == []
    assert c.next_action(REQ).provider == "a"


def test_network_error_retries_the_same_provider_once():
    a = Fake("a", wrapped(ConnectError("getaddrinfo failed")), ok("a"))
    c, sleeps = chain(a, Fake("b"))
    r = c.next_action(REQ)
    assert r.provider == "a" and len(a.requests) == 2 and sleeps == [1.0]
    assert r.attempts[0].retried


def test_network_error_twice_moves_on():
    a = Fake("a", wrapped(ConnectError("x")), wrapped(ConnectError("x")))
    b = Fake("b", ok("b"))
    c, sleeps = chain(a, b)
    r = c.next_action(REQ)
    assert r.provider == "b" and len(a.requests) == 2 and sleeps == [1.0]
    assert [x.retried for x in r.attempts] == [True, False]


def test_all_failing_raises_with_the_trail():
    a = Fake("a", wrapped(RateLimitError("429")))
    b = Fake("b", wrapped(ReadTimeout("timed out")))
    c, _ = chain(a, b)
    with pytest.raises(ModelError, match=r"all providers failed \(a: rate_limit; b: timeout\)") as ei:
        c.next_action(REQ)
    assert [x.provider for x in ei.value.attempts] == ["a", "b"]


def test_unexpected_exception_also_falls_through():
    a, b = Fake("a", RuntimeError("bug")), Fake("b", ok("b"))
    c, _ = chain(a, b)
    r = c.next_action(REQ)
    assert r.provider == "b" and r.attempts[0].kind == "other"


def test_stop_is_checked_between_attempts():
    a, b = Fake("a", wrapped(ReadTimeout("t"))), Fake("b", ok("b"))
    c, _ = chain(a, b)
    calls = []

    def should_stop():
        calls.append(1)
        return len(calls) > 1    # stop after the first attempt

    req = StepRequest("s", "p", [], should_stop=should_stop)
    with pytest.raises(ModelError, match="stopped"):
        c.next_action(req)
    assert b.requests == []      # no extra cloud call after the kill


# ---------- simulated failures ----------

@pytest.mark.parametrize("text, want", [
    ("groq", ("groq", "rate_limit", 1)),
    ("gemini-lite@2", ("gemini-lite", "rate_limit", 2)),
    ("groq:overloaded", ("groq", "overloaded", 1)),
    ("ollama:network@3", ("ollama", "network", 3)),
])
def test_parse_simulate(text, want):
    assert parse_simulate(text) == want


@pytest.mark.parametrize("text", ["", "a:b:c", "a@0", "a:nope", "a@x"])
def test_parse_simulate_rejects(text):
    with pytest.raises(ModelError):
        parse_simulate(text)


@pytest.mark.parametrize("kind", ["rate_limit", "overloaded", "timeout", "network", "auth",
                                  "bad_model", "other"])
def test_simulated_failures_classify_as_their_kind(kind):
    m = FailingModel(Fake("a"), kind, 1)
    with pytest.raises(ModelError) as ei:
        m.next_action(REQ)
    assert classify(ei.value) == kind


def test_simulated_rate_limit_from_call_2_moves_the_run_mid_way():
    a = FailingModel(Fake("a", ok("a"), ok("a")), "rate_limit", 2)
    b = Fake("b", ok("b"), ok("b"))
    c = Chain([Provider("a", a), Provider("b", b)], sleep=lambda s: None)
    assert c.next_action(REQ).provider == "a"     # step 1: real answer
    r = c.next_action(REQ)                        # step 2: simulated 429
    assert r.provider == "b" and r.attempts[0].kind == "rate_limit"
    assert len(a.inner.requests) == 1             # no call reached the inner model
    assert c.next_action(REQ).provider == "b"
