"""``i2c import`` — migrate an **e2e (prose-state)** project to ``.state/``.

The fleet converter scoped in ``DESIGN_migration_v1.md``. It targets e2e
prose-state projects (``DEVPLAN.md`` frontmatter + markdown, bash state machine).

Two stages (§5): **transform → report**.

- *transform* — serialize the unambiguous prose state into schema-valid
  ``.state/`` JSON: ``project.json`` (frontmatter + gotchas) and
  ``decisions.json`` (``DECISIONS.md`` with status/priority mapping). History is
  **snapshot-not-ported** by default (D-mig-3): ``steps.json`` / ``devlog.jsonl``
  are emitted empty; the prose ``DEVPLAN.md`` / ``DEVLOG.md`` remain in place.
- *report* — anything that can't be safely auto-derived (collided phase
  numbering, modules without phase records, manual follow-ups) is collected
  into ``ImportReport.manual_review`` rather than guessed.

Guarantees: **non-destructive** (writes only ``<root>/.state/``; never edits
e2e files), **dry-run by default** (``apply=False``), and refuses to overwrite
an existing ``.state/project.json`` without ``force`` — mirroring
``scaffold.init_project``. Undo = delete ``.state/``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from i2c import state
from i2c import validate as v
from i2c.migrate import CURRENT_SCHEMA_VERSION


class ImportE2EError(Exception):
    """An import precondition failed (not an e2e project, bad data, refusing to clobber)."""


# e2e → i2c value mappings.
_STATUS_MAP = {"closed": "closed", "open": "open", "superseded": "superseded"}
_PRIORITY_MAP = {
    "critical": "critical",
    "important": "high",
    "routine": "medium",
    "nice-to-have": "low",
}
_REGIMES = {"build", "refine", "explore"}


@dataclass
class ImportReport:
    """Structured outcome of an import (rendered by the CLI; ``--json``-able)."""

    applied: bool
    root: str
    files: list[str] = field(default_factory=list)
    manual_review: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    validation_ok: bool = False
    built_state: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _strip_html_comments(text: str) -> str:
    """Remove ``<!-- ... -->`` regions (drops template/example blocks)."""
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse leading ``---`` YAML frontmatter into a flat ``str→str`` dict."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
    return fm


def parse_gotchas(text: str) -> list[str]:
    """Collect ``- `` bullets under a ``Gotchas`` heading (until the next heading)."""
    out: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^#{2,4}\s+Gotchas\b", stripped):
            in_section = True
            continue
        if in_section:
            if re.match(r"^#{1,6}\s+", stripped):  # next heading ends the section
                break
            m = re.match(r"^\s*-\s+(.*)$", line)
            if m and m.group(1).strip():
                out.append(m.group(1).strip())
    return out


def _coerce_state(fm: dict[str, str]) -> str:
    """Map e2e frontmatter (state + blocked) to an i2c lifecycle state.

    e2e gates with a separate ``blocked`` flag; i2c folds that into the state
    enum. blocked-at-close ⇒ audit_boundary; blocked mid-phase ⇒
    audit_escalation; unblocked ⇒ the raw e2e state.
    """
    e2e_state = (fm.get("state") or "plan").strip().lower()
    blocked = (fm.get("blocked") or "").strip().lower()
    is_blocked = blocked in {"true", "awaiting-human-audit"}
    if is_blocked:
        return "audit_boundary" if e2e_state == "close" else "audit_escalation"
    return e2e_state


def _phase_sections(text: str) -> list[tuple[str, str, list[str]]]:
    """Return ``(id_token, title, body_lines)`` for each ``## Phase N:`` section."""
    lines = text.splitlines()
    heads: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        m = re.match(r"^##\s+Phase\s+([0-9A-Za-z.]+)\s*:\s*(.+?)\s*$", line)
        if m:
            heads.append((i, m.group(1), m.group(2)))
    sections: list[tuple[str, str, list[str]]] = []
    for idx, (line_no, id_token, title) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        sections.append((id_token, title, lines[line_no + 1 : end]))
    return sections


def _clean_title(title: str) -> str:
    """Drop a trailing status marker like '— COMPLETE' / '— In Progress'."""
    return re.sub(
        r"\s*[—-]\s*(COMPLETE|In Progress|Complete|Done)\s*$", "", title
    ).strip()


def _section_field(body: list[str], key: str) -> str | None:
    """First value of a ``Key:`` line in a section body (tolerates ** bold **)."""
    pat = re.compile(rf"^\**{re.escape(key)}:\**\s*(.+?)\s*$", re.IGNORECASE)
    for line in body:
        m = pat.match(line.strip())
        if m:
            return m.group(1).strip()
    return None


