"""Tests for tools/control.py — the i2c.control command API."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent

from i2c import control as c  # noqa: E402
from i2c import validate as v  # noqa: E402
from tests._fixtures import copy_fixture  # noqa: E402

FIXTURE = I2C_ROOT / "examples" / "initial_state"


class TempProject:
    """Copy the fixture into a temp dir so write tests don't mutate it."""

    def __init__(self):
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.root: Path | None = None

    def __enter__(self) -> "TempProject":
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_control_")
        self.root = Path(self._tmp.name) / "project"
        copy_fixture(self.root)
        return self

    def __exit__(self, *args):
        self._tmp.cleanup()

    def patch_project(self, **kwargs) -> None:
        path = self.root / ".state" / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(kwargs)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def read_project(self) -> dict:
        path = self.root / ".state" / "project.json"
        return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


class TestStatus(unittest.TestCase):
    def test_status_reports_phase_state_module_regime(self):
        report = c.status(FIXTURE)
        self.assertEqual(report.phase, 2)
        self.assertEqual(report.state, "execute")
        self.assertEqual(report.module, "event_store")
        self.assertEqual(report.regime, "build")
        self.assertEqual(report.dependencies, [])
        self.assertEqual(report.budget, {"steps_remaining": 3})

    def test_status_current_phase_steps(self):
        report = c.status(FIXTURE)
        # Phase 2 has steps 1..4, in step order.
        self.assertEqual([s.step for s in report.steps], [1, 2, 3, 4])
        self.assertEqual(report.steps[0].title, "Append-only writer")
        self.assertEqual(report.steps[0].status, "complete")
        self.assertEqual(report.steps[0].commit, "1234567")
        self.assertEqual(report.steps[1].status, "pending")
        self.assertIsNone(report.steps[1].commit)

    def test_status_gotchas(self):
        report = c.status(FIXTURE)
        self.assertEqual(len(report.gotchas), 1)
        self.assertIn("supervised", report.gotchas[0])

    def test_status_open_decisions(self):
        report = c.status(FIXTURE)
        self.assertEqual([d.id for d in report.open_decisions], ["D-2"])
        self.assertEqual(report.open_decisions[0].status, "open")

    def test_status_recent_activity_last_three_newest_first(self):
        report = c.status(FIXTURE)
        self.assertEqual(len(report.recent_activity), 3)
        # Newest entry (phase 2, step 1) first; phase-level close has step None.
        self.assertEqual(report.recent_activity[0].phase, 2)
        self.assertEqual(report.recent_activity[0].step, 1)
        self.assertEqual(report.recent_activity[0].action, "execute")
        self.assertIsNone(report.recent_activity[1].step)


# ---------------------------------------------------------------------------
# next_action()
# ---------------------------------------------------------------------------


class TestNextAction(unittest.TestCase):
    def test_next_action_execute(self):
        dispatch = c.next_action(FIXTURE)
        self.assertEqual(dispatch.action, "EXECUTE")
        self.assertEqual(dispatch.next_state, "execute")

    def test_invalid_state_raises_control_error(self):
        with TempProject() as p:
            (p.root / ".state" / "project.json").write_text(
                '{"phase": 2, "state": "bogus"}', encoding="utf-8"
            )
            with self.assertRaises(c.ControlError):
                c.next_action(p.root)


# ---------------------------------------------------------------------------
# phase_summary()
# ---------------------------------------------------------------------------


