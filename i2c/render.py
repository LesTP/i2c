"""Operator-facing text renderers for ``i2c.control`` dataclasses.

Shared by every surface (the ``i2c`` CLI and the transport adapters such as the
Telegram bot) so there is exactly one place that turns ``control``'s structured
returns into operator text. ``control`` itself stays prose-free (D-pkg-7);
surfaces add only their own transport concerns (JSON encoding, message chunking,
markdown escaping) on top of these.
"""

from __future__ import annotations

from i2c import control


def _fmt_deps(deps: list[str]) -> str:
    return ", ".join(deps) if deps else "(none)"


def _fmt_budget(budget: dict[str, int] | None) -> str:
    if not budget:
        return "(none)"
    return ", ".join(f"{k}={v}" for k, v in budget.items())


def _fmt_step(step: control.StepView) -> str:
    line = f"  {step.phase}.{step.step}  [{step.status}]  {step.title}"
    if step.commit:
        line += f"  ({step.commit})"
    return line


def _fmt_devlog(entry: control.DevlogView) -> str:
    action_id = f"{entry.phase}" if entry.step is None else f"{entry.phase}.{entry.step}"
    head = f"  - {action_id} {entry.action} -> {entry.outcome}"
    if entry.commit:
        head += f" ({entry.commit})"
    return f"{head} — {entry.summary}"


def _fmt_decision(d: control.DecisionView) -> str:
    priority = d.priority or "—"
    body = f"  - {d.id} [{d.status} · {priority}] {d.title}"
    if d.decision:
        body += f" — {d.decision}"
    return body


def _render_status(r: control.StatusReport) -> str:
    lines = [
        f"Phase:        {r.phase}",
        f"State:        {r.state}",
        f"Module:       {r.module or '—'}",
        f"Regime:       {r.regime or '—'}",
        f"Dependencies: {_fmt_deps(r.dependencies)}",
        f"Budget:       {_fmt_budget(r.budget)}",
        "",
        f"Steps (phase {r.phase}):",
    ]
    lines += [_fmt_step(s) for s in r.steps] or ["  (none)"]
    lines += ["", "Gotchas:"]
    lines += [f"  - {g}" for g in r.gotchas] or ["  (none)"]
    lines += ["", "Open decisions:"]
    lines += [_fmt_decision(d) for d in r.open_decisions] or ["  (none)"]
    lines += ["", "Recent activity:"]
    lines += [_fmt_devlog(e) for e in r.recent_activity] or ["  (none)"]
    return "\n".join(lines)


def _render_dispatch(d: control.Dispatch) -> str:
    return f"ACTION: {d.action}\nNEXT: {d.next_state}"


def _render_phase_summary(s: control.PhaseSummary) -> str:
    regime = s.regime or "—"
    status = s.status or "—"
    lines = [
        f"Phase {s.phase} Summary — {s.module or '—'}: {s.title or '—'} "
        f"({regime}, {status})",
        "",
        "Steps:",
    ]
    lines += [_fmt_step(st) for st in s.steps] or ["  (none)"]
    lines += ["", "Decisions added this phase:"]
    lines += [_fmt_decision(d) for d in s.decisions] or ["  (none)"]
    lines += ["", "Devlog:"]
    lines += [_fmt_devlog(e) for e in s.devlog] or ["  (none)"]
    lines += ["", "Open items:"]
    lines += [_fmt_decision(d) for d in s.open_items] or ["  (none)"]
    return "\n".join(lines)


def _render_decisions(decisions: list[control.DecisionView]) -> str:
    if not decisions:
        return "(no decisions)"
    return "\n".join(_fmt_decision(d).lstrip() for d in decisions)


def _render_devlog_list(entries: list[control.DevlogView]) -> str:
    if not entries:
        return "(no devlog entries)"
    return "\n".join(_fmt_devlog(e).lstrip() for e in entries)


def _render_escalation(e: control.EscalationView) -> str:
    lines = [
        f"Phase:     {e.phase}",
        f"Escalated: {'yes (state=audit_escalation)' if e.is_escalated else 'no'}",
        "",
        "Trigger:",
        _fmt_devlog(e.entry) if e.entry else "  (none)",
        "",
        "Preceding context:",
    ]
    lines += [_fmt_devlog(x) for x in e.surrounding] or ["  (none)"]
    lines += ["", "Open decisions (this phase):"]
    lines += [_fmt_decision(d) for d in e.open_decisions] or ["  (none)"]
    return "\n".join(lines)


