"""Model adapter: one stateless call per step through the `llm` library.

The loop only sees ModelAdapter. from_config() returns a Chain of providers
(M3): when one fails, the same step goes to the next. Tool calls come back as
data; nothing runs inside llm (every llm.Tool has implementation=None, and we
never call chain()/reply()).

llm's Python API does not write to logs.db (only its CLI does), so screen text
sent here is not stored by llm.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from pixel.tools import ToolSpec


@dataclass
class StepRequest:
    system: str
    prompt: str
    tools: list[ToolSpec]
    should_stop: Callable[[], bool] | None = None   # the chain checks it between attempts


@dataclass
class Attempt:
    """One failed (or skipped) try at a provider during a step."""
    provider: str
    kind: str
    error: str
    seconds: float = 0.0
    skipped: bool = False   # not tried: it failed earlier in this run
    retried: bool = False   # the same provider was tried again right after this

    def as_dict(self) -> dict:
        d = {"provider": self.provider, "kind": self.kind, "error": self.error,
             "seconds": round(self.seconds, 2)}
        if self.skipped:
            d["skipped"] = True
        if self.retried:
            d["retried"] = True
        return d


@dataclass
class ModelReply:
    calls: list[tuple[str, dict]] = field(default_factory=list)  # as returned, unvalidated
    text: str = ""
    model: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    thought_tokens: int | None = None
    seconds: float = 0.0
    provider: str = ""                                    # chain entry that answered
    attempts: list[Attempt] = field(default_factory=list)  # failures before the answer


class ModelError(Exception):
    """Any failure talking to the model. `attempts` holds the chain's trail."""

    def __init__(self, msg: str = "", attempts: list[Attempt] | None = None):
        super().__init__(msg)
        self.attempts = list(attempts or [])


def use_system_certs() -> None:
    """Verify HTTPS against the Windows certificate store instead of certifi's
    bundle. HTTPS is intercepted on this machine (see QUIRKS.md), the same
    reason uv needs --system-certs."""
    import truststore
    truststore.inject_into_ssl()


class ModelAdapter(Protocol):
    name: str

    def next_action(self, req: StepRequest) -> ModelReply: ...


# ---------- one model ----------

def _compat_model(name: str, spec: dict):
    """An OpenAI-compatible endpoint (Groq, Ollama) through llm's built-in Chat
    class. Talks only to spec['api_base']; the key comes from llm's key store
    (alias spec['key']) or its environment variable. No client-side retries:
    the chain decides what to retry."""
    from llm.default_plugins.openai_models import Chat

    timeout = float(spec.get("timeout", 60))

    class CompatChat(Chat):
        def get_client(self, key, *, async_=False):
            return super().get_client(key, async_=async_).with_options(
                timeout=timeout, max_retries=0)

    # reasoning = true adds the reasoning_effort option (gpt-oss and similar models).
    m = CompatChat(model_id=f"pixel-{name}", model_name=spec["model"],
                   api_base=spec["api_base"], supports_tools=True,
                   reasoning=bool(spec.get("reasoning", False)))
    key = spec.get("key")
    m.needs_key = key or None
    m.key_env_var = (spec.get("key_env") or f"{key.upper()}_API_KEY") if key else None
    return m


class LlmModel:
    """One llm model plus the options from config.toml."""

    def __init__(self, model_id: str, options: dict | None = None, _llm=None, build=None,
                 local: bool = False):
        self.name = model_id
        self.options = dict(options or {})
        self.local = local
        self._llm = _llm
        self._build = build      # returns an llm model; default: llm.get_model(model_id)
        self._model = None

    def _lib(self):
        if self._llm is None:
            use_system_certs()
            import llm
            self._llm = llm
        return self._llm

    def _get_model(self):
        if self._model is None:
            try:
                lib = self._lib()
                self._model = self._build() if self._build else lib.get_model(self.name)
            except Exception as exc:  # noqa: BLE001
                raise ModelError(f"unknown model '{self.name}': {exc}") from exc
        return self._model

    def describe_options(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in self.options.items() if k != "timeout")

    def next_action(self, req: StepRequest) -> ModelReply:
        lib = self._lib()
        model = self._get_model()
        tools = [lib.Tool(name=t.name, description=t.description,
                          input_schema=t.schema, implementation=None) for t in req.tools]
        t0 = time.perf_counter()
        try:
            response = model.prompt(req.prompt, system=req.system, tools=tools,
                                    options=dict(self.options))
            calls = [(c.name, c.arguments) for c in response.tool_calls()]
            text = response.text() or ""
            usage = response.usage()
        except Exception as exc:  # noqa: BLE001
            raise ModelError(f"{self.name}: {type(exc).__name__}: {exc}") from exc
        details = getattr(usage, "details", None) or {}
        thoughts = details.get("thoughtsTokenCount") if isinstance(details, dict) else None
        return ModelReply(
            calls=calls,
            text=text.strip(),
            model=self.name,
            input_tokens=getattr(usage, "input", None),
            output_tokens=getattr(usage, "output", None),
            thought_tokens=thoughts,
            seconds=time.perf_counter() - t0,
        )


