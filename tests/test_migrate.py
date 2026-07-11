"""Tests for i2c/migrate.py — schema versioning + in-place .state/ migration (§8)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from i2c import migrate
from i2c import validate as v


class TempState:
    """A temp project root with a ``.state/`` dir; writes project.json from a dict."""

    def __init__(self, project: dict):
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.root: Path | None = None
        self._project = project

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_migrate_")
        self.root = Path(self._tmp.name)
        state_dir = self.root / ".state"
        state_dir.mkdir()
        (state_dir / "project.json").write_text(
            json.dumps(self._project, indent=2) + "\n", encoding="utf-8"
        )
        return self.root

    def __exit__(self, *args):
        self._tmp.cleanup()


class TestProjectVersion(unittest.TestCase):
    def test_absent_is_zero(self):
        with TempState({"phase": 1, "state": "plan"}) as root:
            self.assertEqual(migrate.project_version(root / ".state"), 0)

    def test_present_value(self):
        with TempState({"schema_version": 1, "phase": 1, "state": "plan"}) as root:
            self.assertEqual(migrate.project_version(root / ".state"), 1)

    def test_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".state").mkdir()
            with self.assertRaises(migrate.MigrationError):
                migrate.project_version(Path(tmp) / ".state")


class TestMigrateProject(unittest.TestCase):
    def test_legacy_zero_to_current(self):
        legacy = {"phase": 1, "state": "audit_boundary", "blocked": True, "gotchas": []}
        with TempState(legacy) as root:
            result = migrate.migrate_project(root)
            self.assertTrue(result.migrated)
            self.assertEqual(
                (result.from_version, result.to_version),
                (0, migrate.CURRENT_SCHEMA_VERSION),
            )
            self.assertTrue(
                any("blocked" in c for c in result.changes),
                msg=result.changes,
            )
            data = json.loads(
                (root / ".state" / "project.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("blocked", data)
            self.assertEqual(data["schema_version"], migrate.CURRENT_SCHEMA_VERSION)
            # The migrated file passes schema validation.
            v.validate_state_file(root / ".state" / "project.json")

    def test_noop_chain_from_v1(self):
        # Both 1 → 2 (tests action) and 2 → 3 (pattern field) are pure no-op
        # transforms: the only change from v1 is the version stamp, no field
        # transforms, all the way up to CURRENT.
        v1 = {"schema_version": 1, "phase": 1, "state": "plan", "gotchas": []}
        with TempState(v1) as root:
            result = migrate.migrate_project(root)
            self.assertTrue(result.migrated)
            self.assertEqual(
                (result.from_version, result.to_version),
                (1, migrate.CURRENT_SCHEMA_VERSION),
            )
            # No field changes — only the stamp line.
            self.assertTrue(all("blocked" not in c for c in result.changes))
            self.assertTrue(any(
                f"schema_version={migrate.CURRENT_SCHEMA_VERSION}" in c
                for c in result.changes
            ))
            data = json.loads(
                (root / ".state" / "project.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                data["schema_version"], migrate.CURRENT_SCHEMA_VERSION
            )
            v.validate_state_file(root / ".state" / "project.json")

    def test_two_to_three_noop(self):
        # 2 → 3 is a pure no-op stamp bump (pattern-field forward-compat guard,
        # FU-48). A v2 project (e.g. one written before the pattern field) keeps
        # all fields and only gains the new stamp.
        v2 = {"schema_version": 2, "phase": 1, "state": "plan", "gotchas": []}
        with TempState(v2) as root:
            result = migrate.migrate_project(root)
            self.assertTrue(result.migrated)
            self.assertEqual((result.from_version, result.to_version), (2, 3))
            self.assertTrue(all("blocked" not in c for c in result.changes))
            data = json.loads(
                (root / ".state" / "project.json").read_text(encoding="utf-8")
            )
            self.assertEqual(data["schema_version"], 3)
            v.validate_state_file(root / ".state" / "project.json")

    def test_already_current_is_noop(self):
        current = {
            "schema_version": migrate.CURRENT_SCHEMA_VERSION,
            "phase": 1, "state": "plan", "gotchas": [],
        }
        with TempState(current) as root:
            result = migrate.migrate_project(root)
            self.assertFalse(result.migrated)
            self.assertEqual(result.changes, [])
            self.assertEqual(
                (result.from_version, result.to_version),
                (migrate.CURRENT_SCHEMA_VERSION, migrate.CURRENT_SCHEMA_VERSION),
            )

    def test_idempotent(self):
        legacy = {"phase": 1, "state": "plan", "blocked": False}
        with TempState(legacy) as root:
            migrate.migrate_project(root)
            second = migrate.migrate_project(root)
            self.assertFalse(second.migrated)
            self.assertEqual(second.changes, [])

    def test_newer_than_current_raises(self):
        future = {"schema_version": 99, "phase": 1, "state": "plan"}
        with TempState(future) as root:
            with self.assertRaises(migrate.MigrationError):
                migrate.migrate_project(root)

    def test_non_integer_version_raises(self):
        with TempState({"schema_version": "two", "phase": 1, "state": "plan"}) as root:
            with self.assertRaises(migrate.MigrationError):
                migrate.project_version(root / ".state")

    def test_post_migration_validation_failure_raises(self):
        # A legacy file that stays schema-invalid after the 0->1 step (an
        # unknown key survives) must raise — and must NOT be stamped, so a
        # re-run still surfaces the problem.
        legacy = {"phase": 1, "state": "plan", "blocked": True, "bogus": "x"}
        with TempState(legacy) as root:
            with self.assertRaises(migrate.MigrationError):
                migrate.migrate_project(root)
            data = json.loads(
                (root / ".state" / "project.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("schema_version", data)  # left unstamped
            self.assertEqual(migrate.project_version(root / ".state"), 0)

    def test_dry_run_leaves_files_untouched(self):
        legacy = {"phase": 1, "state": "plan", "blocked": True}
        with TempState(legacy) as root:
            result = migrate.migrate_project(root, dry_run=True)
            self.assertTrue(result.migrated)
            self.assertTrue(any("blocked" in c for c in result.changes))
            # Nothing written: blocked still present, no schema_version stamped.
            data = json.loads(
                (root / ".state" / "project.json").read_text(encoding="utf-8")
            )
            self.assertIn("blocked", data)
            self.assertNotIn("schema_version", data)


class TestNeedsMigration(unittest.TestCase):
    def test_true_for_legacy(self):
        with TempState({"phase": 1, "state": "plan"}) as root:
            self.assertTrue(migrate.needs_migration(root))

    def test_false_for_current(self):
        with TempState({
            "schema_version": migrate.CURRENT_SCHEMA_VERSION,
            "phase": 1, "state": "plan",
        }) as root:
            self.assertFalse(migrate.needs_migration(root))


if __name__ == "__main__":
    unittest.main()
