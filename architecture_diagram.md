# Vigil — Architecture Diagram

Vigil is an autonomous on-call SRE built as a team of AI agents on top of Splunk. Every agent
reaches Splunk only through the **Splunk MCP Server** (Model Context Protocol, token-based
auth); every conclusion and decision is written back to Splunk for audit.

## System architecture

```mermaid
flowchart TB
  subgraph SPL["Splunk Enterprise 10.4"]
    DATA[("app_logs · deploy_events")]
    AUDIT[("vigil:incident · vigil:action")]
    DASH["Native dashboard + saved searches"]
  end

  DET["Anomaly detection<br/>(trailing z-score SPL)"] --> ALERT["Incident raised"]
  ALERT --> ORCH(["Orchestrator"])

  subgraph TEAM["Agent team (each runs its own tool-use loop)"]
    direction LR
    D["Detective<br/>root cause"]
    V["Verifier<br/>second opinion"]
    H["Historian<br/>change history"]
    R["Remediator<br/>the fix"]
    C["Communicator<br/>record"]
    D --> V --> H --> R --> C
  end

  ORCH --> TEAM
  TEAM -->|"MCP tools · JWT auth"| MCP[["Splunk MCP Server<br/>vigil/splunk_mcp/server.py"]]
  MCP <-->|"splunk_search · list_indexes<br/>server_info · write_event"| DATA

  R --> GATE{"verified confidence ≥ threshold<br/>and autonomy enabled?"}
  GATE -->|yes| AUTO["Auto-apply<br/>(human veto window)"]
  GATE -->|no| HUMAN["Manual approval"]
  AUTO --> C
  HUMAN --> C
  C -->|"write_event"| AUDIT
  AUDIT --> DASH

  subgraph UI["Operations console (FastAPI + WebSocket + SPA)"]
    CONSOLE["Console · Alerts · War Room<br/>Incidents · Audit · Agents"]
  end
  CONSOLE <-->|"REST + /ws stream"| ORCH
  CONSOLE -->|"drill-down"| DASH
```

## End-to-end incident flow

```mermaid
sequenceDiagram
  participant Splunk
  participant Vigil as Vigil engine
  participant Detective
  participant Verifier
  participant Operator as Human operator

  Vigil->>Splunk: anomaly search (z-score over p95 latency)
  Splunk-->>Vigil: anomaly ~58× baseline
  Vigil->>Detective: investigate
  Detective->>Splunk: multi-step SPL via MCP
  Splunk-->>Detective: rows (latency, errors, deploys)
  Detective-->>Vigil: root cause (pool 50→5)
  Vigil->>Verifier: try to refute it
  Verifier->>Splunk: independent SPL via MCP
  Verifier-->>Vigil: verdict + calibrated confidence
  alt confidence ≥ threshold and autonomy on
    Vigil->>Splunk: apply fix (human veto window)
  else
    Vigil->>Operator: request approval
    Operator-->>Vigil: approve / hold
  end
  Vigil->>Splunk: write incident + decision (audit)
```

## Components

| Component | Path | Role |
|:--|:--|:--|
| Splunk MCP Server | `vigil/splunk_mcp/server.py` | Exposes Splunk to agents over MCP: `splunk_search`, `splunk_list_indexes`, `splunk_server_info`, `splunk_write_event`. |
| Agent orchestrator | `vigil/agents/team.py` | Coordinates the five agents, the confidence gate, and the audit write-back. |
| Tool-use loop | `vigil/agents/llm.py` | OpenAI-compatible ReAct loop; streams every step to the UI. |
| MCP bridge | `vigil/agents/mcp_bridge.py` | Lets the synchronous agent loop discover and call MCP tools. |
| Console backend | `vigil/ui/server.py`, `vigil/ui/data.py` | FastAPI + WebSocket; anomaly detection, signals, incident/decision history. |
| Console frontend | `vigil/ui/static/index.html` | Single-page operations console. |
| Splunk objects | `vigil/splunk_app/setup_splunk.py` | Pushes the native dashboard and saved searches. |
| Bootstrap | `scripts/bootstrap.py` | One-command, idempotent environment setup. |

**Data plane:** Splunk Enterprise 10.4 — ingestion via HEC, reads via REST, audit write-back
via `receivers/simple`. Anomaly detection, signals, and history are all real SPL.

**LLM backend:** any OpenAI-compatible endpoint with tool calling (Volcengine Ark
`ark-code-latest` by default).
