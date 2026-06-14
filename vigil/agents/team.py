"""Vigil incident-response team orchestrator.

Coordinates four specialised agents over a shared Splunk MCP toolbox:

    Detective   -> autonomous root-cause analysis
    Historian   -> change/deploy context
    Remediator  -> human-gated remediation plan (+ confidence)
    Communicator-> incident write-up + audit event back into Splunk

Each agent runs its own tool-use loop. Every reasoning step is streamed through
`on_event` so a UI can render the live "war room". The orchestrator returns a
structured IncidentReport.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents import prompts  # noqa: E402
from agents.llm import AgentRunner, Step, make_client  # noqa: E402
from agents.mcp_bridge import MCPToolbox  # noqa: E402


@dataclass
class IncidentReport:
    alert: str
    detective: str = ""
    verification: str = ""
    historian: str = ""
    remediation: str = ""
    communication: str = ""
    approved: bool = False
    steps: list[Step] = field(default_factory=list)


# which MCP tools each agent is allowed to use
_TOOLSETS = {
    "Detective": ["splunk_search", "splunk_list_indexes"],
    "Verifier": ["splunk_search"],
    "Historian": ["splunk_search"],
    "Remediator": ["splunk_search"],
    "Communicator": ["splunk_search", "splunk_write_event"],
}

_PROMPTS = {
    "Detective": prompts.DETECTIVE,
    "Verifier": prompts.VERIFIER,
    "Historian": prompts.HISTORIAN,
    "Remediator": prompts.REMEDIATOR,
    "Communicator": prompts.COMMUNICATOR,
}


class VigilTeam:
    # Remediations at/above this confidence may auto-execute (with a human veto
    # window) when the operator has enabled autonomy. Below it: always manual.
    AUTONOMY_THRESHOLD = 90

    def __init__(
        self,
        on_event: Callable[[Step], None] | None = None,
        approval_gate: Callable[[str], dict] | None = None,
        autonomy_threshold: int | None = None,
        include_verifier: bool = True,
    ) -> None:
        self.on_event = on_event or (lambda s: None)
        # approval_gate(remediation_text) -> {"approved": bool, "mode": "auto"|"human"}.
        # Default: auto-approve as the operator (CLI). The web UI supplies the real gate.
        self.approval_gate = approval_gate or (lambda _plan: {"approved": True, "mode": "human"})
        self.autonomy_threshold = autonomy_threshold or self.AUTONOMY_THRESHOLD
        self.include_verifier = include_verifier
        self._collected: list[Step] = []
        self.toolbox = MCPToolbox().start()
        self.client = make_client()

    def _sink(self, step: Step) -> None:
        self._collected.append(step)
        self.on_event(step)

    def _agent(self, name: str) -> AgentRunner:
        return AgentRunner(
            name=name,
            system_prompt=_PROMPTS[name],
            tools=self.toolbox.openai_tools(_TOOLSETS[name]),
            tool_impl=self.toolbox.impl_map(_TOOLSETS[name]),
            on_event=self._sink,
            client=self.client,
            max_iters=12,
        )

    def _phase(self, label: str) -> None:
        self._sink(Step("phase", "Orchestrator", label))

    @staticmethod
    def _parse_confidence(text: str) -> int | None:
        """Pull the remediation's self-reported confidence (0-100) from its text."""
        import re
        m = re.search(r"confidence[^0-9]{0,12}(\d{1,3})", text, re.IGNORECASE)
        if not m:
            return None
        return max(0, min(100, int(m.group(1))))

    @staticmethod
    def _parse_verdict(text: str) -> str | None:
        """Pull the Verifier's VERDICT (CONFIRMED / REFUTED / CONFIRMED-WITH-CAVEATS)."""
        import re
        m = re.search(r"verdict[:\s*]+([A-Za-z][A-Za-z-]+)", text, re.IGNORECASE)
        return m.group(1).upper() if m else None

    @staticmethod
    def _first_line(text: str) -> str:
        import re
        s = re.sub(r"[`*]", "", re.sub(r"^#+\s*", "", text or "", flags=re.M))
        for ln in s.splitlines():
            t = ln.strip()
            if t and not re.match(r"^root cause:?$", t, re.IGNORECASE):
                return t[:200]
        return s.strip()[:200]

    @staticmethod
    def _parse_service(text: str) -> str | None:
        import re
        m = re.search(r"([a-z0-9-]+-service)", text or "", re.IGNORECASE)
        return m.group(1) if m else None

    def _record_action(self, remediation: str, mode: str, confidence,
                       verdict=None, root_cause=None, service=None) -> None:
        """Persist the remediation decision as an auditable Splunk event: whether a
        human approved it, Vigil acted autonomously, or it was held — plus the
        Verifier's verdict and calibrated confidence."""
        import json as _json
        executed_by = {"auto": "vigil-autonomy", "human": "human-on-call"}.get(
            mode, "held (no execution)")
        payload = _json.dumps({
            "action": "remediation_executed" if mode in ("auto", "human") else "remediation_held",
            "decision_mode": mode,
            "executed_by": executed_by,
            "confidence": confidence,
            "verdict": verdict,
            "root_cause": root_cause,
            "service": service,
            "via": "vigil-agent",
            "plan_excerpt": remediation[:300],
        })
        try:
            self.toolbox.call("splunk_write_event", event_text=payload,
                              index="main", sourcetype="vigil:action")
        except Exception:
            pass

    def respond(self, alert: str) -> IncidentReport:
        report = IncidentReport(alert=alert)
        try:
            self._phase("Detective: investigating root cause")
            report.detective = self._agent("Detective").run(
                task=f"ALERT: {alert}\nInvestigate and find the root cause.")

            if self.include_verifier:
                self._phase("Verifier: adversarially checking the root cause")
                report.verification = self._agent("Verifier").run(
                    task="Try to refute the root cause and give a calibrated confidence.",
                    context=f"Detective's proposed root cause:\n{report.detective}")

            self._phase("Historian: gathering change context")
            report.historian = self._agent("Historian").run(
                task="Provide the deploy/change context behind this incident.",
                context=f"Detective's findings:\n{report.detective}")

            self._phase("Remediator: drafting human-gated remediation plan")
            report.remediation = self._agent("Remediator").run(
                task="Propose a remediation plan for human approval.",
                context=(f"Root cause:\n{report.detective}\n\n"
                         f"Verifier's adversarial check:\n{report.verification}\n\n"
                         f"Change context:\n{report.historian}"))

            # --- Confidence-gated, human-in-the-loop remediation gate ---
            # Prefer the Verifier's independent calibrated confidence over the
            # Remediator's self-report; fall back to the plan's own number.
            conf = self._parse_confidence(report.verification)
            if conf is None:
                conf = self._parse_confidence(report.remediation)
            auto_eligible = conf is not None and conf >= self.autonomy_threshold
            self._sink(Step("approval_required", "Remediator", report.remediation,
                            meta={"confidence": conf, "auto_eligible": auto_eligible,
                                  "threshold": self.autonomy_threshold}))
            decision = self.approval_gate(report.remediation) or {}
            report.approved = bool(decision.get("approved"))
            mode = decision.get("mode", "human")
            verdict = self._parse_verdict(report.verification)
            rc = self._first_line(report.detective)
            service = self._parse_service(alert) or self._parse_service(report.detective)
            if report.approved:
                how = ("auto-executed by Vigil (confidence "
                       f"{conf} ≥ {self.autonomy_threshold})") if mode == "auto" \
                    else "approved by human operator"
                self._sink(Step("approved", "Orchestrator",
                                f"Remediation {how} — executing (simulated) and "
                                "recording action."))
                self._record_action(report.remediation, mode, conf, verdict, rc, service)
            else:
                self._sink(Step("rejected", "Orchestrator",
                                "Remediation held — operator declined execution."))
                self._record_action(report.remediation, "hold", conf, verdict, rc, service)

            self._phase("Communicator: writing incident record + audit event")
            report.communication = self._agent("Communicator").run(
                task="Write the incident record and persist the audit event.",
                context=(f"Detective:\n{report.detective}\n\n"
                         f"Verifier:\n{report.verification}\n\n"
                         f"Historian:\n{report.historian}\n\n"
                         f"Remediator:\n{report.remediation}"))
        finally:
            report.steps = list(self._collected)
        return report

    def close(self) -> None:
        self.toolbox.close()