# ---------- error classes ----------

RATE_LIMIT, OVERLOADED, TIMEOUT, NETWORK = "rate_limit", "overloaded", "timeout", "network"
AUTH, BAD_MODEL, OTHER = "auth", "bad_model", "other"
KINDS = (RATE_LIMIT, OVERLOADED, TIMEOUT, NETWORK, AUTH, BAD_MODEL, OTHER)

STICKY = {RATE_LIMIT, OVERLOADED, AUTH, BAD_MODEL}   # skip that provider for the rest of the run
RETRY_ONCE = {NETWORK}                               # try the same provider once more first

_MESSAGES = [  # checked in this order
    (RATE_LIMIT, r"quota|rate.?limit|resource.?exhausted|too many requests|\b429\b"),
    (AUTH, r"api.?key|no key found|unauthori[sz]ed|permission.?denied|\b40[13]\b"),
    (BAD_MODEL, r"unknown model|model\b.{0,60}\b(not found|does not exist|decommissioned)|\b404\b"),
    (OVERLOADED, r"high demand|overloaded|unavailable|try again later|internal (server )?error"
                 r"|\b50[0234]\b"),
    (TIMEOUT, r"timed? ?out|timeout"),
    (NETWORK, r"getaddrinfo|name resolution|nodename|connection (error|refused|reset|aborted)"
              r"|failed to establish|network is unreachable|connect ?error"),
]
_MESSAGES = [(k, re.compile(p, re.IGNORECASE)) for k, p in _MESSAGES]

_NETWORK_CLASSES = {"ConnectError", "APIConnectionError", "RemoteProtocolError", "NetworkError",
                    "ConnectionError", "gaierror"}


def _status(e) -> int | None:
    s = getattr(e, "status_code", None)
    if s is None:
        s = getattr(getattr(e, "response", None), "status_code", None)
    return s if isinstance(s, int) else None


def _by_type(e) -> str | None:
    s = _status(e)
    if s == 429:
        return RATE_LIMIT
    if s in (401, 403):
        return AUTH
    if s == 404:
        return BAD_MODEL
    if s is not None and s >= 500:
        return OVERLOADED
    names = {c.__name__ for c in type(e).__mro__}
    if "RateLimitError" in names:
        return RATE_LIMIT
    if names & {"NeedsKeyException", "AuthenticationError", "PermissionDeniedError"}:
        return AUTH
    if "NotFoundError" in names:
        return BAD_MODEL
    if any("Timeout" in n for n in names):   # before network: APITimeoutError is a connection error
        return TIMEOUT
    if names & _NETWORK_CLASSES:
        return NETWORK
    if "InternalServerError" in names:
        return OVERLOADED
    return None


def classify(exc: BaseException) -> str:
    """Sort a model failure into one of KINDS by duck typing: status codes and
    class names along the exception chain first, then the message text
    (llm-gemini raises a bare ModelError(message) with no status code)."""
    chain, e = [], exc
    while e is not None and len(chain) < 6:
        chain.append(e)
        e = e.__cause__ or e.__context__
    for e in chain:
        kind = _by_type(e)
        if kind:
            return kind
    text = " ".join(str(e) for e in chain)
    for kind, rx in _MESSAGES:
        if rx.search(text):
            return kind
    return OTHER


def _short(s: str, n: int = 160) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------- the chain ----------

@dataclass
class Provider:
    name: str              # the key in [models.providers]
    model: ModelAdapter
    local: bool = False

    def describe(self) -> str:
        opts = getattr(self.model, "describe_options", lambda: "")()
        bits = [getattr(self.model, "name", "?")] + ([opts] if opts else []) + \
               (["local"] if self.local else [])
        return f"{self.name} ({', '.join(bits)})"


class Chain:
    """Providers tried in order on every step. Any ModelError moves the same
    request to the next provider. Network errors get one retry on the same
    provider first. Rate limits, overload, auth and unknown-model errors take
    that provider out for the rest of the run (a Chain lives for one run)."""

    def __init__(self, providers: list[Provider], *, sleep=time.sleep, retry_delay: float = 1.0,
                 clock=time.perf_counter):
        if not providers:
            raise ModelError("the model chain is empty")
        self.providers = providers
        self.name = " > ".join(p.name for p in providers)
        self.down: dict[str, str] = {}      # provider -> kind that took it out
        self.sleep = sleep
        self.retry_delay = retry_delay
        self.clock = clock

    def describe(self) -> str:
        return " > ".join(p.describe() for p in self.providers)

    def recipients(self) -> str:
        """Who may receive the window tree, for the first-send prompt."""
        names = [p.name + (" (local)" if p.local else "") for p in self.providers]
        if len(names) == 1:
            return names[0]
        return f"{names[0]} (falling back to {', then '.join(names[1:])})"

    def next_action(self, req: StepRequest) -> ModelReply:
        attempts: list[Attempt] = []
        stop = req.should_stop or (lambda: False)
        for p in self.providers:
            if p.name in self.down:
                attempts.append(Attempt(p.name, self.down[p.name],
                                        "failed earlier this run", skipped=True))
                continue
            tries = 0
            while True:
                if stop():
                    raise ModelError("stopped", attempts)
                t0 = self.clock()
                try:
                    reply = p.model.next_action(req)
                except Exception as exc:  # noqa: BLE001 - every failure moves the chain on
                    kind = classify(exc)
                    a = Attempt(p.name, kind, _short(str(exc)), self.clock() - t0)
                    attempts.append(a)
                    if kind in RETRY_ONCE and tries == 0:
                        tries += 1
                        a.retried = True
                        self.sleep(self.retry_delay)
                        continue
                    if kind in STICKY:
                        self.down[p.name] = kind
                    break
                reply.provider = p.name
                reply.attempts = attempts
                return reply
        tried = [f"{a.provider}: {a.kind}" for a in attempts if not a.retried]
        raise ModelError("all providers failed (" + "; ".join(tried) + ")", attempts)


