"""Splunk MCP Server (Vigil).

Exposes Splunk data to AI agents over the Model Context Protocol using
token-based authentication, mirroring the purpose of the official Splunk MCP
Server. Runs over stdio (for local agents) or streamable-http.

Tools
-----
- splunk_search        : run an SPL search, get rows back
- splunk_list_indexes  : enumerate data indexes
- splunk_server_info   : Splunk version / identity
- splunk_write_event   : record agent reasoning/actions back into Splunk (audit)

Run:  python -m vigil.mcp.server            # stdio
      python -m vigil.mcp.server --http     # streamable-http on MCP_PORT
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from config import settings  # noqa: E402
from splunk_mcp import splunk_client as sc  # noqa: E402

mcp = FastMCP("splunk-vigil", host=settings.MCP_HOST, port=settings.MCP_PORT)


@mcp.tool()
def splunk_search(
    query: str,
    earliest: str = "-24h",
    latest: str = "now",
    max_count: int = 200,
) -> dict:
    """Run an SPL search against Splunk and return the result rows.

    Args:
        query: SPL search string. You may omit the leading `search` keyword.
               Transforming searches starting with `|` are run as-is.
               Examples:
                 index=app_logs sourcetype=checkout:app status=500
                 index=app_logs | timechart span=5m avg(latency_ms) count(eval(status=500)) as errors
                 index=deploy_events | sort -_time
        earliest: time modifier, e.g. -60m, -24h, @d (default -24h).
        latest: time modifier, default now.
        max_count: max rows to return (default 200).

    Returns a dict with `row_count` and `rows` (list of field dicts).
    """
    rows = sc.oneshot_search(query, earliest, latest, max_count)
    return {"query": query, "earliest": earliest, "latest": latest,
            "row_count": len(rows), "rows": rows}


@mcp.tool()
def splunk_list_indexes() -> dict:
    """List the non-internal Splunk indexes with their event counts and time ranges."""
    return {"indexes": sc.list_indexes()}


@mcp.tool()
def splunk_server_info() -> dict:
    """Return Splunk server version and identity (connectivity check)."""
    return sc.server_info()


@mcp.tool()
def splunk_write_event(event_text: str, index: str = "main",
                       sourcetype: str = "vigil:audit") -> dict:
    """Write an event back into Splunk (e.g. an agent's finding or remediation action).

    Creates a first-class, auditable record of what the AI did and why.
    """
    return sc.write_event(event_text, index=index, sourcetype=sourcetype)


if __name__ == "__main__":
    transport = "streamable-http" if "--http" in sys.argv else "stdio"
    if transport == "streamable-http":
        print(f"Splunk MCP server on http://{settings.MCP_HOST}:{settings.MCP_PORT}/mcp",
              file=sys.stderr)
    mcp.run(transport=transport)
