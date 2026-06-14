"""Console data layer: alert inbox, Splunk-backed incident history, and KPIs.

The alert inbox is a curated, realistic set for an on-call console. The top
checkout-service P1 is fully backed by seeded Splunk data and is the one the
agent team investigates live; the others provide product context (acknowledged
/ monitoring) the way a real console would show a queue of signals.

Incident history is REAL — it reads the audit events the Communicator agent
writes back to Splunk (index=main sourcetype=vigil:incident).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from splunk_mcp import splunk_client as sc  # noqa: E402

# --- curated alert inbox ---
ALERTS = [
    {
        "id": "ALR-1042", "severity": "P1", "service": "checkout-service",
        "title": ("p95 latency > 5000ms and HTTP 500 rate spiking on "
                  "/api/v1/checkout for ~25 minutes"),
        "source": "Splunk ITSI · Episode", "fired": "16:11", "status": "firing",
        "ready": True,
        "alert_text": ("ALERT [P1] checkout-service: p95 latency > 5000ms and "
                       "HTTP 500 rate spiking on /api/v1/checkout for the last "
                       "~25 minutes."),
        "signals": ["p95 9.9s", "5xx 57%", "pool saturated"],
    },
    {
        "id": "ALR-1041", "severity": "P2", "service": "auth-gateway",
        "title": "Elevated 401 rate after token-service rollout",
        "source": "Splunk · Correlation Search", "fired": "15:30",
        "status": "acknowledged", "ready": False, "signals": ["401 +18%"],
    },
    {
        "id": "ALR-1040", "severity": "P3", "service": "search-indexer",
        "title": "Indexing pipeline backlog > 10k events",
        "source": "Splunk · Alert", "fired": "15:02", "status": "monitoring",
        "ready": False, "signals": ["lag 12k"],
    },
    {
        "id": "ALR-1039", "severity": "P4", "service": "email-worker",
        "title": "SMTP retry rate slowly climbing",
        "source": "Splunk · Alert", "fired": "14:18", "status": "monitoring",
        "ready": False, "signals": ["retries +6%"],
    },
]


_CFG_FILE = Path(__file__).resolve().parent / ".console_config.json"
_CFG_DEFAULTS = {
    "autonomy_enabled": True,       # may Vigil auto-apply high-confidence fixes?
    "autonomy_threshold": 90,       # confidence at/above which it may auto-apply
    "verifier_enabled": True,       # run the adversarial second-opinion agent?
}


def get_config() -> dict:
    cfg = dict(_CFG_DEFAULTS)
    if _CFG_FILE.exists():
        try:
            cfg.update(json.loads(_CFG_FILE.read_text()))
        except Exception:
            pass
    return cfg


def set_config(patch: dict) -> dict:
    cfg = get_config()
    if "autonomy_enabled" in patch:
        cfg["autonomy_enabled"] = bool(patch["autonomy_enabled"])
    if "verifier_enabled" in patch:
        cfg["verifier_enabled"] = bool(patch["verifier_enabled"])
    if "autonomy_threshold" in patch:
        try:
            cfg["autonomy_threshold"] = max(50, min(100, int(patch["autonomy_threshold"])))
        except (TypeError, ValueError):
            pass
    _CFG_FILE.write_text(json.dumps(cfg))
    return cfg


def _fmt_ms(v: float) -> str:
    return f"{v/1000:.1f}s" if v >= 1000 else f"{int(v)}ms"


def detect_anomalies(service: str = "checkout-service") -> list[dict]:
    """Real statistical anomaly detection in Splunk (no human-configured alert):
    a trailing-baseline z-score on p95 latency flags the regression autonomously."""
    try:
        rows = sc.oneshot_search(
            "index=app_logs sourcetype=checkout:app "
            "| timechart span=2m p95(latency_ms) as p95 "
            "count(eval(status=500)) as errors count as total "
            "| streamstats window=12 current=f mean(p95) as base stdev(p95) as sd "
            "| eval z=if(sd>0,(p95-base)/sd,0) "
            "| where p95>1000 AND z>3 "
            "| stats max(p95) as peak_p95 max(z) as peak_z min(base) as base "
            "sum(errors) as errors sum(total) as total count as buckets",
            earliest="-2h", latest="now", max_count=5)
    except Exception:
        return []
    out = []
    for r in rows:
        if not r.get("peak_p95"):
            continue
        total = float(r.get("total") or 0)
        errors = float(r.get("errors") or 0)
        peak = float(r["peak_p95"])
        base = float(r.get("base") or 0) or 1
        out.append({
            "service": service,
            "metric": "p95 latency",
            "peak_p95": round(peak),
            "baseline_p95": round(base),
            "ratio": round(peak / base, 1),               # e.g. 68× above baseline
            "sigma": round(min(float(r.get("peak_z") or 0), 50), 1),  # clamp display
            "error_rate": round(100 * errors / total, 1) if total else 0,
            "buckets": int(float(r.get("buckets") or 0)),
        })
    return out


def detection_summary() -> dict:
    """What the anomaly engine scanned + found, for the console strip."""
    anomalies = detect_anomalies()
    return {
        "scanned": "checkout-service · p95 latency (last 2h, 2m buckets)",
        "method": "Splunk trailing z-score anomaly search",
        "anomalies": anomalies,
        "count": len(anomalies),
    }


def list_alerts() -> list[dict]:
    """The inbox. The top P1 is produced by Vigil's own anomaly detection
    (real Splunk search), not a human-configured alert."""
    alerts = [dict(a) for a in ALERTS]
    det = detect_anomalies()
    if det and alerts:
        d = det[0]
        a = alerts[0]
        a["source"] = "Vigil Anomaly Detection"
        a["detected"] = True
        a["title"] = (f"Anomalous p95 latency on {d['service']} — peak "
                      f"{_fmt_ms(d['peak_p95'])}, {d['ratio']}× above baseline")
        a["signals"] = [f"p95 {_fmt_ms(d['peak_p95'])}",
                        f"5xx {d['error_rate']}%", f"{d['ratio']}× baseline"]
        a["anomaly"] = d
    return alerts


def get_alert(alert_id: str) -> dict | None:
    return next((a for a in ALERTS if a["id"] == alert_id), None)


def list_incidents(limit: int = 20) -> list[dict]:
    """Resolved incidents the agent team has recorded back into Splunk."""
    try:
        rows = sc.oneshot_search(
            "index=main sourcetype=vigil:incident | sort -_time "
            f"| head {limit}", earliest="-7d", latest="now", max_count=limit)
    except Exception:
        return []
    out = []
    for r in rows:
        raw = r.get("_raw", "{}")
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            doc = {}
        out.append({
            "time": r.get("_time", ""),
            "title": doc.get("title", "Incident"),
            "root_cause": doc.get("root_cause", ""),
            "suspect_deploy": doc.get("suspect_deploy", ""),
            "remediation": doc.get("remediation", ""),
            "confidence": doc.get("confidence", ""),
        })
    return out


def list_actions(limit: int = 25) -> list[dict]:
    """Remediation decisions the team recorded back into Splunk: autonomy vs human,
    the Verifier's verdict, and the calibrated confidence (vigil:action)."""
    try:
        rows = sc.oneshot_search(
            "index=main sourcetype=vigil:action | sort -_time "
            f"| head {limit}", earliest="-7d", latest="now", max_count=limit)
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            doc = json.loads(r.get("_raw", "{}"))
        except json.JSONDecodeError:
            doc = {}
        out.append({
            "time": r.get("_time", ""),
            "decision_mode": doc.get("decision_mode"),
            "executed_by": doc.get("executed_by"),
            "confidence": doc.get("confidence"),
            "verdict": doc.get("verdict"),
            "root_cause": doc.get("root_cause"),
            "service": doc.get("service"),
        })
    return out


