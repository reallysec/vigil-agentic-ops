# Vigil — 3-Minute Demo Script

**Goal of the demo:** show a real incident going from *auto-detected* → *diagnosed* →
*adversarially verified* → *fixed* in under 3 minutes, with every claim grounded in live
Splunk data and the whole decision auditable back inside Splunk.

**Before you record**
- `docker start 17c09f1147ce` and wait until healthy.
- `python scripts/bootstrap.py` (fresh, time-relative demo data + native Splunk dashboard).
- `python vigil/ui/server.py` → open `http://127.0.0.1:8800`.
- Have a second browser tab logged into Splunk (`http://localhost:8000`, `admin / Admin@123`).
- Optional: switch the console to dark mode for the "live ops" feel.

Total runtime ~2:50. Times are cumulative.

---

### Scene 1 — The hook (0:00–0:20)
**On screen:** Operations Console. The violet anomaly strip at the top, the red **1 active
P1** banner, the two live Splunk charts (latency spiking at the deploy marker; the DB pool
ceiling dropping 50→5).

> "This is Vigil — an autonomous on-call SRE team. Right now it has already **detected**
> an incident on checkout-service on its own: p95 latency is ~58× its normal baseline, with
> 58% of requests failing. No human wrote this alert — Vigil found it with a Splunk
> anomaly search."

**Beat:** hover the latency chart so the tooltip shows `p95 9.7s · 56% 5xx`, then click it —
Splunk opens the underlying search with the real events. "And every number is drillable
straight into Splunk."

### Scene 2 — Dispatch the team (0:20–0:35)
**On screen:** Click **Investigate now** (or the alert → *Investigate with Vigil*). The
War Room opens: the live signals chart sits under the hero, the 5-agent stepper lights up.

> "I hand it to the agent team. They don't get a script — each one reasons on its own,
> querying live Splunk through the Splunk MCP Server."

### Scene 3 — Watch it reason (0:35–1:25)
**On screen:** The Detective card streams: thoughts, the SPL queries it runs, the rows it
gets back, then its conclusion. Expand **Show N SPL queries** to reveal the evidence.

> "The Detective finds the inflection point at the deploy, the HikariCP pool timeouts, and
> pins the cause: release v2.3.1 cut the DB connection pool from 50 to 5."

**On screen:** The Verifier card (violet) runs *its own* searches.

> "Then a Verifier acts as an adversarial skeptic — it tries to **disprove** that root cause
> with independent queries, and reports a *calibrated* confidence. That number, not a
> self-report, is what decides what happens next."

### Scene 4 — Responsible autonomy (1:25–2:05)
**On screen:** Historian (the bad deploy + PR), Remediator (the rollback plan). Then the
green **autonomy** card appears with the confidence chip and an 8-second countdown.

> "Because verified confidence cleared the bar, Vigil is allowed to apply the fix
> itself — but it still gives me a veto window."

**Beat:** Let it auto-apply (or click **Run it now**). If you want to show the manual path
instead, toggle Autonomy off in Agents first, or note the threshold is configurable.

> "Below the threshold it always waits for a human. Either way, the decision is logged."

### Scene 5 — Resolved + the payoff (2:05–2:35)
**On screen:** Hero flips to **Resolved**, the MTTR comparison bar appears.

> "Resolved in about two minutes. A human on-call would average closer to forty-five."

**On screen:** Go to **Audit**. Show the decision log: this incident, the verdict, *Auto-
applied / You approved*, the confidence. Click **Open in Splunk**.

> "And here's the part that matters for trust: the AI's own conclusion and decision are
> written back into Splunk. You can search what Vigil decided, and why."

### Scene 6 — Lives in Splunk (2:35–2:50)
**On screen:** The Splunk tab showing the native **Vigil · Agentic Ops** dashboard
(latency chart, resolved count, the audited root-cause table, the auto-vs-human decisions).

> "Vigil doesn't just read Splunk — it lives in it. Detection, diagnosis, verification,
> a human-gated fix, and a full audit trail. That's agentic ops."

---

**If you have 30 extra seconds:** open the **Agents** page to show the team and the
Automation settings (autonomy on/off, confidence threshold, adversarial verification),
and toggle dark mode.

**One-line close:** "Detected, diagnosed, verified, and fixed — grounded in your Splunk
data the whole way."