class TestPhaseSummary(unittest.TestCase):
    def test_phase_summary_header_and_steps(self):
        summary = c.phase_summary(FIXTURE, phase=2)
        self.assertEqual(summary.phase, 2)
        self.assertEqual(summary.module, "event_store")
        self.assertEqual(summary.regime, "build")
        self.assertEqual(summary.title, "Core storage")
        self.assertEqual(summary.status, "pending")
        self.assertEqual([s.step for s in summary.steps], [1, 2, 3, 4])

    def test_phase_summary_devlog_filtered(self):
        summary = c.phase_summary(FIXTURE, phase=2)
        self.assertEqual([e.phase for e in summary.devlog], [2])
        self.assertEqual(summary.devlog[0].summary[:11], "Append-only")

    def test_phase_summary_decisions_untagged_excluded(self):
        # Fixture decisions lack the optional `phase` field, so none are
        # attributed to phase 2.
        summary = c.phase_summary(FIXTURE, phase=2)
        self.assertEqual(summary.decisions, [])
        self.assertEqual(summary.open_items, [])

    def test_phase_summary_decisions_tagged(self):
        with TempProject() as p:
            path = p.root / ".state" / "decisions.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data[1]["phase"] = 2  # D-2 (open) now tagged to phase 2
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            summary = c.phase_summary(p.root, phase=2)
            self.assertEqual([d.id for d in summary.decisions], ["D-2"])
            self.assertEqual([d.id for d in summary.open_items], ["D-2"])


# ---------------------------------------------------------------------------
# decisions()
# ---------------------------------------------------------------------------


class TestDecisions(unittest.TestCase):
    def test_decisions_all(self):
        result = c.decisions(FIXTURE)
        self.assertEqual([d.id for d in result], ["D-1", "D-2"])

    def test_decisions_filter_by_phase(self):
        result = c.decisions(FIXTURE, phase=2)
        self.assertEqual(result, [])  # none tagged in the fixture

    def test_decisions_filter_matches_tagged(self):
        with TempProject() as p:
            path = p.root / ".state" / "decisions.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data[0]["phase"] = 1
            data[1]["phase"] = 2
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            self.assertEqual([d.id for d in c.decisions(p.root, phase=1)], ["D-1"])
            self.assertEqual([d.id for d in c.decisions(p.root, phase=2)], ["D-2"])


# ---------------------------------------------------------------------------
# devlog()
# ---------------------------------------------------------------------------


class TestDevlog(unittest.TestCase):
    def test_devlog_all(self):
        result = c.devlog(FIXTURE)
        # Fixture: 3 phase-1 entries + 1 phase-2 entry, in file order.
        self.assertEqual([(e.phase, e.step) for e in result],
                         [(1, 1), (1, 2), (1, None), (2, 1)])

    def test_devlog_filter_by_phase(self):
        result = c.devlog(FIXTURE, phase=2)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].phase, 2)
        self.assertEqual(result[0].summary[:11], "Append-only")

    def test_devlog_phase_one_full_history(self):
        result = c.devlog(FIXTURE, phase=1)
        self.assertEqual([e.step for e in result], [1, 2, None])

    def test_devlog_limit_takes_last_n(self):
        result = c.devlog(FIXTURE, limit=2)
        self.assertEqual([(e.phase, e.step) for e in result], [(1, None), (2, 1)])


# ---------------------------------------------------------------------------
# escalation()
# ---------------------------------------------------------------------------


def _append_devlog(root: Path, entry: dict) -> None:
    path = root / ".state" / "devlog.jsonl"
    text = path.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + json.dumps(entry) + "\n", encoding="utf-8")


class TestEscalation(unittest.TestCase):
    def test_no_escalation_in_fixture(self):
        e = c.escalation(FIXTURE)
        self.assertEqual(e.phase, 2)
        self.assertFalse(e.is_escalated)  # fixture state is execute
        self.assertIsNone(e.entry)
        self.assertEqual(e.surrounding, [])
        self.assertEqual(e.open_decisions, [])

    def test_escalation_detected(self):
        with TempProject() as p:
            p.patch_project(state="audit_escalation")
            _append_devlog(p.root, {
                "phase": 2, "step": 2, "action": "execute", "outcome": "escalate",
                "summary": "cross-module contract break", "contracts": [],
                "timestamp": "2026-06-03T12:00:00Z",
            })
            e = c.escalation(p.root, phase=2)
            self.assertTrue(e.is_escalated)
            self.assertIsNotNone(e.entry)
            self.assertEqual(e.entry.outcome, "escalate")
            self.assertEqual(e.entry.step, 2)
            # The preceding in-phase entry (step 1) is surrounding context.
            self.assertEqual([s.step for s in e.surrounding], [1])

    def test_escalation_open_decisions_phase_tagged(self):
        with TempProject() as p:
            path = p.root / ".state" / "decisions.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data[1]["phase"] = 2  # D-2 (open) tagged to phase 2
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            e = c.escalation(p.root, phase=2)
            self.assertEqual([d.id for d in e.open_decisions], ["D-2"])