def parse_phases(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse ``## Phase N:`` sections into phase records + a manual-review list.

    Only unambiguous integer ids with a single section are emitted. Colliding
    ids and non-integer (alphanumeric) ids are reported, never guessed.
    """
    manual: list[str] = []
    sections = _phase_sections(text)

    by_id: dict[int, list[tuple[str, list[str]]]] = {}
    for id_token, title, body in sections:
        if not re.fullmatch(r"\d+", id_token):
            manual.append(
                f"phases.json: non-integer phase id {id_token!r} "
                f"('{_clean_title(title)}') — i2c schema requires integer ids; "
                "renumber or change the schema before converting."
            )
            continue
        by_id.setdefault(int(id_token), []).append((title, body))

    phases: list[dict[str, Any]] = []
    for pid in sorted(by_id):
        entries = by_id[pid]
        if len(entries) > 1:
            titles = "; ".join(f"'{_clean_title(t)}'" for t, _ in entries)
            manual.append(
                f"phases.json: id {pid} appears in {len(entries)} sections "
                f"({titles}) — resolve the collision manually."
            )
            continue
        title, body = entries[0]
        regime = (_section_field(body, "Regime") or "build").lower()
        if regime not in _REGIMES:
            regime = "build"
        status_raw = (_section_field(body, "Status") or "").lower()
        status = "complete" if status_raw.startswith("complete") else "pending"
        phases.append(
            {
                "id": pid,
                "title": _clean_title(title),
                "regime": regime,
                "dependencies": [],
                "status": status,
            }
        )

    if sections:
        manual.append(
            "phases.json: only `## Phase N:` sections are converted; modules "
            "completed before phase-numbering (see DEVPLAN 'Current Status' / "
            "ARCHITECTURE.md) have no phase records — add manually if needed. "
            "Module names and dependencies are not auto-derived."
        )
    return phases, manual


def _decision_blocks(text: str) -> list[tuple[str, list[str]]]:
    """Split decisions text into ``(id, body_lines)`` blocks keyed by ``D-N:``."""
    lines = text.splitlines()
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = re.match(r"^(D-\d+):", line.strip())
        if m:
            starts.append((i, m.group(1)))
    blocks: list[tuple[str, list[str]]] = []
    for idx, (line_no, did) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        blocks.append((did, lines[line_no:end]))
    return blocks


def _normalize_date(raw: str | None) -> str | None:
    """``YYYY-MM-DD`` → ``YYYY-MM-DDT00:00:00Z`` (date-time); else passthrough."""
    if not raw:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw.strip())
    return f"{m.group(1)}T00:00:00Z" if m else None


def parse_decisions(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse ``DECISIONS.md`` (comments stripped) into i2c decision records."""
    manual: list[str] = []
    cleaned = _strip_html_comments(text)
    records: list[dict[str, Any]] = []

    field_keys = ("Date", "Priority", "Decision", "Rationale", "Revisit if")
    key_pat = re.compile(rf"^({'|'.join(re.escape(k) for k in field_keys)}):\s*(.*)$")

    for did, body in _decision_blocks(cleaned):
        title = body[0].split(":", 1)[1].strip() if ":" in body[0] else ""
        fields: dict[str, str] = {}
        current: str | None = None
        for line in body[1:]:
            stripped = line.strip()
            # "Date: X | Status: Y" — split the combined line.
            if stripped.lower().startswith("date:") and "|" in stripped:
                date_part, _, status_part = stripped.partition("|")
                fields["Date"] = date_part.split(":", 1)[1].strip()
                if ":" in status_part:
                    fields["Status"] = status_part.split(":", 1)[1].strip()
                current = None
                continue
            if stripped.lower().startswith("status:"):
                fields["Status"] = stripped.split(":", 1)[1].strip()
                current = None
                continue
            m = key_pat.match(stripped)
            if m:
                current = m.group(1)
                fields[current] = m.group(2).strip()
            elif current and stripped:
                fields[current] = (fields[current] + " " + stripped).strip()

        status = _STATUS_MAP.get((fields.get("Status") or "").lower())
        decision_text = fields.get("Decision", "").strip()
        if status is None or not decision_text:
            manual.append(
                f"decisions.json: {did} could not be fully parsed "
                f"(status={fields.get('Status')!r}, decision present="
                f"{bool(decision_text)}) — add manually."
            )
            continue

        record: dict[str, Any] = {
            "id": did,
            "title": title or did,
            "status": status,
            "decision": decision_text,
        }
        if fields.get("Rationale"):
            record["rationale"] = fields["Rationale"].strip()
        if fields.get("Revisit if"):
            record["revisit_if"] = fields["Revisit if"].strip()
        priority = _PRIORITY_MAP.get((fields.get("Priority") or "").lower())
        if priority:
            record["priority"] = priority
        timestamp = _normalize_date(fields.get("Date"))
        if timestamp:
            record["timestamp"] = timestamp
        records.append(record)

    return records, manual


# ---------------------------------------------------------------------------
# Build + apply
# ---------------------------------------------------------------------------


def _build_project(root: Path) -> dict[str, Any]:
    devplan = (root / "DEVPLAN.md").read_text(encoding="utf-8")
    fm = parse_frontmatter(devplan)

    phase_raw = (fm.get("phase") or "0").strip()
    if not re.fullmatch(r"\d+", phase_raw):
        raise ImportE2EError(
            f"non-integer current phase {phase_raw!r} in DEVPLAN frontmatter; "
            "i2c's project schema requires an integer `phase`. Resolve the "
            "alphanumeric-phase blocker (see DESIGN_migration_v1.md) before "
            "converting this project."
        )

    project: dict[str, Any] = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "phase": int(phase_raw),
        "state": _coerce_state(fm),
        "gotchas": parse_gotchas(devplan),
    }
    return project


