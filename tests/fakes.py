"""In-memory fakes so the async orchestration is tested without a live server.

These stand in for a real MCP ``ClientSession``. Each fake exposes just the two
async methods the engine calls, ``list_tools`` and ``call_tool``, with
configurable behavior so we can simulate clean errors, crashes, and servers that
wrongly accept invalid input.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData


def make_mcp_error(message: str = "Invalid params", code: int = -32602) -> McpError:
    """Construct a structured MCP error, as a real server would return."""
    return McpError(ErrorData(code=code, message=message))


class FakeSession:
    """A minimal async stand-in for ``mcp.ClientSession``.

    Args:
        tools: List of tool dicts to return from ``list_tools``.
        call_behavior: Optional ``(tool_name, arguments) -> result`` callable.
            It may return an object with an ``isError`` attribute, or raise
            (``McpError`` for a clean rejection, any other exception for a
            crash). Defaults to returning a clean ``isError=True`` result.
        list_tools_raises: If set, ``list_tools`` raises this each call (used to
            exercise the latency-probe failure path).
    """

    def __init__(
        self,
        tools: list[dict[str, Any]] | None = None,
        call_behavior: Callable[[str, dict[str, Any]], Any] | None = None,
        list_tools_raises: BaseException | None = None,
    ) -> None:
        self._tools = tools or []
        self._call_behavior = call_behavior
        self._list_tools_raises = list_tools_raises
        self.call_log: list[tuple[str, dict[str, Any]]] = []
        self.list_tools_calls = 0

    async def list_tools(self) -> Any:
        self.list_tools_calls += 1
        if self._list_tools_raises is not None:
            raise self._list_tools_raises
        # SDK returns objects with .name/.description/.inputSchema; a
        # SimpleNamespace matches what tool_to_dict reads via getattr.
        tool_objs = [SimpleNamespace(**t) for t in self._tools]
        return SimpleNamespace(tools=tool_objs)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        arguments = arguments or {}
        self.call_log.append((name, arguments))
        if self._call_behavior is not None:
            return self._call_behavior(name, arguments)
        # Default: behave like a reliable server that cleanly rejects.
        return SimpleNamespace(isError=True, content=[], structuredContent=None)


def clean_via_result(name: str, arguments: dict[str, Any]) -> Any:
    """Server behavior: return a tool-level error result (clean rejection)."""
    return SimpleNamespace(isError=True, content=[], structuredContent=None)


def clean_via_mcp_error(name: str, arguments: dict[str, Any]) -> Any:
    """Server behavior: raise a structured MCP error (clean rejection)."""
    raise make_mcp_error(f"invalid arguments for {name}")


def crash(name: str, arguments: dict[str, Any]) -> Any:
    """Server behavior: raise a non-MCP exception (a crash)."""
    raise ValueError("unhandled server exception")


def accepts_invalid(name: str, arguments: dict[str, Any]) -> Any:
    """Server behavior: return success despite invalid input (silent-wrong)."""
    return SimpleNamespace(isError=False, content=[], structuredContent=None)