# ---------------------------------------------------------------------------
# logs() / logs_transcript()
# ---------------------------------------------------------------------------


def _write_summary(root: Path, lines: list[str]) -> None:
    log_dir = root / "logs" / "loop"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "summary.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


_LINE_1 = (
    '2026-06-25T04:03:35+00:00 | iter=1 | backend=claude | action=EXECUTE | '
    'exit=0 | tokens_in=41000 tokens_out=900 tokens_cached=38000 | '
    'reason="step 2.1 complete"'
)
_LINE_2 = (
    '2026-06-25T05:00:00+00:00 | iter=2 | backend=codex | action=REVIEW | '
    'exit=2 | reason="needs human"'
)
_LINE_3 = (
    '2026-06-25T06:00:00+00:00 | iter=3 | backend=claude | action=CLOSE | '
    'exit=0 | reason="phase closed"'
)


class TestLogs(unittest.TestCase):
    def test_logs_empty_without_summary(self):
        self.assertEqual(c.logs(FIXTURE), [])  # fixture has no logs/loop/

    def test_logs_parses_index_and_tokens(self):
        with TempProject() as p:
            _write_summary(p.root, [_LINE_1, _LINE_2])
            entries = c.logs(p.root)
            self.assertEqual([e.iter for e in entries], [1, 2])
            self.assertEqual(entries[0].backend, "claude")
            self.assertEqual(entries[0].action, "EXECUTE")
            self.assertEqual(entries[0].exit_code, 0)
            self.assertEqual(entries[0].reason, "step 2.1 complete")
            self.assertEqual(
                entries[0].tokens, {"input": 41000, "output": 900, "cached": 38000}
            )
            self.assertIsNone(entries[1].tokens)  # no token segment
            self.assertIsNone(entries[0].transcript)  # index mode

    def test_logs_limit_keeps_last_n(self):
        with TempProject() as p:
            _write_summary(p.root, [_LINE_1, _LINE_2, _LINE_3])
            self.assertEqual([e.iter for e in c.logs(p.root, limit=2)], [2, 3])
            self.assertEqual([e.iter for e in c.logs(p.root, limit=None)], [1, 2, 3])

    def test_logs_transcript_reads_file(self):
        with TempProject() as p:
            _write_summary(p.root, [_LINE_1])
            (p.root / "logs" / "loop" / "iteration_001.txt").write_text(
                "worker transcript body", encoding="utf-8"
            )
            rec = c.logs_transcript(p.root, iter=1)
            self.assertEqual(rec.iter, 1)
            self.assertEqual(rec.transcript, "worker transcript body")

    def test_logs_transcript_missing_file_is_none(self):
        with TempProject() as p:
            _write_summary(p.root, [_LINE_1])
            rec = c.logs_transcript(p.root, iter=1)
            self.assertIsNone(rec.transcript)

    def test_logs_transcript_unknown_iter_raises(self):
        with TempProject() as p:
            _write_summary(p.root, [_LINE_1])
            with self.assertRaises(c.NotFoundError):
                c.logs_transcript(p.root, iter=99)


# ---------------------------------------------------------------------------
# portfolio() + discover_projects()
# ---------------------------------------------------------------------------


