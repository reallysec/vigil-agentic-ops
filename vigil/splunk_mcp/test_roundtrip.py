"""Verify the Splunk MCP server end-to-end over stdio (list tools + call one)."""
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "splunk_mcp" / "server.py")],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])

            res = await session.call_tool(
                "splunk_search",
                {
                    "query": "index=app_logs sourcetype=checkout:app status=500 | stats count",
                    "earliest": "-2h",
                    "latest": "now",
                },
            )
            print("CALL RESULT:", res.content[0].text[:300])


if __name__ == "__main__":
    asyncio.run(main())
