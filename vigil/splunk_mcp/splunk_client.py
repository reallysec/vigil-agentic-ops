"""Thin Splunk REST client used by the MCP server.

Prefers Bearer (JWT) token auth as recommended by the hackathon guidance;
falls back to basic auth. All search goes through the blocking export/oneshot
endpoints so the MCP tools return final rows in a single call.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import httpx

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402

_TOKEN_FILE = Path(__file__).resolve().parents[2] / ".splunk_token"


def _auth_headers() -> dict[str, str]:
    token = settings.SPLUNK_TOKEN
    if not token and _TOKEN_FILE.exists():
        token = _TOKEN_FILE.read_text().strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _client() -> httpx.Client:
    auth = None
    headers = _auth_headers()
    if not headers:  # fall back to basic auth
        auth = (settings.SPLUNK_USERNAME, settings.SPLUNK_PASSWORD)
    return httpx.Client(
        base_url=settings.SPLUNK_BASE_URL,
        headers=headers,
        auth=auth,
        verify=settings.SPLUNK_VERIFY_SSL,
        timeout=120.0,
    )


def oneshot_search(
    query: str,
    earliest: str = "-24h",
    latest: str = "now",
    max_count: int = 200,
) -> list[dict[str, Any]]:
    """Run a blocking search and return result rows as dicts.

    `query` may omit the leading `search` keyword for transforming searches
    (e.g. starting with `|`); otherwise it is prefixed automatically.
    """
    q = query.strip()
    if not q.startswith("|") and not q.lower().startswith("search "):
        q = f"search {q}"

    with _client() as c:
        resp = c.post(
            "/services/search/jobs/export",
            data={
                "search": q,
                "earliest_time": earliest,
                "latest_time": latest,
                "output_mode": "csv",
                "count": str(max_count),
            },
        )
        resp.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        return rows[:max_count]


def list_indexes() -> list[dict[str, Any]]:
    with _client() as c:
        resp = c.get(
            "/services/data/indexes",
            params={"output_mode": "json", "count": "0"},
        )
        resp.raise_for_status()
        out = []
        for e in resp.json().get("entry", []):
            name = e["name"]
            if name.startswith("_"):
                continue
            ct = e.get("content", {})
            out.append({
                "name": name,
                "total_event_count": ct.get("totalEventCount"),
                "current_db_size_mb": ct.get("currentDBSizeMB"),
                "earliest_time": ct.get("minTime"),
                "latest_time": ct.get("maxTime"),
            })
        return out


def write_event(event_text: str, index: str, sourcetype: str,
                source: str = "vigil-agent") -> dict[str, Any]:
    """Write a single event back into Splunk via receivers/simple (port 8089).

    Used by the Communicator/Remediator agents to record AI reasoning and
    actions as a first-class, auditable Splunk event.
    """
    with _client() as c:
        resp = c.post(
            "/services/receivers/simple",
            params={"index": index, "sourcetype": sourcetype,
                    "source": source, "output_mode": "json"},
            content=event_text.encode("utf-8"),
        )
        resp.raise_for_status()
        return {"status": "ok", "bytes": len(event_text)}


def server_info() -> dict[str, Any]:
    with _client() as c:
        resp = c.get("/services/server/info", params={"output_mode": "json"})
        resp.raise_for_status()
        ct = resp.json()["entry"][0]["content"]
        return {"version": ct.get("version"), "server_name": ct.get("serverName")}