class TestPortfolio(unittest.TestCase):
    @staticmethod
    def _make(root: Path, specs: dict) -> None:
        """Create one fixture-copy project per spec entry, applying
        project.json overrides."""
        for name, overrides in specs.items():
            dst = root / name
            copy_fixture(dst)
            if overrides:
                pj = dst / ".state" / "project.json"
                data = json.loads(pj.read_text(encoding="utf-8"))
                data.update(overrides)
                pj.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def test_discovers_all_projects(self):
        with tempfile.TemporaryDirectory(prefix="i2c_pf_") as tmp:
            root = Path(tmp)
            self._make(root, {"a": {}, "b": {}, "c": {}})
            self.assertEqual(
                [p.name for p in c.discover_projects(root)], ["a", "b", "c"]
            )

    def test_skips_noise_and_does_not_descend(self):
        with tempfile.TemporaryDirectory(prefix="i2c_pf_") as tmp:
            root = Path(tmp)
            self._make(root, {"a": {}})
            (root / "node_modules").mkdir()
            (root / ".hidden").mkdir()
            # A nested project inside a/ must not be discovered separately.
            copy_fixture(root / "a" / "nested")
            self.assertEqual([p.name for p in c.discover_projects(root)], ["a"])

    def test_orders_attention_first(self):
        with tempfile.TemporaryDirectory(prefix="i2c_pf_") as tmp:
            root = Path(tmp)
            self._make(root, {
                "calm": {},  # execute
                "boundary": {"state": "audit_boundary"},
                "stuck": {"state": "audit_escalation"},
                "finished": {"state": "done"},
            })
            _append_devlog(root / "stuck", {
                "phase": 2, "step": 2, "action": "execute", "outcome": "escalate",
                "summary": "blocked on upstream API", "contracts": [],
                "timestamp": "2026-06-03T12:00:00Z",
            })
            report = c.portfolio(root)
            order = [p.name for p in report.projects]
            self.assertEqual(order[0], "stuck")      # escalation first
            self.assertEqual(order[1], "boundary")   # then boundary
            self.assertEqual(order[-1], "finished")  # done last
            stuck = report.projects[0]
            self.assertTrue(stuck.is_escalated)
            self.assertEqual(stuck.escalation_reason, "blocked on upstream API")
            self.assertEqual(stuck.next_action, "EXIT")

    def test_captures_load_error_and_floats_it(self):
        with tempfile.TemporaryDirectory(prefix="i2c_pf_") as tmp:
            root = Path(tmp)
            self._make(root, {"ok": {}, "broken": {}})
            (root / "broken" / ".state" / "project.json").write_text(
                '{"phase": 1, "state": "BOGUS"}', encoding="utf-8"
            )
            report = c.portfolio(root)
            broken = next(p for p in report.projects if p.name == "broken")
            self.assertIsNotNone(broken.error)
            self.assertEqual(report.projects[0].name, "broken")  # floats to top

    def test_empty_root(self):
        with tempfile.TemporaryDirectory(prefix="i2c_pf_") as tmp:
            self.assertEqual(c.portfolio(Path(tmp)).projects, [])


# ---------------------------------------------------------------------------
# clear_boundary()
# ---------------------------------------------------------------------------