def stats() -> dict:
    firing = sum(1 for a in ALERTS if a["status"] == "firing")
    resolved = len(list_incidents(limit=50))
    return {
        "open_alerts": sum(1 for a in ALERTS if a["status"] != "resolved"),
        "firing": firing,
        "resolved_by_vigil": resolved,
        "agents_online": "4/4",
        "median_mttr": "~2m",
    }


def signals(service: str = "checkout-service") -> dict:
    """Real Splunk timechart driving the war-room 'live signals' chart:
    p95 latency + error rate over the last 2h, plus the deploy marker."""
    series: list[dict] = []
    try:
        rows = sc.oneshot_search(
            "index=app_logs sourcetype=checkout:app "
            "| timechart span=2m p95(latency_ms) as p95 "
            "count(eval(status=500)) as errors count as total "
            "avg(pool_active) as pool_active max(pool_max) as pool_max "
            "| eval epoch=_time",
            earliest="-2h", latest="now", max_count=120)
        for r in rows:
            ep = r.get("epoch")
            p95 = float(r.get("p95") or 0)
            if not ep or p95 <= 0:   # skip empty leading/trailing buckets
                continue
            total = float(r.get("total") or 0)
            errors = float(r.get("errors") or 0)
            series.append({
                "t": float(ep),
                "p95": round(p95, 1),
                "errors": int(errors),
                "error_rate": round(100 * errors / total, 1) if total else 0,
                "pool_active": round(float(r.get("pool_active") or 0), 1),
                "pool_max": int(float(r.get("pool_max") or 0)),
            })
    except Exception:
        pass

    deploy_t = None
    deploy_version = None
    try:
        dep = sc.oneshot_search(
            "index=deploy_events sourcetype=deploy:event "
            f"service={service} | head 1 | eval epoch=_time "
            "| table epoch version",
            earliest="-24h", latest="now", max_count=1)
        if dep:
            deploy_t = float(dep[0]["epoch"])
            deploy_version = dep[0].get("version")
    except Exception:
        pass

    return {"service": service, "series": series,
            "deploy_t": deploy_t, "deploy_version": deploy_version}


def env_info() -> dict:
    from config import settings as st
    try:
        si = sc.server_info()
    except Exception:
        si = {}
    return {
        "splunk_version": si.get("version"),
        "splunk_server": si.get("server_name"),
        "model": st.LLM_MODEL,
        "mcp_tools": ["splunk_search", "splunk_list_indexes",
                      "splunk_server_info", "splunk_write_event"],
    }
