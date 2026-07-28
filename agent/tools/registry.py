"""Provider-independent tool registry.

Tools are the boundary where untrusted, model-generated input meets real side
effects, so two rules are enforced here rather than left to each tool:

* the model supplies a *name* and a JSON *string* of arguments - the registry
  parses and validates them before anything runs;
* execution is wrapped so a raising tool becomes a recorded error result, never
  a 500, and never an exception that escapes into the agent loop.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from agent.observability.logger import get_logger
from agent.routing.providers.base import ToolCall, ToolSpec

log = get_logger(__name__)


class ToolError(RuntimeError):
    """A tool could not be executed. Message is safe to return to the model."""


@dataclass(slots=True)
class ToolResult:
    name: str
    ok: bool
    content: str
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Who is asking, and on whose behalf.

    Passed to every tool call rather than left ambient, so a tool that touches
    tenant data has to state which tenant it is acting for. A tool that ignores
    it — the calculator — still receives it, and one that uses it wrongly fails
    visibly in review rather than silently reading across tenants.

    ``workspace_id`` is ``None`` only on the development path, where there is no
    database and therefore no tenancy.
    """

    workspace_id: str | None = None
    user_id: str | None = None
    user_email: str = ""
    request_id: str = ""
    #: The role the caller holds in ``workspace_id``.
    role: str = ""


#: Used when a tool is invoked outside a request — a script, or a test.
UNSCOPED = ToolContext()


class Tool(ABC):
    """A capability the model may invoke."""

    name: str = ""
    description: str = ""
    #: Pydantic model describing the tool's arguments.
    args_model: type[BaseModel] | None = None

    #: Optional guarantee that a value the user needs reaches the answer.
    #:
    #: Some tools mint an identifier the user has to be able to quote back to
    #: support — a ticket id being the obvious case. A model that says "I have
    #: created a support ticket" and drops the id has produced an answer the
    #: user cannot act on, and small models do that routinely no matter how the
    #: system prompt is worded.
    #:
    #: A tool sets `reference_pattern` to declare "values matching this regex
    #: are quotable references", and `reference_label` to name them in the
    #: fallback line. The agent then guarantees they appear, appending the
    #: missing one and recording a guardrail flag so the repair is visible in
    #: the response, the logs and the metrics rather than being silent.
    reference_pattern: str | None = None
    reference_label: str = "Reference"

    #: When set, the registry refuses the call unless the context carries a
    #: workspace. A tool that reads tenant data declares this, so a future
    #: caller that forgets to pass a scope gets a recorded error rather than
    #: unscoped access.
    requires_workspace: bool = False

    def spec(self) -> ToolSpec:
        schema: dict[str, Any]
        if self.args_model is not None:
            schema = self.args_model.model_json_schema()
            # Some models reject the JSON-Schema keywords Pydantic emits.
            schema.pop("title", None)
        else:
            schema = {"type": "object", "properties": {}}
        return ToolSpec(name=self.name, description=self.description, parameters=schema)

    @abstractmethod
    def run(self, context: ToolContext, **kwargs: Any) -> str | ToolResult:
        """Execute the tool.

        Return a string for the model, or a :class:`ToolResult` when the tool
        also needs to hand structured data (such as source citations) back to
        the agent.
        """

    # -- shared helpers ----------------------------------------------------
    def parse_arguments(self, raw: Any) -> dict[str, Any]:
        """Turn raw model arguments into validated keyword arguments.

        Models send arguments either as a JSON object or as a JSON-encoded
        string depending on the provider, and they get the schema wrong often
        enough that a hard failure is the wrong response - a clear error string
        lets the model retry with corrected arguments.
        """
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                data: Any = {}
            else:
                try:
                    data = json.loads(text)
                except ValueError as exc:
                    raise ToolError(
                        f"Arguments for '{self.name}' were not valid JSON. "
                        f"Expected an object matching the tool schema."
                    ) from exc
        else:
            data = raw

        if not isinstance(data, dict):
            raise ToolError(
                f"Arguments for '{self.name}' must be a JSON object, got {type(data).__name__}."
            )

        if self.args_model is None:
            return data

        try:
            return self.args_model(**data).model_dump()
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            raise ToolError(f"Invalid arguments for '{self.name}' ({problems}).") from exc


class ToolRegistry:
    """Holds the available tools and dispatches calls to them."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool
        return tool

    def register_all(self, tools: Sequence[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    # -- introspection -----------------------------------------------------
    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    # -- execution ---------------------------------------------------------
    def execute(self, call: ToolCall, context: ToolContext | None = None) -> ToolResult:
        """Run one tool call, converting any failure into a result object.

        ``context`` is optional so a script or a test can call a tool directly.
        A tool that declares ``requires_workspace`` refuses when it is missing,
        which is what keeps tenant-scoped tools from working unscoped.
        """
        scope = context or UNSCOPED
        tool = self.get(call.name)
        if tool is None:
            log.warning("tool.unknown", context={"tool": call.name})
            return ToolResult(
                name=call.name,
                ok=False,
                content=(
                    f"Unknown tool '{call.name}'. Available tools: "
                    f"{', '.join(self.names) or 'none'}."
                ),
            )

        if tool.requires_workspace and not scope.workspace_id:
            # Reported to the model as a tool error so it can recover, and to
            # the log as an authorization event, because a model asking for
            # unscoped access is worth knowing about.
            log.warning(
                "tool.workspace_required",
                context={"tool": call.name, "user_id": scope.user_id},
            )
            return ToolResult(
                name=call.name,
                ok=False,
                content="This tool needs an authenticated workspace and was called without one.",
            )

        started = time.perf_counter()
        try:
            arguments = tool.parse_arguments(call.arguments)
            raw = tool.run(scope, **arguments)
        except ToolError as exc:
            elapsed = (time.perf_counter() - started) * 1000
            log.warning("tool.rejected", context={"tool": call.name, "reason": str(exc)})
            return ToolResult(name=call.name, ok=False, content=str(exc), latency_ms=elapsed)
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            # Log the type, not the message: provider/tool exceptions can
            # contain arguments or internal detail that should not be echoed.
            log.error(
                "tool.failed",
                context={"tool": call.name, "error_type": type(exc).__name__},
                exc_info=True,
            )
            return ToolResult(
                name=call.name,
                ok=False,
                content=f"Tool '{call.name}' failed unexpectedly ({type(exc).__name__}).",
                latency_ms=elapsed,
            )

        elapsed = (time.perf_counter() - started) * 1000
        log.info(
            "tool.executed",
            context={
                **tool_result_log(tool.name, elapsed),
                "workspace_id": scope.workspace_id,
                "user_id": scope.user_id,
            },
        )
        if isinstance(raw, ToolResult):
            return ToolResult(
                name=call.name,
                ok=raw.ok,
                content=raw.content,
                latency_ms=elapsed,
                metadata=raw.metadata,
            )
        return ToolResult(name=call.name, ok=True, content=str(raw), latency_ms=elapsed)


def tool_result_log(name: str, elapsed_ms: float) -> dict[str, Any]:
    return {"tool": name, "latency_ms": round(elapsed_ms, 2)}
