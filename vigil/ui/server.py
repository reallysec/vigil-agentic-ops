"""Web 'war room' backend for Vigil.

Serves the SPA and a WebSocket that:
  - receives an alert from the browser,
  - runs the VigilTeam in a background thread,
  - streams every reasoning Step to the browser in real time,
  - bridges the human-in-the-loop approval: the team blocks at the remediation
    gate until the browser sends {"type":"approve"|"reject"}.

Run:  python vigil/ui/server.py    ->  http://127.0.0.1:8800
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.llm import Step  # noqa: E402
from agents.team import VigilTeam  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data as console_data  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Vigil Console")


def step_to_dict(step: Step) -> dict:
    d = asdict(step)
    d["ts"] = time.time()
    return d


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/alerts")
async def api_alerts() -> dict:
    return {"alerts": console_data.list_alerts()}


@app.get("/api/incidents")
async def api_incidents() -> dict:
    return {"incidents": await asyncio.to_thread(console_data.list_incidents)}


@app.get("/api/actions")
async def api_actions() -> dict:
    return {"actions": await asyncio.to_thread(console_data.list_actions)}


@app.get("/api/stats")
async def api_stats() -> dict:
    return await asyncio.to_thread(console_data.stats)


@app.get("/api/env")
async def api_env() -> dict:
    return await asyncio.to_thread(console_data.env_info)


@app.get("/api/config")
async def api_get_config() -> dict:
    return await asyncio.to_thread(console_data.get_config)


@app.post("/api/config")
async def api_set_config(patch: dict) -> dict:
    return await asyncio.to_thread(console_data.set_config, patch)


@app.get("/api/signals")
async def api_signals(service: str = "checkout-service") -> dict:
    return await asyncio.to_thread(console_data.signals, service)


@app.get("/api/detect")
async def api_detect() -> dict:
    return await asyncio.to_thread(console_data.detection_summary)


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    approval_event = threading.Event()
    approval_result = {"approved": True, "mode": "human"}

    try:
        first = await websocket.receive_json()
    except WebSocketDisconnect:
        return
    if first.get("type") != "start":
        await websocket.close()
        return
    alert = first.get("alert") or (
        "ALERT [P1] checkout-service: p95 latency > 5000ms and HTTP 500 rate "
        "spiking on /api/v1/checkout for the last ~25 minutes.")

    def on_event(step: Step) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, step_to_dict(step))

    def approval_gate(_plan: str) -> dict:
        # Blocks the team thread until the browser answers (or 5-min timeout).
        # The browser decides auto (autonomy + high confidence) vs human approval.
        approval_event.wait(timeout=300)
        return dict(approval_result)

    cfg = console_data.get_config()

    def run_team() -> None:
        team = VigilTeam(on_event=on_event, approval_gate=approval_gate,
                            autonomy_threshold=cfg["autonomy_threshold"],
                            include_verifier=cfg["verifier_enabled"])
        try:
            report = team.respond(alert)
            loop.call_soon_threadsafe(queue.put_nowait, {
                "kind": "done", "agent": "Orchestrator",
                "content": report.communication, "approved": report.approved,
                "ts": time.time()})
        except Exception as exc:  # surface failures to the UI
            loop.call_soon_threadsafe(queue.put_nowait, {
                "kind": "error", "agent": "Orchestrator",
                "content": f"{type(exc).__name__}: {exc}", "ts": time.time()})
        finally:
            team.close()
            loop.call_soon_threadsafe(queue.put_nowait, {"kind": "_end"})

    worker = threading.Thread(target=run_team, daemon=True)
    worker.start()

    async def pump_incoming() -> None:
        """Watch for the approve/reject decision from the browser."""
        try:
            while not approval_event.is_set():
                msg = await websocket.receive_json()
                if msg.get("type") in ("approve", "reject"):
                    approval_result["approved"] = msg["type"] == "approve"
                    approval_result["mode"] = msg.get("mode", "human")
                    approval_event.set()
                    return
        except WebSocketDisconnect:
            approval_result["approved"] = False
            approval_event.set()

    incoming = asyncio.create_task(pump_incoming())

    try:
        while True:
            item = await queue.get()
            if item.get("kind") == "_end":
                break
            await websocket.send_json(item)
    except WebSocketDisconnect:
        pass
    finally:
        approval_event.set()  # unblock the worker if still waiting
        incoming.cancel()


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8800, log_level="warning")
