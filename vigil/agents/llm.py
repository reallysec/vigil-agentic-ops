"""LLM backend + agentic tool-use loop.

Backend is any OpenAI-compatible chat-completions endpoint (Volcengine Ark by
default; also works with OpenAI, Ollama, or a Splunk hosted-model gateway).

`AgentRunner.run()` drives a full ReAct-style loop: the model thinks, calls
Splunk MCP tools, observes the rows, and iterates until it produces a final
answer. Every step is streamed out via an `on_event` callback so the UI can
render the agent's live reasoning chain.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402


def make_client() -> OpenAI:
    if not settings.LLM_API_KEY:
        raise RuntimeError(
            "No LLM_API_KEY set. Put your Volcengine Ark key in .env "
            "(LLM_API_KEY=...). See .env.example."
        )
    return OpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY)


@dataclass
class Step:
    """One step in an agent's reasoning chain (for the UI / audit trail)."""
    kind: str                       # "thought" | "tool_call" | "observation" | "final"
    agent: str
    content: str
    tool: str | None = None
    tool_args: dict | None = None
    meta: dict = field(default_factory=dict)


EventSink = Callable[[Step], None]


class AgentRunner:
    """Runs one named agent's tool-use loop against a set of tool functions."""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[dict],
        tool_impl: dict[str, Callable[..., Any]],
        on_event: EventSink | None = None,
        client: OpenAI | None = None,
        max_iters: int = 8,
    ) -> None:
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools
        self.tool_impl = tool_impl
        self.on_event = on_event or (lambda s: None)
        self.client = client or make_client()
        self.max_iters = max_iters

    def _emit(self, step: Step) -> None:
        self.on_event(step)

    def run(self, task: str, context: str = "") -> str:
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": (context + "\n\n" + task).strip()},
        ]

        for _ in range(self.max_iters):
            resp = self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=messages,
                tools=self.tools or None,
                temperature=settings.LLM_TEMPERATURE,
                max_tokens=settings.LLM_MAX_TOKENS,
            )
            msg = resp.choices[0].message

            if msg.content and msg.content.strip():
                self._emit(Step("thought", self.name, msg.content.strip()))

            if not msg.tool_calls:
                final = (msg.content or "").strip()
                self._emit(Step("final", self.name, final))
                return final

            # Record the assistant turn (with its tool calls) before answering them.
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name,
                                     "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })

            for tc in msg.tool_calls:
                fname = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                self._emit(Step("tool_call", self.name,
                                 f"{fname}({_fmt_args(args)})",
                                 tool=fname, tool_args=args))
                try:
                    result = self.tool_impl[fname](**args)
                    result_text = json.dumps(result, default=str)
                except Exception as exc:  # surface tool errors back to the model
                    result_text = json.dumps({"error": str(exc)})

                self._emit(Step("observation", self.name,
                                _summarize_obs(result_text), tool=fname,
                                meta={"raw_len": len(result_text)}))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text[:12000],
                })

        self._emit(Step("final", self.name,
                         "(stopped: reached max reasoning iterations)"))
        return "(no conclusion: max iterations reached)"


def _fmt_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        sv = str(v)
        if len(sv) > 60:
            sv = sv[:57] + "..."
        parts.append(f"{k}={sv}")
    return ", ".join(parts)


def _summarize_obs(result_text: str) -> str:
    try:
        obj = json.loads(result_text)
    except json.JSONDecodeError:
        return result_text[:200]
    if isinstance(obj, dict):
        if "row_count" in obj:
            rows = obj.get("rows", [])
            preview = json.dumps(rows[:2], default=str)
            if len(preview) > 220:
                preview = preview[:217] + "..."
            return f"{obj['row_count']} rows; sample: {preview}"
        if "error" in obj:
            return f"ERROR: {obj['error']}"
    return result_text[:200]
