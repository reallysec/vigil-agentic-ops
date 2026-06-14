"""CLI entrypoint: fire an alert at the Vigil team and watch the live war room.

Usage:
    python vigil/run_incident.py
    python vigil/run_incident.py --alert "Checkout p95 latency > 5s, 500s spiking"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.markdown import Markdown  # noqa: E402

from agents.llm import Step  # noqa: E402
from agents.team import VigilTeam  # noqa: E402

console = Console()

_AGENT_COLOR = {
    "Orchestrator": "bold white on blue",
    "Detective": "cyan",
    "Historian": "magenta",
    "Remediator": "yellow",
    "Communicator": "green",
}


def render(step: Step) -> None:
    color = _AGENT_COLOR.get(step.agent, "white")
    if step.kind == "phase":
        console.rule(f"[{color}]{step.content}")
        return
    tag = f"[{color}]{step.agent}[/]"
    if step.kind == "thought":
        console.print(f"{tag} 💭 {step.content}")
    elif step.kind == "tool_call":
        console.print(f"{tag} [bold]→ SPL[/] [dim]{step.content}[/]")
    elif step.kind == "observation":
        console.print(f"{tag} [dim]👁  {step.content}[/]")
    elif step.kind == "final":
        console.print(Panel(Markdown(step.content or "(empty)"),
                            title=f"{step.agent} — conclusion",
                            border_style=color.split()[0]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alert", default=(
        "ALERT [P1] checkout-service: p95 latency > 5000ms and HTTP 500 rate "
        "spiking on /api/v1/checkout for the last ~25 minutes."))
    args = ap.parse_args()

    console.print(Panel.fit("[bold]SENTINEL[/] — autonomous on-call SRE team",
                            subtitle="grounded in live Splunk via MCP"))
    console.print(f"[bold red]INCOMING ALERT:[/] {args.alert}\n")

    team = VigilTeam(on_event=render)
    try:
        report = team.respond(args.alert)
    finally:
        team.close()

    console.rule("[bold]✅ Incident response complete")
    console.print(Panel(Markdown(report.communication or "(no summary)"),
                        title="📣 Final Incident Record", border_style="green"))


if __name__ == "__main__":
    main()
