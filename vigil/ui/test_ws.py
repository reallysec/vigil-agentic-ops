"""End-to-end test of the war-room WebSocket: stream steps, auto-approve, finish."""
import asyncio
import json

import websockets


async def main() -> None:
    uri = "ws://127.0.0.1:8800/ws"
    async with websockets.connect(uri, max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "start",
            "alert": "ALERT [P1] checkout-service: p95 > 5s, 500s spiking ~25min.",
        }))
        phases = tool_calls = finals = 0
        approved_sent = False
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=240)
            m = json.loads(raw)
            k = m.get("kind")
            if k == "phase":
                phases += 1
                print(f"[PHASE] {m['content']}")
            elif k == "tool_call":
                tool_calls += 1
            elif k == "final":
                finals += 1
                print(f"[FINAL:{m['agent']}] {m['content'][:90]}...")
            elif k == "approval_required":
                print(f"[APPROVAL REQUIRED] plan len={len(m['content'])} -> sending approve")
                await ws.send(json.dumps({"type": "approve"}))
                approved_sent = True
            elif k in ("approved", "rejected"):
                print(f"[{k.upper()}] {m['content'][:70]}")
            elif k == "done":
                print(f"[DONE] approved={m.get('approved')} summary_len={len(m.get('content',''))}")
                break
            elif k == "error":
                print(f"[ERROR] {m['content']}")
                break
        print(f"\nphases={phases} tool_calls={tool_calls} finals={finals} approval_sent={approved_sent}")


if __name__ == "__main__":
    asyncio.run(main())
