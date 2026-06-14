"""One-command setup for Vigil against a running Splunk.

Idempotent: safe to re-run. It will
  1. wait for Splunk's REST API,
  2. create the demo indexes (app_logs, deploy_events),
  3. enable HEC and ensure the 'vigil' token,
  4. enable token auth and mint a JWT (-> .splunk_token),
  5. seed the demo incident,
  6. push the native Splunk dashboard + saved searches.

Usage:  python scripts/bootstrap.py
Env:    SPLUNK_HOST/PORT/USERNAME/PASSWORD, SPLUNK_CONTAINER, HEC_TOKEN (see .env.example)
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vigil"))
from config import settings  # noqa: E402

BASE = settings.SPLUNK_BASE_URL
AUTH = (settings.SPLUNK_USERNAME, settings.SPLUNK_PASSWORD)
TOKEN_FILE = ROOT / ".splunk_token"


def _c() -> httpx.Client:
    return httpx.Client(base_url=BASE, auth=AUTH, verify=False, timeout=60)


def wait_for_splunk(timeout: int = 180) -> None:
    print("· waiting for Splunk REST...", end="", flush=True)
    deadline = time.time() + timeout
    with _c() as c:
        while time.time() < deadline:
            try:
                if c.get("/services/server/info").status_code == 200:
                    print(" up")
                    return
            except Exception:
                pass
            print(".", end="", flush=True)
            time.sleep(4)
    raise SystemExit("\nSplunk did not become ready in time.")


def ensure_indexes() -> None:
    with _c() as c:
        for idx in (settings.INDEX_LOGS, settings.INDEX_DEPLOYS):
            r = c.post("/services/data/indexes", data={"name": idx})
            print(f"· index {idx}: {'created' if r.status_code in (200,201) else 'exists'}")


def ensure_hec() -> None:
    with _c() as c:
        c.post("/servicesNS/nobody/splunk_httpinput/data/inputs/http/http",
               data={"disabled": 0})
        r = c.post("/servicesNS/nobody/splunk_httpinput/data/inputs/http",
                   data={"name": "vigil", "index": settings.INDEX_LOGS,
                         "indexes": f"{settings.INDEX_LOGS},{settings.INDEX_DEPLOYS},main",
                         "output_mode": "json"})
        if r.status_code in (200, 201):
            tok = r.json()["entry"][0]["content"]["token"]
            print(f"· HEC token 'vigil' created: {tok}")
            print("  (set HEC_TOKEN in your .env to this value)")
        else:
            print("· HEC token 'vigil': exists (keep your .env HEC_TOKEN)")


def mint_jwt() -> None:
    with _c() as c:
        c.post("/services/admin/token-auth/tokens_auth", data={"disabled": "false"})
        r = c.post("/services/authorization/tokens",
                   data={"name": settings.SPLUNK_USERNAME, "audience": "vigil-mcp",
                         "output_mode": "json"})
        try:
            tok = r.json()["entry"][0]["content"]["token"]
            TOKEN_FILE.write_text(tok)
            print(f"· JWT minted -> {TOKEN_FILE.name}")
        except Exception:
            print("· JWT: kept existing .splunk_token" if TOKEN_FILE.exists()
                  else "· JWT: could not mint (will use basic auth)")


def run(label: str, *args: str) -> None:
    print(f"· {label}...")
    subprocess.run([sys.executable, *args], check=True, cwd=ROOT)


def main() -> None:
    wait_for_splunk()
    ensure_indexes()
    ensure_hec()
    mint_jwt()
    run("seeding demo incident", "vigil/ingest/seed_demo.py")
    run("pushing Splunk dashboard + saved searches",
        "vigil/splunk_app/setup_splunk.py")
    print("\n✅ Bootstrap complete. Start the console:")
    print("   python vigil/ui/server.py   ->  http://127.0.0.1:8800")


if __name__ == "__main__":
    main()
