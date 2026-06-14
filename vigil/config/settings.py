"""Central configuration for the Vigil agentic-ops project.

All values can be overridden via environment variables (see .env.example).
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:  # dotenv optional
    pass


# --- Splunk connection (REST management API, port 8089) ---
SPLUNK_HOST = os.getenv("SPLUNK_HOST", "localhost")
SPLUNK_PORT = int(os.getenv("SPLUNK_PORT", "8089"))
SPLUNK_SCHEME = os.getenv("SPLUNK_SCHEME", "https")
SPLUNK_USERNAME = os.getenv("SPLUNK_USERNAME", "admin")
SPLUNK_PASSWORD = os.getenv("SPLUNK_PASSWORD", "Admin@123")
# Preferred for production: a Splunk JWT auth token (Bearer). Falls back to basic auth.
SPLUNK_TOKEN = os.getenv("SPLUNK_TOKEN", "")
SPLUNK_VERIFY_SSL = os.getenv("SPLUNK_VERIFY_SSL", "false").lower() == "true"

SPLUNK_BASE_URL = f"{SPLUNK_SCHEME}://{SPLUNK_HOST}:{SPLUNK_PORT}"

# --- Docker container (used only for HEC ingestion, since 8088 is unmapped) ---
SPLUNK_CONTAINER = os.getenv("SPLUNK_CONTAINER", "17c09f1147ce")
HEC_TOKEN = os.getenv("HEC_TOKEN", "d854b2b9-68c7-4c61-a298-e6e1c12acc7c")

# --- Demo data indexes ---
INDEX_LOGS = os.getenv("INDEX_LOGS", "app_logs")
INDEX_DEPLOYS = os.getenv("INDEX_DEPLOYS", "deploy_events")

# --- LLM backend (OpenAI-compatible: Volcengine Ark / OpenAI / Ollama / Splunk hosted) ---
# Default endpoint: Volcengine Ark (火山方舟), which is OpenAI-compatible.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
LLM_API_KEY = os.getenv("LLM_API_KEY", os.getenv("ARK_API_KEY", ""))
LLM_MODEL = os.getenv("LLM_MODEL", "doubao-seed-1-6-250615")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))

# --- MCP server ---
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8930"))
