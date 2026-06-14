"""Push Vigil's native Splunk objects so it lives INSIDE Splunk:
a dashboard + saved searches (anomaly detection + the AI audit trail).

After running, open Splunk -> Search & Reporting -> Dashboards -> "Vigil · Agentic Ops".
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from splunk_mcp import splunk_client as sc  # noqa: E402

NS = "/servicesNS/nobody/search"

DASHBOARD_NAME = "vigil_agentic_ops"
DASHBOARD_XML = """<dashboard version="1.1" theme="light">
  <label>Vigil · Agentic Ops</label>
  <description>Autonomous incident response — live signals and the AI audit trail</description>
  <row>
    <panel>
      <title>checkout-service · p95 latency &amp; 5xx (last 2h)</title>
      <chart>
        <search>
          <query>index=app_logs sourcetype=checkout:app | timechart span=2m p95(latency_ms) as p95_latency_ms count(eval(status=500)) as errors</query>
          <earliest>-2h</earliest><latest>now</latest>
        </search>
        <option name="charting.chart">line</option>
        <option name="charting.axisTitleY.text">p95 latency (ms)</option>
      </chart>
    </panel>
    <panel>
      <title>Incidents resolved by Vigil (7d)</title>
      <single>
        <search>
          <query>index=main sourcetype=vigil:incident | stats count</query>
          <earliest>-7d</earliest><latest>now</latest>
        </search>
        <option name="colorBy">value</option>
        <option name="rangeColors">["0x0fa76e","0x0fa76e"]</option>
      </single>
    </panel>
  </row>
  <row>
    <panel>
      <title>AI conclusions — audited root causes (vigil:incident)</title>
      <table>
        <search>
          <query>index=main sourcetype=vigil:incident | spath | sort -_time | table _time, title, root_cause, confidence</query>
          <earliest>-7d</earliest><latest>now</latest>
        </search>
        <option name="count">10</option>
      </table>
    </panel>
  </row>
  <row>
    <panel>
      <title>Remediation decisions — autonomy vs human (vigil:action)</title>
      <table>
        <search>
          <query>index=main sourcetype=vigil:action | spath | sort -_time | table _time, decision_mode, executed_by, confidence</query>
          <earliest>-7d</earliest><latest>now</latest>
        </search>
        <option name="count">10</option>
      </table>
    </panel>
  </row>
</dashboard>
"""

SAVED_SEARCHES = {
    "Vigil - Anomaly detection (checkout p95)": (
        "index=app_logs sourcetype=checkout:app "
        "| timechart span=2m p95(latency_ms) as p95 count(eval(status=500)) as errors count as total "
        "| streamstats window=12 current=f mean(p95) as base stdev(p95) as sd "
        "| eval z=if(sd>0,(p95-base)/sd,0) "
        "| where p95>1000 AND z>3"),
    "Vigil - Resolved incidents": (
        "index=main sourcetype=vigil:incident | spath | sort -_time "
        "| table _time, title, root_cause, suspect_deploy, confidence"),
    "Vigil - Remediation audit": (
        "index=main sourcetype=vigil:action | spath | sort -_time "
        "| table _time, decision_mode, executed_by, confidence"),
}


def _client() -> httpx.Client:
    headers = sc._auth_headers()
    auth = None if headers else (settings.SPLUNK_USERNAME, settings.SPLUNK_PASSWORD)
    return httpx.Client(base_url=settings.SPLUNK_BASE_URL, headers=headers,
                        auth=auth, verify=settings.SPLUNK_VERIFY_SSL, timeout=60)


def _upsert(c: httpx.Client, path: str, name: str, data: dict) -> str:
    """Create, or update if it already exists."""
    r = c.post(f"{NS}/{path}", data={"name": name, **data, "output_mode": "json"})
    if r.status_code in (200, 201):
        return "created"
    if r.status_code == 409:  # exists -> update (POST to the entity, no name)
        from urllib.parse import quote
        r2 = c.post(f"{NS}/{path}/{quote(name)}",
                    data={**data, "output_mode": "json"})
        r2.raise_for_status()
        return "updated"
    r.raise_for_status()
    return "ok"


def main() -> None:
    with _client() as c:
        for name, spl in SAVED_SEARCHES.items():
            status = _upsert(c, "saved/searches", name, {"search": spl})
            print(f"saved search [{status}]: {name}")
        status = _upsert(c, "data/ui/views", DASHBOARD_NAME,
                         {"eai:data": DASHBOARD_XML})
        print(f"dashboard  [{status}]: {DASHBOARD_NAME}")
    print("\nOpen Splunk -> App: Search & Reporting -> Dashboards -> 'Vigil · Agentic Ops'")
    print(f"Direct: {settings.SPLUNK_SCHEME}://{settings.SPLUNK_HOST}:8000"
          f"/en-US/app/search/{DASHBOARD_NAME}")


if __name__ == "__main__":
    main()