def _validate(name: str, data: Any) -> None:
    schema = v.load_schema(v.SCHEMA_BY_FILENAME[name])
    v.validate_json_schema(data, schema, label=name)


def import_project(
    root: Path,
    *,
    apply: bool = False,
    port_history: bool = False,
    force: bool = False,
) -> ImportReport:
    """Convert an e2e (prose-state) project at ``root``. Dry-run unless ``apply``."""
    root = Path(root)
    if not root.is_dir():
        raise ImportE2EError(f"{root} is not a directory.")

    if not (root / "DEVPLAN.md").is_file():
        raise ImportE2EError(
            f"{root} has no DEVPLAN.md — not an e2e (prose-state) project; "
            "nothing to import."
        )

    state_dir = root / ".state"
    if (state_dir / "project.json").exists() and not force:
        raise ImportE2EError(
            f".state/project.json already exists in {root}; refusing to "
            "overwrite (pass --force to re-import)."
        )

    report = ImportReport(applied=False, root=str(root))

    # Transform.
    project = _build_project(root)
    phases, phase_manual = parse_phases((root / "DEVPLAN.md").read_text("utf-8"))
    report.manual_review.extend(phase_manual)

    decisions: list[dict[str, Any]] = []
    decisions_path = root / "DECISIONS.md"
    if decisions_path.is_file():
        decisions, dec_manual = parse_decisions(decisions_path.read_text("utf-8"))
        report.manual_review.extend(dec_manual)
    else:
        report.warnings.append("DECISIONS.md not found — decisions.json is empty.")

    # budget_type follows the *current* phase's regime (the schema infers it
    # when omitted). Don't assume 'steps' — single-pass projects commonly end
    # in a Refine (time-budget) phase. Omit when the current phase isn't a
    # converted record or is Explore, and let i2c infer.
    current = next((p for p in phases if p["id"] == project["phase"]), None)
    if current is not None:
        if current["regime"] == "refine":
            project["budget_type"] = "time"
        elif current["regime"] == "build":
            project["budget_type"] = "steps"

    steps: list[dict[str, Any]] = []  # snapshot-don't-port
    if port_history:
        report.warnings.append(
            "--port-history is not yet implemented; history was snapshotted "
            "(steps.json / devlog.jsonl emitted empty)."
        )

    # Validate everything before any write.
    _validate("project.json", project)
    _validate("phases.json", phases)
    _validate("steps.json", steps)
    _validate("decisions.json", decisions)
    report.validation_ok = True

    report.built_state = {
        "project.json": project,
        "phases.json": phases,
        "steps.json": steps,
        "decisions.json": decisions,
        "devlog.jsonl": [],
    }
    report.files = [
        f"project.json: phase={project['phase']} state={project['state']} "
        f"gotchas={len(project['gotchas'])} schema_version={project['schema_version']} "
        f"budget_type={project.get('budget_type', '(inferred)')}",
        f"phases.json: {len(phases)} record(s)"
        + (f" (ids {', '.join(str(p['id']) for p in phases)})" if phases else ""),
        f"steps.json: {len(steps)} record(s) (snapshot-don't-port)",
        f"decisions.json: {len(decisions)} record(s)",
        "devlog.jsonl: empty (snapshot-don't-port)",
    ]

    report.warnings.append(
        "Snapshot-don't-port: DEVLOG.md / DEVPLAN.md / DECISIONS.md are left in "
        "place as the history archive."
    )
    report.warnings.append(
        "Remaining manual steps (out of prototype scope): de-vendor framework "
        "files (GOVERNANCE.md, WORKER_SPEC.md, tools/, run-iteration.sh, "
        ".claude/commands), rewrite CLAUDE.md/CODEX.md adapters to i2c form, add "
        "i2c.toml + the i2c package dependency."
    )

    if apply:
        state.atomic_write_json(state_dir / "project.json", project)
        state.atomic_write_json(state_dir / "phases.json", phases)
        state.atomic_write_json(state_dir / "steps.json", steps)
        state.atomic_write_json(state_dir / "decisions.json", decisions)
        (state_dir / "devlog.jsonl").write_text("", encoding="utf-8")
        # Self-check: written files validate against the registered schemas.
        for name_ in ("project.json", "phases.json", "steps.json", "decisions.json"):
            v.validate_state_file(state_dir / name_)
        v.validate_devlog_jsonl(state_dir / "devlog.jsonl")
        report.applied = True

    return report
