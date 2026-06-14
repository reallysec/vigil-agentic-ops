"""Seed a realistic, self-contained incident into Splunk for the demo.

SCENARIO  (the ground-truth root cause the agents must rediscover)
------------------------------------------------------------------
An e-commerce "checkout-service" is healthy until a deploy of v2.3.1 at T-30min.
That release shipped a config change: HikariCP `maximumPoolSize` 50 -> 5.
Under normal traffic the DB connection pool exhausts, requests queue then time
out ("HikariPool-1 - Connection is not available, request timed out after
30000ms"), checkout latency explodes and the api-gateway sees upstream 5xx.

The data is shaped so an agent can correlate:
    latency/error spike  ->  pool-timeout errors  ->  deploy at T-30  ->  config change.

Ingestion goes through HEC *inside* the container (host cannot reach :8088),
with explicit per-event epoch timestamps so the incident sits in real time.
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402

random.seed(42)

NOW = int(time.time())
INCIDENT_START = NOW - 30 * 60        # deploy + breakage 30 min ago
WINDOW_START = NOW - 90 * 60          # 60 min of healthy baseline before that

HOSTS = ["checkout-prod-01", "checkout-prod-02", "checkout-prod-03"]
GW_HOSTS = ["gateway-prod-01", "gateway-prod-02"]


def _trace() -> str:
    return "%016x" % random.getrandbits(64)


def checkout_event(ts: float, healthy: bool) -> dict:
    """One checkout-service application log line (JSON)."""
    host = random.choice(HOSTS)
    if healthy:
        latency = int(random.gauss(120, 30))
        latency = max(40, latency)
        pool_active = random.randint(2, 12)
        status, level, msg = 200, "INFO", "checkout completed"
        if random.random() < 0.01:  # rare baseline error
            status, level, msg = 500, "ERROR", "transient downstream error"
        pool_wait = random.randint(0, 5)
    else:
        # pool exhausted: most requests queue on the pool then time out
        exhausted = random.random() < 0.55
        if exhausted:
            latency = int(random.gauss(8000, 1500))
            status, level = 500, "ERROR"
            msg = ("HikariPool-1 - Connection is not available, request timed "
                   "out after 30000ms")
            pool_wait = int(random.gauss(30000, 4000))
        else:
            latency = int(random.gauss(2600, 900))
            status, level, msg = 200, "WARN", "checkout slow: pool contention"
            pool_wait = int(random.gauss(2200, 800))
        latency = max(200, latency)
        pool_active = 5          # capped at the new (broken) maximumPoolSize
        pool_wait = max(0, pool_wait)

    return {
        "time": round(ts, 3),
        "host": host,
        "source": "checkout-service",
        "sourcetype": "checkout:app",
        "index": settings.INDEX_LOGS,
        "event": {
            "service": "checkout-service",
            "version": "v2.3.1" if not healthy else "v2.3.0",
            "level": level,
            "message": msg,
            "latency_ms": latency,
            "status": status,
            "trace_id": _trace(),
            "host": host,
            "pool_active": pool_active,
            "pool_max": 5 if not healthy else 50,
            "pool_wait_ms": pool_wait,
        },
    }


def gateway_event(ts: float, healthy: bool) -> dict:
    """api-gateway access log calling the checkout upstream."""
    host = random.choice(GW_HOSTS)
    if healthy:
        status = 200 if random.random() > 0.01 else 502
        resp = int(random.gauss(150, 40))
    else:
        status = random.choice([502, 504, 500, 200, 504])
        resp = int(random.gauss(7000, 2000)) if status != 200 else int(random.gauss(2500, 800))
    return {
        "time": round(ts, 3),
        "host": host,
        "source": "api-gateway",
        "sourcetype": "gateway:access",
        "index": settings.INDEX_LOGS,
        "event": {
            "service": "api-gateway",
            "upstream": "checkout-service",
            "method": "POST",
            "path": "/api/v1/checkout",
            "status": status,
            "response_ms": max(20, resp),
            "host": host,
        },
    }


def deploy_event() -> dict:
    """The smoking gun: the deploy that introduced the bad pool config."""
    return {
        "time": INCIDENT_START,
        "host": "ci-runner-01",
        "source": "ci-cd-pipeline",
        "sourcetype": "deploy:event",
        "index": settings.INDEX_DEPLOYS,
        "event": {
            "service": "checkout-service",
            "version": "v2.3.1",
            "previous_version": "v2.3.0",
            "deployed_by": "j.martinez",
            "environment": "production",
            "change_summary": "Performance tuning + dependency bumps",
            "config_changes": [
                {"key": "spring.datasource.hikari.maximumPoolSize",
                 "old": "50", "new": "5"},
                {"key": "spring.datasource.hikari.connectionTimeout",
                 "old": "30000", "new": "30000"},
            ],
            "commit": "a1b9f33",
            "pr": "#4827",
            "pr_title": "Reduce idle DB connections to cut RDS cost",
        },
    }


def build_batch() -> list[dict]:
    events: list[dict] = []

    # Healthy baseline: WINDOW_START .. INCIDENT_START  (~3 checkout/sec sampled)
    t = WINDOW_START
    while t < INCIDENT_START:
        for _ in range(random.randint(2, 4)):
            events.append(checkout_event(t + random.random() * 10, healthy=True))
        for _ in range(random.randint(1, 2)):
            events.append(gateway_event(t + random.random() * 10, healthy=True))
        t += 10

    # The deploy
    events.append(deploy_event())

    # Broken period: INCIDENT_START .. NOW
    t = INCIDENT_START + 20  # ~20s for pods to roll
    while t < NOW:
        for _ in range(random.randint(3, 5)):
            events.append(checkout_event(t + random.random() * 10, healthy=False))
        for _ in range(random.randint(2, 3)):
            events.append(gateway_event(t + random.random() * 10, healthy=False))
        t += 10

    return events


def push_via_hec(events: list[dict]) -> None:
    """Write events to HEC. Prefers a network HEC URL (set SPLUNK_HEC_URL, e.g. for
    docker-compose); otherwise copies an NDJSON batch into the container and POSTs to
    localhost:8088 (host setup where 8088 isn't published)."""
    import os
    payload = "\n".join(json.dumps(e) for e in events)

    hec_url = os.getenv("SPLUNK_HEC_URL", "").rstrip("/")
    if hec_url:
        import httpx
        with httpx.Client(verify=False, timeout=60) as c:
            res = c.post(f"{hec_url}/services/collector/event",
                         headers={"Authorization": f"Splunk {settings.HEC_TOKEN}"},
                         content=payload.encode("utf-8"))
            print("HEC(net) response:", res.text.strip()[:200])
        return

    with tempfile.NamedTemporaryFile("w", suffix=".ndjson", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(payload)
        local = fh.name

    remote = "/tmp/vigil_seed.ndjson"
    cid = settings.SPLUNK_CONTAINER
    subprocess.run(["docker", "cp", local, f"{cid}:{remote}"], check=True)
    cmd = (
        f"curl -s -k https://localhost:8088/services/collector/event "
        f"-H 'Authorization: Splunk {settings.HEC_TOKEN}' "
        f"--data-binary @{remote}"
    )
    res = subprocess.run(
        ["docker", "exec", cid, "bash", "-lc", cmd],
        capture_output=True, text=True, check=True,
    )
    print("HEC response:", res.stdout.strip()[:200])
    Path(local).unlink(missing_ok=True)


def main() -> None:
    events = build_batch()
    n_logs = sum(1 for e in events if e["index"] == settings.INDEX_LOGS)
    n_dep = sum(1 for e in events if e["index"] == settings.INDEX_DEPLOYS)
    print(f"Generated {len(events)} events ({n_logs} logs, {n_dep} deploy).")
    print(f"Window: {time.strftime('%H:%M', time.localtime(WINDOW_START))} "
          f"-> deploy {time.strftime('%H:%M', time.localtime(INCIDENT_START))} "
          f"-> now {time.strftime('%H:%M', time.localtime(NOW))}")
    push_via_hec(events)
    print("Done. Give Splunk ~10s to index, then search index=app_logs.")


if __name__ == "__main__":
    main()
