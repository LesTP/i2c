"""Tests for tools/validate.py — schema loading and validation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from i2c import validate as v


class TestLoadSchema(unittest.TestCase):
    def test_loads_project_schema(self) -> None:
        schema = v.load_schema("project.schema.json")
        self.assertEqual(schema["title"], "Project State")
        self.assertIn("phase", schema["properties"])

    def test_missing_schema_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "Schema not found"):
            v.load_schema("nonexistent.schema.json")


class TestValidateJsonSchema(unittest.TestCase):
    def setUp(self) -> None:
        self.project_schema = v.load_schema("project.schema.json")

    def test_valid_project_state(self) -> None:
        data = {"phase": 1, "state": "execute"}
        v.validate_json_schema(data, self.project_schema)  # does not raise

    def test_invalid_state_enum(self) -> None:
        data = {"phase": 1, "state": "bogus"}
        with self.assertRaisesRegex(ValueError, "state"):
            v.validate_json_schema(data, self.project_schema)

    def test_missing_required_field(self) -> None:
        data = {"phase": 1}  # missing state
        with self.assertRaisesRegex(ValueError, "state"):
            v.validate_json_schema(data, self.project_schema)

    def test_unknown_field_rejected(self) -> None:
        data = {"phase": 1, "state": "execute", "typo_key": "x"}
        with self.assertRaisesRegex(ValueError, "typo_key|additional"):
            v.validate_json_schema(data, self.project_schema)

    def test_blocked_field_rejected(self) -> None:
        # 'blocked' was dropped per DESIGN_state_lifecycle_v1; schema must reject it.
        data = {"phase": 1, "state": "audit_boundary", "blocked": True}
        with self.assertRaisesRegex(ValueError, "blocked|additional"):
            v.validate_json_schema(data, self.project_schema)

    def test_lifecycle_states_accepted(self) -> None:
        # All seven enum values must validate.
        for state in ("plan", "execute", "review", "close",
                       "audit_boundary", "audit_escalation", "done"):
            v.validate_json_schema({"phase": 1, "state": state}, self.project_schema)

    def test_label_in_error(self) -> None:
        data = {"phase": 1, "state": "bogus"}
        with self.assertRaisesRegex(ValueError, "myfile.json"):
            v.validate_json_schema(data, self.project_schema, label="myfile.json")


class TestValidateStateFile(unittest.TestCase):
    def test_validates_known_file(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            path.write_text(json.dumps({"phase": 2, "state": "review"}))
            data = v.validate_state_file(path)
            self.assertEqual(data["phase"], 2)

    def test_unknown_filename_rejected(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "random.json"
            path.write_text("{}")
            with self.assertRaisesRegex(ValueError, "No schema registered"):
                v.validate_state_file(path)

    def test_invalid_json_rejected(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            path.write_text("{not json")
            with self.assertRaisesRegex(ValueError, "not valid JSON"):
                v.validate_state_file(path)


class TestValidateDevlogJsonl(unittest.TestCase):
    def test_validates_well_formed_jsonl(self) -> None:
        import tempfile

        entry = {
            "phase": 1,
            "step": 1,
            "action": "execute",
            "outcome": "complete",
            "summary": "wired the loop",
            "timestamp": "2026-06-04T04:00:00Z",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "devlog.jsonl"
            path.write_text(json.dumps(entry) + "\n" + json.dumps(entry) + "\n")
            entries = v.validate_devlog_jsonl(path)
            self.assertEqual(len(entries), 2)

    def test_rejects_bad_line_with_lineno(self) -> None:
        import tempfile

        good = {
            "phase": 1,
            "step": 1,
            "action": "execute",
            "outcome": "complete",
            "summary": "ok",
            "timestamp": "2026-06-04T04:00:00Z",
        }
        bad = {"phase": 1}  # missing required fields
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "devlog.jsonl"
            path.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n")
            with self.assertRaisesRegex(ValueError, ":2"):
                v.validate_devlog_jsonl(path)


# ---------------------------------------------------------------------------
# Schema additions: regime, dependencies, budget, devlog action enum
# ---------------------------------------------------------------------------


class TestPhasesSchemaRegimeAndDependencies(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = v.load_schema("phases.schema.json")

    def _phase(self, **overrides):
        base = {
            "id": 1, "title": "P1", "regime": "build",
            "dependencies": [], "status": "pending",
        }
        base.update(overrides)
        return [base]

    def test_valid_build_leaf(self):
        v.validate_json_schema(self._phase(), self.schema)

    def test_valid_refine(self):
        v.validate_json_schema(
            self._phase(regime="refine"), self.schema,
        )

    def test_valid_explore(self):
        v.validate_json_schema(
            self._phase(regime="explore"), self.schema,
        )

    def test_invalid_regime(self):
        with self.assertRaisesRegex(ValueError, "regime"):
            v.validate_json_schema(
                self._phase(regime="fix"), self.schema,
            )

    def test_missing_regime(self):
        bad = [{"id": 1, "title": "P1", "dependencies": [], "status": "pending"}]
        with self.assertRaisesRegex(ValueError, "regime"):
            v.validate_json_schema(bad, self.schema)

    def test_dependencies_non_leaf(self):
        v.validate_json_schema(
            self._phase(dependencies=["event_store", "json_rpc"]),
            self.schema,
        )

    def test_dependencies_empty_string_rejected(self):
        with self.assertRaisesRegex(ValueError, ""):
            v.validate_json_schema(
                self._phase(dependencies=[""]),
                self.schema,
            )


class TestProjectSchemaBudgetFields(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = v.load_schema("project.schema.json")

    def _project(self, **overrides):
        base = {"phase": 1, "state": "execute"}
        base.update(overrides)
        return base

    def test_budget_type_steps(self):
        v.validate_json_schema(
            self._project(budget_type="steps"), self.schema,
        )

    def test_budget_type_time(self):
        v.validate_json_schema(
            self._project(
                budget_type="time",
                time_budget_seconds=3600,
                time_started_at="2026-06-04T04:00:00Z",
            ),
            self.schema,
        )

    def test_invalid_budget_type(self):
        with self.assertRaisesRegex(ValueError, "budget_type"):
            v.validate_json_schema(
                self._project(budget_type="hours"), self.schema,
            )

    def test_negative_time_budget(self):
        with self.assertRaisesRegex(ValueError, "time_budget_seconds"):
            v.validate_json_schema(
                self._project(time_budget_seconds=-1), self.schema,
            )

    def test_budget_fields_all_optional(self):
        # Pure step-mode project doesn't need to set budget fields.
        v.validate_json_schema(self._project(), self.schema)

    def test_schema_version_accepted(self):
        v.validate_json_schema(self._project(schema_version=1), self.schema)

    def test_schema_version_optional(self):
        # Unversioned (legacy) project still validates.
        v.validate_json_schema(self._project(), self.schema)

    def test_schema_version_below_minimum_rejected(self):
        # 0 is the in-code "legacy" sentinel; it must not appear on disk.
        with self.assertRaisesRegex(ValueError, "schema_version|minimum"):
            v.validate_json_schema(self._project(schema_version=0), self.schema)


class TestDecisionsPhaseField(unittest.TestCase):
    """Δ1: optional `phase` field on decisions.schema.json."""

    def setUp(self) -> None:
        self.schema = v.load_schema("decisions.schema.json")

    def _decision(self, **overrides):
        base = {
            "id": "D-1",
            "title": "T",
            "status": "closed",
            "decision": "d",
        }
        base.update(overrides)
        return [base]

    def test_phase_field_accepted(self):
        v.validate_json_schema(self._decision(phase=4), self.schema)

    def test_phase_field_optional(self):
        # Existing records without phase still validate (back-compat).
        v.validate_json_schema(self._decision(), self.schema)

    def test_phase_must_be_integer(self):
        with self.assertRaisesRegex(ValueError, "phase"):
            v.validate_json_schema(self._decision(phase="4"), self.schema)

    def test_phase_must_be_positive(self):
        with self.assertRaisesRegex(ValueError, "phase|minimum"):
            v.validate_json_schema(self._decision(phase=0), self.schema)


class TestDevlogActionEnumExtensions(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = v.load_schema("devlog_entry.schema.json")

    def _entry(self, **overrides):
        base = {
            "phase": 1, "step": 1,
            "action": "execute", "outcome": "complete",
            "summary": "ok", "timestamp": "2026-06-04T04:00:00Z",
        }
        base.update(overrides)
        return base

    def test_probe_action(self):
        v.validate_json_schema(
            self._entry(action="probe", step=None),
            self.schema,
        )

    def test_integration_check_action(self):
        v.validate_json_schema(
            self._entry(action="integration_check", step=None),
            self.schema,
        )

    def test_unknown_action_rejected(self):
        with self.assertRaisesRegex(ValueError, "action"):
            v.validate_json_schema(
                self._entry(action="cleanup"),
                self.schema,
            )


class TestFollowupsSchema(unittest.TestCase):
    """Proposal A: followups.schema.json — the refine backlog."""

    def setUp(self) -> None:
        self.schema = v.load_schema("followups.schema.json")

    def _fu(self, **overrides):
        base = {
            "id": "FU-41",
            "title": "integrate D-refine-7/8",
            "kind": "doc-reconciliation",
            "status": "open",
        }
        base.update(overrides)
        return [base]

    def test_minimal_valid(self):
        v.validate_json_schema(self._fu(), self.schema)

    def test_full_record_valid(self):
        v.validate_json_schema(
            self._fu(
                context="c", trigger="t", resolution="r",
                refs=["D-refine-8", "9d39390"], files=["a.py"],
                opened="2026-07-02", closed="2026-07-02",
            ),
            self.schema,
        )

    def test_experiment_log_kind_accepted(self):
        # The kind added from the diplomat validation must validate.
        v.validate_json_schema(self._fu(kind="experiment-log"), self.schema)

    def test_all_kinds_accepted(self):
        for kind in ("prose", "dead-surface", "doc-reconciliation",
                     "cli-ergonomics", "test-hardening", "structural-refactor",
                     "experiment-log", "other"):
            v.validate_json_schema(self._fu(kind=kind), self.schema)

    def test_invalid_kind_rejected(self):
        with self.assertRaisesRegex(ValueError, "kind"):
            v.validate_json_schema(self._fu(kind="bugfix"), self.schema)

    def test_all_statuses_accepted(self):
        for status in ("open", "accepted", "partially-closed", "closed",
                       "wontfix"):
            v.validate_json_schema(self._fu(status=status), self.schema)

    def test_invalid_status_rejected(self):
        with self.assertRaisesRegex(ValueError, "status"):
            v.validate_json_schema(self._fu(status="done"), self.schema)

    def test_priority_accepted(self):
        for p in ("now", "next", "eventually", "icebox"):
            v.validate_json_schema(self._fu(priority=p), self.schema)

    def test_invalid_priority_rejected(self):
        with self.assertRaisesRegex(ValueError, "priority"):
            v.validate_json_schema(self._fu(priority="high"), self.schema)

    def test_priority_optional(self):
        v.validate_json_schema(self._fu(), self.schema)  # absent priority is fine

    def test_id_pattern_enforced(self):
        with self.assertRaisesRegex(ValueError, "id|pattern"):
            v.validate_json_schema(self._fu(id="41"), self.schema)

    def test_missing_required_rejected(self):
        bad = [{"id": "FU-1", "title": "x", "status": "open"}]  # no kind
        with self.assertRaisesRegex(ValueError, "kind"):
            v.validate_json_schema(bad, self.schema)

    def test_unknown_field_rejected(self):
        with self.assertRaisesRegex(ValueError, "typo|additional"):
            v.validate_json_schema(self._fu(typo="x"), self.schema)


if __name__ == "__main__":
    unittest.main()
