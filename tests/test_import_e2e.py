"""Tests for i2c/import_e2e.py — `i2c import` (e2e prose-state converter)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from i2c import import_e2e
from i2c import validate as v

# A minimal but realistic e2e prose-state DEVPLAN: frontmatter, a Gotchas
# section, two clean phases, and a deliberate id collision (two `## Phase 2:`).
DEVPLAN = """\
---
phase: 1
blocked: false
state: plan
steps_remaining: 0
---

# Demo — Dev Plan

## Cold Start
Active module: widget.

### Gotchas
- **Tests:** run from the repo root.
- **Venv:** use the project venv, not system Python.

### Key Context
- something unrelated

## Phase 1: Foundations
**Status:** Complete
**Regime:** Build

Some prose.

## Phase 2: Core engine
**Status:** Complete
**Regime:** Build

## Phase 2: Duplicate that collides
**Status:** Complete
**Regime:** Build
"""

DECISIONS = """\
# Demo — Decision Log

<!-- Example entry:
D-1: [Decision Title]
Date: 2026-04-01 | Status: Open | Closed
Priority: Critical | Important | Nice-to-have
Decision: [What was chosen]
-->

D-1: Use an injected client protocol
Date: 2026-05-16 | Status: Closed
Priority: Important
Decision: Accept an injected client instead of importing the concrete module.
Rationale: Keeps the leaf module independent.
Revisit if: Multiple modules need a shared formal protocol type.

D-2: Categories stay hardcoded
Date: 2026-06-07 | Status: Closed
Priority: Routine
Decision: Hardcode the category tuple for now.
Rationale: Two consumers with identical needs; parameterizing is premature.
"""


class TempDir:
    def __init__(self):
        self._tmp: tempfile.TemporaryDirectory | None = None

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_import_")
        return Path(self._tmp.name)

    def __exit__(self, *args):
        self._tmp.cleanup()


def _make_e2e_project(root: Path) -> None:
    (root / "DEVPLAN.md").write_text(DEVPLAN, encoding="utf-8")
    (root / "DECISIONS.md").write_text(DECISIONS, encoding="utf-8")
    tools = root / "tools"
    tools.mkdir()
    (tools / "state_machine.sh").write_text("# bash state machine\n", encoding="utf-8")


class TestParsers(unittest.TestCase):
    def test_frontmatter_and_gotchas(self):
        fm = import_e2e.parse_frontmatter(DEVPLAN)
        self.assertEqual(fm["phase"], "1")
        self.assertEqual(fm["state"], "plan")
        gotchas = import_e2e.parse_gotchas(DEVPLAN)
        self.assertEqual(len(gotchas), 2)
        self.assertTrue(gotchas[0].startswith("**Tests:**"))

    def test_phases_collision_reported(self):
        phases, manual = import_e2e.parse_phases(DEVPLAN)
        ids = sorted(p["id"] for p in phases)
        self.assertEqual(ids, [1])  # id 2 collides → excluded
        self.assertTrue(any("id 2 appears" in m for m in manual))

    def test_decisions_mapping(self):
        records, manual = import_e2e.parse_decisions(DECISIONS)
        self.assertEqual(manual, [])
        self.assertEqual([r["id"] for r in records], ["D-1", "D-2"])
        self.assertEqual(records[0]["status"], "closed")
        self.assertEqual(records[0]["priority"], "high")  # Important → high
        self.assertEqual(records[1]["priority"], "medium")  # Routine → medium
        self.assertEqual(records[0]["timestamp"], "2026-05-16T00:00:00Z")
        self.assertNotIn("D-1: [Decision Title]", str(records))  # example skipped


class TestImportProject(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with TempDir() as root:
            _make_e2e_project(root)
            report = import_e2e.import_project(root, apply=False)
            self.assertFalse(report.applied)
            self.assertTrue(report.validation_ok)
            self.assertFalse((root / ".state").exists())

    def test_apply_writes_valid_state(self):
        with TempDir() as root:
            _make_e2e_project(root)
            report = import_e2e.import_project(root, apply=True)
            self.assertTrue(report.applied)
            project = v.validate_state_file(root / ".state" / "project.json")
            self.assertEqual(project["phase"], 1)
            self.assertEqual(project["state"], "plan")
            self.assertEqual(project["schema_version"], 1)
            self.assertEqual(project["budget_type"], "steps")  # phase 1 is Build
            self.assertEqual(len(project["gotchas"]), 2)
            self.assertNotIn("blocked", project)  # e2e field dropped
            self.assertNotIn("steps_remaining", project)
            # Arrays valid; history snapshot-not-ported.
            self.assertEqual(v.validate_state_file(root / ".state" / "steps.json"), [])
            decisions = v.validate_state_file(root / ".state" / "decisions.json")
            self.assertEqual(len(decisions), 2)
            self.assertEqual(
                v.validate_devlog_jsonl(root / ".state" / "devlog.jsonl"), []
            )

    def test_blocked_close_maps_to_audit_boundary(self):
        with TempDir() as root:
            _make_e2e_project(root)
            (root / "DEVPLAN.md").write_text(
                DEVPLAN.replace("blocked: false", "blocked: true").replace(
                    "state: plan", "state: close"
                ),
                encoding="utf-8",
            )
            report = import_e2e.import_project(root, apply=True)
            project = v.validate_state_file(root / ".state" / "project.json")
            self.assertEqual(project["state"], "audit_boundary")
            self.assertTrue(report.applied)

    def test_refuses_non_e2e(self):
        with TempDir() as root:
            # No DEVPLAN.md → not an e2e prose-state project.
            with self.assertRaises(import_e2e.ImportE2EError):
                import_e2e.import_project(root, apply=True)

    def test_refine_current_phase_sets_time_budget(self):
        with TempDir() as root:
            _make_e2e_project(root)
            (root / "DEVPLAN.md").write_text(
                DEVPLAN.replace(
                    "## Phase 1: Foundations\n**Status:** Complete\n**Regime:** Build",
                    "## Phase 1: Foundations\n**Status:** Complete\n**Regime:** Refine",
                ),
                encoding="utf-8",
            )
            import_e2e.import_project(root, apply=True)
            project = v.validate_state_file(root / ".state" / "project.json")
            self.assertEqual(project["budget_type"], "time")

    def test_refuses_non_integer_phase(self):
        with TempDir() as root:
            _make_e2e_project(root)
            (root / "DEVPLAN.md").write_text(
                DEVPLAN.replace("phase: 1", "phase: MVP.4d"), encoding="utf-8"
            )
            with self.assertRaises(import_e2e.ImportE2EError):
                import_e2e.import_project(root, apply=False)

    def test_overwrite_guard(self):
        with TempDir() as root:
            _make_e2e_project(root)
            import_e2e.import_project(root, apply=True)
            with self.assertRaises(import_e2e.ImportE2EError):
                import_e2e.import_project(root, apply=True)
            # --force re-imports cleanly.
            report = import_e2e.import_project(root, apply=True, force=True)
            self.assertTrue(report.applied)


if __name__ == "__main__":
    unittest.main()