def _render_logs(entries: list[control.IterationLog]) -> str:
    if not entries:
        return "(no iterations logged)"
    lines = []
    for e in entries:
        tok = ""
        if e.tokens:
            tok = (
                f"  tokens(in/out/cached)="
                f"{e.tokens['input']}/{e.tokens['output']}/{e.tokens['cached']}"
            )
        lines.append(
            f"  iter {e.iter} [{e.backend} {e.action} exit={e.exit_code}] "
            f"{e.timestamp}{tok}"
        )
        lines.append(f"      reason: {e.reason}")
    return "\n".join(lines)


def _render_log_transcript(e: control.IterationLog) -> str:
    head = (
        f"iter {e.iter} [{e.backend} {e.action} exit={e.exit_code}] {e.timestamp}\n"
        f"reason: {e.reason}\n"
    )
    body = e.transcript if e.transcript is not None else "(no transcript file)"
    return head + "\n" + body


def _render_portfolio(r: control.PortfolioReport) -> str:
    if not r.projects:
        return f"No i2c projects found under {r.root}"
    lines = [f"Portfolio: {r.root}  ({len(r.projects)} project(s))", ""]
    for p in r.projects:
        if p.error:
            lines.append(f"  !! {p.name}  [load error] {p.error}")
            continue
        if p.is_escalated:
            flag, label = "!!", "ESCALATED"
        elif p.state == "audit_boundary":
            flag, label = " *", "audit_boundary"
        else:
            flag, label = "  ", p.state
        line = f"  {flag} {p.name}: phase {p.phase} [{label}] next={p.next_action}"
        if p.module:
            line += f" module={p.module}"
        if p.open_decisions:
            line += f" open_dec={p.open_decisions}"
        lines.append(line)
        if p.is_escalated and p.escalation_reason:
            lines.append(f"        reason: {p.escalation_reason}")
    return "\n".join(lines)


def _render_boundary(r: control.BoundaryResult) -> str:
    return f"{r.outcome} — phase {r.phase}, state {r.state}"


def _render_diagnosis(d: control.Diagnosis) -> str:
    target = f"iter {d.target}" if d.target is not None else "(no loop log)"
    lines = [
        f"Target:         {target}",
        f"Classification: {d.classification}",
        f"Reconcilable:   {'yes' if d.reconcilable else 'no'}",
        f"Phase/State:    {d.phase} / {d.state}",
    ]
    if d.exit_code is not None:
        lines.append(f"Iteration exit: {d.exit_code}")
        lines.append(f"Reason:         {d.reason or '—'}")
    if d.malformed_signal:
        lines.append(
            "Note:           target failed on a missing/malformed exit signal"
        )
    lines += ["", "Drift findings:"]
    if d.findings:
        for f in d.findings:
            mark = "fixable" if f.reconcilable else "judgment"
            loc = ""
            if f.phase is not None:
                loc = f" [{f.phase}" + (f".{f.step}" if f.step is not None else "") + "]"
            lines.append(f"  - ({mark}){loc} {f.message}")
            if f.proposal:
                lines.append(f"      -> {f.proposal}")
    else:
        lines.append("  (none)")
    if d.escalation:
        lines += ["", "Escalation context:", _fmt_devlog(d.escalation)]
    return "\n".join(lines)


def _render_reconcile(r: control.ReconcileReport) -> str:
    head = "RECONCILED" if r.applied else "DRY-RUN (no .state writes)"
    lines = [f"reconcile — {head}", "", "Reconcilable drift:"]
    if r.items:
        for it in r.items:
            mark = "applied" if it.applied else "would apply"
            lines.append(f"  - [{mark}] {it.message}")
            lines.append(f"      -> {it.proposal}")
    else:
        lines.append("  (none)")
    lines += ["", "Needs human judgment (not auto-applied):"]
    if r.skipped:
        lines += [f"  - {s.message}" for s in r.skipped]
    else:
        lines.append("  (none)")
    if not r.applied and r.items:
        lines += ["", "Re-run with --apply to write these via i2c state."]
    return "\n".join(lines)