class TestClearBoundary(unittest.TestCase):
    def test_advance_writes_next_phase_plan(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary", phase=2)
            result = c.clear_boundary(p.root, advance=True)
            self.assertEqual(result.outcome, "advanced")
            self.assertEqual(result.phase, 3)
            self.assertEqual(result.state, "plan")
            on_disk = p.read_project()
            self.assertEqual(on_disk["phase"], 3)
            self.assertEqual(on_disk["state"], "plan")

    def test_terminate_writes_done(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary", phase=2)
            result = c.clear_boundary(p.root, advance=False)
            self.assertEqual(result.outcome, "terminated")
            self.assertEqual(result.phase, 2)
            self.assertEqual(result.state, "done")
            on_disk = p.read_project()
            self.assertEqual(on_disk["phase"], 2)
            self.assertEqual(on_disk["state"], "done")

    def test_non_boundary_raises_invalid_state(self):
        with TempProject() as p:
            # Fixture is state=execute.
            with self.assertRaises(c.InvalidStateError):
                c.clear_boundary(p.root, advance=True)

    def test_result_revalidates_against_schema(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary", phase=2)
            c.clear_boundary(p.root, advance=True)
            # Should not raise — the written file is schema-valid.
            v.validate_state_file(p.root / ".state" / "project.json")


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrors(unittest.TestCase):
    def test_missing_state_raises_control_error_not_systemexit(self):
        with tempfile.TemporaryDirectory(prefix="i2c_control_empty_") as tmp:
            root = Path(tmp)  # no .state/ here
            with self.assertRaises(c.ControlError):
                c.status(root)

    def test_find_project_root_raises_not_found(self):
        with tempfile.TemporaryDirectory(prefix="i2c_control_noroot_") as tmp:
            with self.assertRaises(c.NotFoundError):
                c.find_project_root(Path(tmp))


# ---------------------------------------------------------------------------
# diagnose() — deterministic-first recovery diagnosis
# ---------------------------------------------------------------------------


def _write_steps(p: "TempProject", steps: list) -> None:
    (p.root / ".state" / "steps.json").write_text(
        json.dumps(steps, indent=2) + "\n", encoding="utf-8"
    )


def _seed_summary(p: "TempProject", line: str) -> None:
    log_dir = p.root / "logs" / "loop"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "summary.log").write_text(line + "\n", encoding="utf-8")


class TestDiagnose(unittest.TestCase):
    def test_clean_fixture_is_none(self):
        # Use an out-of-repo temp copy: the committed fixture lives inside the
        # i2c git repo, where audit_git would (correctly) report real drift.
        with TempProject() as p:
            d = c.diagnose(p.root)
            self.assertEqual(d.classification, "none")
            self.assertFalse(d.reconcilable)
            self.assertEqual(d.findings, [])
            self.assertIsNone(d.target)

    def test_workflow_drift_when_state_not_advanced(self):
        with TempProject() as p:
            steps = json.loads(
                (p.root / ".state" / "steps.json").read_text(encoding="utf-8")
            )
            for s in steps:
                if s["phase"] == 2:
                    s["status"] = "complete"
                    s.setdefault("commit", "abc1234")
            _write_steps(p, steps)
            d = c.diagnose(p.root)
            self.assertEqual(d.classification, "workflow-drift")
            self.assertTrue(d.reconcilable)
            self.assertTrue(
                any(f.signal == "execute_state_not_advanced" for f in d.findings)
            )
            self.assertTrue(any(f.proposal for f in d.findings))

    def test_malformed_signal_detected_from_log(self):
        with TempProject() as p:
            _seed_summary(
                p,
                '2026-06-29T00:00:00+00:00 | iter=7 | backend=codex | '
                'action=EXECUTE | exit=2 | reason="exit signal missing or '
                'malformed (2-line block not found in worker output)"',
            )
            d = c.diagnose(p.root)
            self.assertEqual(d.target, 7)
            self.assertTrue(d.malformed_signal)
            self.assertEqual(d.exit_code, 2)
            # No drift in .state → not attributable to drift → unknown.
            self.assertEqual(d.classification, "unknown")

    def test_explicit_missing_target_raises(self):
        with TempProject() as p:
            _seed_summary(
                p,
                '2026-06-29T00:00:00+00:00 | iter=1 | backend=claude | '
                'action=EXECUTE | exit=0 | reason="ok"',
            )
            with self.assertRaises(c.NotFoundError):
                c.diagnose(p.root, target=99)

    def test_drift_wins_over_failed_target(self):
        # Drift present AND the target iteration failed: classification must be
        # workflow-drift (drift is the class recovery owns), not unknown.
        with TempProject() as p:
            _complete_phase_steps(p, 2)  # execute_state_not_advanced drift
            _seed_summary(
                p,
                '2026-06-29T00:00:00+00:00 | iter=3 | backend=codex | '
                'action=EXECUTE | exit=2 | reason="boom"',
            )
            d = c.diagnose(p.root)
            self.assertEqual(d.classification, "workflow-drift")
            self.assertEqual(d.target, 3)
            self.assertEqual(d.exit_code, 2)

    def test_diagnose_writes_nothing(self):
        with TempProject() as p:
            before = (p.root / ".state" / "project.json").read_text(encoding="utf-8")
            c.diagnose(p.root)
            after = (p.root / ".state" / "project.json").read_text(encoding="utf-8")
            self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# reconcile() — human-gated remediation
# ---------------------------------------------------------------------------


def _complete_phase_steps(p: "TempProject", phase: int) -> None:
    steps = json.loads(
        (p.root / ".state" / "steps.json").read_text(encoding="utf-8")
    )
    for s in steps:
        if s["phase"] == phase:
            s["status"] = "complete"
            s.setdefault("commit", "abc1234")
    _write_steps(p, steps)


def _drop_commit(p: "TempProject", phase: int, step: int) -> None:
    steps = json.loads(
        (p.root / ".state" / "steps.json").read_text(encoding="utf-8")
    )
    for s in steps:
        if s["phase"] == phase and s["step"] == step:
            s.pop("commit", None)
    _write_steps(p, steps)


class TestReconcile(unittest.TestCase):
    def test_dry_run_proposes_without_writing(self):
        with TempProject() as p:
            _complete_phase_steps(p, 2)
            report = c.reconcile(p.root)  # apply defaults False
            self.assertFalse(report.applied)
            self.assertEqual(len(report.items), 1)
            self.assertEqual(report.items[0].signal, "execute_state_not_advanced")
            self.assertFalse(report.items[0].applied)
            # State unchanged.
            self.assertEqual(p.read_project()["state"], "execute")

    def test_apply_writes_via_state_path(self):
        with TempProject() as p:
            _complete_phase_steps(p, 2)
            report = c.reconcile(p.root, apply=True)
            self.assertTrue(report.applied)
            self.assertTrue(report.items[0].applied)
            self.assertEqual(p.read_project()["state"], "review")
            # The written file is schema-valid (went through state.py).
            v.validate_state_file(p.root / ".state" / "project.json")

    def test_judgment_findings_are_skipped_not_applied(self):
        with TempProject() as p:
            _drop_commit(p, 2, 1)  # complete step missing commit = judgment-class
            report = c.reconcile(p.root, apply=True)
            self.assertEqual(report.items, [])
            self.assertTrue(
                any(s.signal == "step_complete_without_commit" for s in report.skipped)
            )

    def test_conflicting_combo_applies_single_boundary(self):
        # state=execute + all steps complete + phase record complete: only the
        # close-gate fix should apply (set audit_boundary), not also state=review.
        with TempProject() as p:
            _complete_phase_steps(p, 2)
            phases = json.loads(
                (p.root / ".state" / "phases.json").read_text(encoding="utf-8")
            )
            for ph in phases:
                if ph["id"] == 2:
                    ph["status"] = "complete"
            (p.root / ".state" / "phases.json").write_text(
                json.dumps(phases, indent=2) + "\n", encoding="utf-8"
            )
            report = c.reconcile(p.root, apply=True)
            self.assertEqual(
                [i.signal for i in report.items], ["close_gate_not_set"]
            )
            self.assertEqual(p.read_project()["state"], "audit_boundary")


class TestFollowups(unittest.TestCase):
    """followups() — the refine backlog projection (Proposal A step 2)."""

    _FUS = [
        {"id": "FU-1", "title": "prose pass", "kind": "prose", "status": "open"},
        {"id": "FU-2", "title": "dead code", "kind": "dead-surface",
         "status": "closed", "resolution": "removed", "refs": ["D-1"],
         "files": ["a.py"]},
        {"id": "FU-3", "title": "run 21 finding", "kind": "experiment-log",
         "status": "open"},
    ]

    def _write(self, root: Path, records) -> None:
        (root / ".state" / "followups.json").write_text(
            json.dumps(records), encoding="utf-8"
        )

    def test_empty_when_absent(self):
        with TempProject() as t:
            self.assertEqual(c.followups(t.root), [])

    def test_returns_all_as_views(self):
        with TempProject() as t:
            self._write(t.root, self._FUS)
            result = c.followups(t.root)
            self.assertEqual([f.id for f in result], ["FU-1", "FU-2", "FU-3"])
            self.assertIsInstance(result[0], c.FollowupView)

    def test_filter_by_status(self):
        with TempProject() as t:
            self._write(t.root, self._FUS)
            result = c.followups(t.root, status="open")
            self.assertEqual([f.id for f in result], ["FU-1", "FU-3"])

    def test_filter_by_kind(self):
        with TempProject() as t:
            self._write(t.root, self._FUS)
            result = c.followups(t.root, kind="experiment-log")
            self.assertEqual([f.id for f in result], ["FU-3"])

    def test_filter_by_priority(self):
        with TempProject() as t:
            self._write(t.root, [
                {"id": "FU-1", "title": "a", "kind": "prose", "status": "open",
                 "priority": "next"},
                {"id": "FU-2", "title": "b", "kind": "other", "status": "open",
                 "priority": "icebox"},
            ])
            result = c.followups(t.root, priority="next")
            self.assertEqual([f.id for f in result], ["FU-1"])
            self.assertEqual(result[0].priority, "next")

    def test_full_fields_mapped(self):
        with TempProject() as t:
            self._write(t.root, self._FUS)
            fu2 = next(f for f in c.followups(t.root) if f.id == "FU-2")
            self.assertEqual(fu2.resolution, "removed")
            self.assertEqual(fu2.refs, ["D-1"])
            self.assertEqual(fu2.files, ["a.py"])

    def test_invalid_backlog_raises(self):
        with TempProject() as t:
            self._write(
                t.root,
                [{"id": "FU-1", "title": "x", "kind": "bogus", "status": "open"}],
            )
            with self.assertRaises(c.ControlError):
                c.followups(t.root)

    def test_works_without_full_project(self):
        # D-refine-3: a repo can carry a backlog without the phase-lifecycle files.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".state").mkdir()
            (root / ".state" / "followups.json").write_text(
                json.dumps(self._FUS), encoding="utf-8"
            )
            result = c.followups(root)
            self.assertEqual(len(result), 3)


# ---------------------------------------------------------------------------
# dashboard_model()
# ---------------------------------------------------------------------------


class TestDashboardModel(unittest.TestCase):
    @staticmethod
    def _make_portfolio(root: Path, names: list[str]) -> None:
        for name in names:
            copy_fixture(root / name)

    def test_project_mode_against_fixture(self):
        model = c.dashboard_model(FIXTURE)
        self.assertEqual(model.mode, "project")
        self.assertIsNone(model.portfolio)
        self.assertIsNotNone(model.project)
        self.assertEqual(model.project.phase, 2)
        self.assertEqual(model.project.state, "execute")
        # run_config present (dict with the four [run] keys), health is a list.
        self.assertIsInstance(model.run_config, dict)
        self.assertIn("backend", model.run_config)
        self.assertIn("backends", model.run_config)
        self.assertIsInstance(model.health, list)
        self.assertTrue(model.health)  # doctor always yields checks

    def test_portfolio_mode_with_root(self):
        with tempfile.TemporaryDirectory(prefix="i2c_dash_") as tmp:
            root = Path(tmp)
            self._make_portfolio(root, ["one", "two"])
            model = c.dashboard_model(root)
            self.assertEqual(model.mode, "portfolio")
            self.assertIsNone(model.project)
            self.assertIsNotNone(model.portfolio)
            names = {p.name for p in model.portfolio.projects}
            self.assertEqual(names, {"one", "two"})

    def test_force_portfolio_override(self):
        # portfolio=True forces portfolio mode even against a single project dir.
        model = c.dashboard_model(FIXTURE, portfolio=True)
        self.assertEqual(model.mode, "portfolio")
        self.assertIsNotNone(model.portfolio)

    def test_allowlist_excludes_telegram_admins(self):
        # D-dash-3: an i2c.toml [telegram] block must never leak into the model.
        with tempfile.TemporaryDirectory(prefix="i2c_dash_") as tmp:
            root = Path(tmp) / "project"
            copy_fixture(root)
            (root / "i2c.toml").write_text(
                "[run]\nbackend = \"claude\"\n\n"
                "[telegram]\nadmins = [111222333, 444555666]\n"
                "root = \"/secret/portfolio\"\n",
                encoding="utf-8",
            )
            from dataclasses import asdict

            model = c.dashboard_model(root)
            blob = json.dumps(asdict(model))
            self.assertNotIn("111222333", blob)
            self.assertNotIn("444555666", blob)
            self.assertNotIn("telegram", blob.lower())
            self.assertNotIn("/secret/portfolio", blob)
            # But the allowlisted [run] value did come through.
            self.assertEqual(model.run_config["backend"], "claude")


if __name__ == "__main__":
    unittest.main()
