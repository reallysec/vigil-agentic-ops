"""Synchronous bridge to the Splunk MCP server.

Keeps a persistent MCP ClientSession alive on a background asyncio loop so the
(synchronous) agent loop can list tools and call them. Tool schemas advertised
by the MCP server are converted to OpenAI function-calling format, so the
agents discover Splunk capabilities dynamically from the server — exactly the
point of MCP.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


class MCPToolbox:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._session: ClientSession | None = None
        self._ctx: Any = None
        self._tools_cache: list[dict] = []
        self._stack: list[Any] = []

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    # --- lifecycle ---
    def start(self) -> "MCPToolbox":
        self._submit(self._connect())
        return self

    async def _connect(self) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "splunk_mcp" / "server.py")],
        )
        self._stdio_cm = stdio_client(params)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()
        tools = await self._session.list_tools()
        self._tools_cache = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": (t.description or "").strip(),
                    "parameters": t.inputSchema or {"type": "object", "properties": {}},
                },
            }
            for t in tools.tools
        ]

    def close(self) -> None:
        try:
            self._submit(self._disconnect())
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    async def _disconnect(self) -> None:
        if self._session is not None:
            await self._session_cm.__aexit__(None, None, None)
        if getattr(self, "_stdio_cm", None) is not None:
            await self._stdio_cm.__aexit__(None, None, None)

    # --- tool access ---
    def openai_tools(self, only: list[str] | None = None) -> list[dict]:
        if only is None:
            return self._tools_cache
        return [t for t in self._tools_cache if t["function"]["name"] in only]

    def call(self, name: str, **kwargs) -> Any:
        return self._submit(self._call(name, kwargs))

    async def _call(self, name: str, args: dict) -> Any:
        res = await self._session.call_tool(name, args)
        text = res.content[0].text if res.content else "{}"
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    def impl_map(self, names: list[str]) -> dict:
        """Return {tool_name: callable} for the named tools, for AgentRunner."""
        return {n: (lambda _n=n, **kw: self.call(_n, **kw)) for n in names}
