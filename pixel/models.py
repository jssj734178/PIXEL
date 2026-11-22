"""Model adapter: one stateless call per step through the `llm` library.

The loop only sees ModelAdapter. M3 puts a fallback chain behind from_config()
without changing the loop. Tool calls come back as data; nothing runs inside llm
(every llm.Tool has implementation=None, and we never call chain()/reply()).

llm's Python API does not write to logs.db (only its CLI does), so screen text
sent here is not stored by llm.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from pixel.tools import ToolSpec


@dataclass
class StepRequest:
    system: str
    prompt: str
    tools: list[ToolSpec]


@dataclass
class ModelReply:
    calls: list[tuple[str, dict]] = field(default_factory=list)  # as returned, unvalidated
    text: str = ""
    model: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    thought_tokens: int | None = None
    seconds: float = 0.0


class ModelError(Exception):
    """Any failure talking to the model. M3: RateLimited(ModelError) moves the chain on."""


def use_system_certs() -> None:
    """Verify HTTPS against the Windows certificate store instead of certifi's
    bundle. HTTPS is intercepted on this machine (see QUIRKS.md), the same
    reason uv needs --system-certs."""
    import truststore
    truststore.inject_into_ssl()


class ModelAdapter(Protocol):
    name: str

    def next_action(self, req: StepRequest) -> ModelReply: ...


class LlmModel:
    """One llm model id plus the options from config.toml."""

    def __init__(self, model_id: str, options: dict | None = None, _llm=None):
        self.name = model_id
        self.options = dict(options or {})
        self._llm = _llm
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
                self._model = self._lib().get_model(self.name)
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


def from_config(cfg: dict) -> LlmModel:
    """cfg is config.models(). M2: the navigation model. M3: a Chain here."""
    model_id = cfg.get("navigation")
    if not model_id:
        raise ModelError("config.toml has no [models] navigation = \"...\" entry")
    return LlmModel(model_id, cfg.get("options", {}).get(model_id, {}))