# ---------- simulated failures (pixel run --simulate-fail) ----------

_SIM_STATUS = {RATE_LIMIT: 429, OVERLOADED: 503, AUTH: 401, BAD_MODEL: 404}
_SIM_TEXT = {RATE_LIMIT: "429 rate limit", OVERLOADED: "503 high demand", AUTH: "401 bad API key",
             BAD_MODEL: "404 model not found", TIMEOUT: "read timed out",
             NETWORK: "connection reset", OTHER: "unexpected failure"}


class SimulatedFailure(Exception):
    def __init__(self, kind: str):
        self.status_code = _SIM_STATUS.get(kind)
        super().__init__(f"simulated {_SIM_TEXT[kind]}")


class FailingModel:
    """Wraps a model so that from its Nth call on it fails with `kind` instead
    of calling the network. Only built from the CLI flag, never from config."""

    def __init__(self, inner, kind: str = RATE_LIMIT, start: int = 1):
        self.inner, self.kind, self.start = inner, kind, start
        self.name = inner.name
        self.calls = 0

    def describe_options(self) -> str:
        opts = getattr(self.inner, "describe_options", lambda: "")()
        sim = f"simulated {self.kind} from call {self.start}"
        return f"{opts}, {sim}" if opts else sim

    def next_action(self, req: StepRequest) -> ModelReply:
        self.calls += 1
        if self.calls >= self.start:
            raise ModelError(f"{self.name}: simulated {_SIM_TEXT[self.kind]}") \
                from SimulatedFailure(self.kind)
        return self.inner.next_action(req)


def parse_simulate(text: str) -> tuple[str, str, int]:
    """'groq', 'groq:timeout', 'gemini-lite@2', 'groq:overloaded@3' -> (name, kind, start)."""
    m = re.fullmatch(r"\s*([^:@\s]+)(?::([a-z_]+))?(?:@(\d+))?\s*", text or "")
    if not m:
        raise ModelError(f"--simulate-fail '{text}': use PROVIDER[:KIND][@N]")
    name, kind, start = m.group(1), m.group(2) or RATE_LIMIT, int(m.group(3) or 1)
    if kind not in KINDS:
        raise ModelError(f"--simulate-fail '{text}': kind must be one of {', '.join(KINDS)}")
    if start < 1:
        raise ModelError(f"--simulate-fail '{text}': N must be at least 1")
    return name, kind, start


# ---------- config ----------

def _provider(name: str, spec: dict, llm_lib=None) -> Provider:
    if not isinstance(spec, dict) or not spec.get("model"):
        raise ModelError(f"config.toml: [models.providers.{name}] needs model = \"...\"")
    options = dict(spec.get("options") or {})
    local = bool(spec.get("local", False))
    if spec.get("api_base"):
        model = LlmModel(spec["model"], options, _llm=llm_lib, local=local,
                         build=lambda: _compat_model(name, spec))
    else:
        if "timeout" in spec:
            options.setdefault("timeout", spec["timeout"])   # llm-gemini takes it as an option
        model = LlmModel(spec["model"], options, _llm=llm_lib, local=local)
    return Provider(name, model, local)


def from_config(cfg: dict, simulate: list[str] | tuple = (), llm_lib=None) -> Chain:
    """cfg is config.models(): {"chain": [names], "providers": {name: {...}}}.
    `simulate` holds --simulate-fail values."""
    names = cfg.get("chain") or []
    if not names:
        raise ModelError("config.toml has no [models] chain = [\"...\"] entry")
    providers_cfg = cfg.get("providers") or {}
    missing = [n for n in names if n not in providers_cfg]
    if missing:
        raise ModelError(f"config.toml: chain names {', '.join(missing)} "
                         "with no [models.providers.<name>] table")
    if len(set(names)) != len(names):
        raise ModelError("config.toml: a provider appears twice in [models] chain")
    providers = [_provider(n, providers_cfg[n], llm_lib) for n in names]

    by_name = {p.name: p for p in providers}
    for s in simulate:
        name, kind, start = parse_simulate(s)
        if name not in by_name:
            raise ModelError(f"--simulate-fail: '{name}' is not in the chain "
                             f"({', '.join(names)})")
        by_name[name].model = FailingModel(by_name[name].model, kind, start)
    return Chain(providers)
