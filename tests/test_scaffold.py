"""Tests for i2c/scaffold.py — `i2c init` and `i2c eject` (§5.4)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from i2c import scaffold
from i2c import validate as v
from i2c.assemble_context import packaged_data_dir


class TempDir:
    def __init__(self):
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.root: Path | None = None

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_scaffold_")
        self.root = Path(self._tmp.name)
        return self.root

    def __exit__(self, *args):
        self._tmp.cleanup()


class TestInit(unittest.TestCase):
    def test_init_creates_state_and_docs(self):
        with TempDir() as root:
            scaffold.init_project(root, name="Demo")
            # .state seeded and schema-valid.
            project = v.validate_state_file(root / ".state" / "project.json")
            self.assertEqual(project["phase"], 0)
            self.assertEqual(project["state"], "plan")
            self.assertEqual(project["schema_version"], 1)
            for arr in ("phases.json", "steps.json", "decisions.json"):
                self.assertEqual(v.validate_state_file(root / ".state" / arr), [])
            v.validate_devlog_jsonl(root / ".state" / "devlog.jsonl")
            # Docs + both adapters.
            for f in ("PROJECT.md", "ARCHITECTURE.md", "CLAUDE.md", "CODEX.md"):
                self.assertTrue((root / f).is_file(), f"{f} missing")
            # Starter config.
            toml = root / "i2c.toml"
            self.assertTrue(toml.is_file(), "i2c.toml missing")
            self.assertIn("[run]", toml.read_text(encoding="utf-8"))
            # gitignore.
            self.assertIn(
                "logs/loop/", (root / ".gitignore").read_text(encoding="utf-8")
            )

    def test_name_substituted(self):
        with TempDir() as root:
            scaffold.init_project(root, name="MyProj")
            claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
            project = (root / "PROJECT.md").read_text(encoding="utf-8")
            self.assertIn("MyProj", claude)
            self.assertNotIn("[Project Name]", claude)
            self.assertIn("MyProj", project)
            self.assertNotIn("[Project Name]", project)

    def test_backend_claude_only(self):
        with TempDir() as root:
            scaffold.init_project(root, name="x", backends=("claude",))
            self.assertTrue((root / "CLAUDE.md").is_file())
            self.assertFalse((root / "CODEX.md").is_file())

    def test_refuses_existing_without_force(self):
        with TempDir() as root:
            scaffold.init_project(root, name="x")
            (root / "PROJECT.md").write_text("CUSTOM", encoding="utf-8")
            with self.assertRaises(scaffold.ScaffoldError):
                scaffold.init_project(root, name="x")
            # Untouched.
            self.assertEqual((root / "PROJECT.md").read_text(encoding="utf-8"), "CUSTOM")

    def test_force_overwrites(self):
        with TempDir() as root:
            scaffold.init_project(root, name="x")
            (root / "PROJECT.md").write_text("CUSTOM", encoding="utf-8")
            scaffold.init_project(root, name="x", force=True)
            self.assertNotEqual(
                (root / "PROJECT.md").read_text(encoding="utf-8"), "CUSTOM"
            )

    def test_gitignore_append_idempotent_and_preserving(self):
        with TempDir() as root:
            (root / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
            scaffold.init_project(root, name="x")
            scaffold.init_project(root, name="x", force=True)
            gi = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("*.pyc", gi)  # preserved
            self.assertEqual(gi.count("logs/loop/"), 1)  # not duplicated


    def test_seed_stamps_current_schema_version(self):
        from i2c.migrate import CURRENT_SCHEMA_VERSION

        with TempDir() as root:
            scaffold.init_project(root, name="x")
            project = v.validate_state_file(root / ".state" / "project.json")
            self.assertEqual(project["schema_version"], CURRENT_SCHEMA_VERSION)


class TestEject(unittest.TestCase):
    def test_eject_worker_spec(self):
        with TempDir() as root:
            written = scaffold.eject_asset(root, "WORKER_SPEC.md")
            self.assertEqual(written, [root / "WORKER_SPEC.md"])
            self.assertEqual(
                (root / "WORKER_SPEC.md").read_text(encoding="utf-8"),
                (packaged_data_dir() / "WORKER_SPEC.md").read_text(encoding="utf-8"),
            )

    def test_eject_single_instruction(self):
        with TempDir() as root:
            scaffold.eject_asset(root, "instructions/plan.md")
            self.assertTrue((root / "instructions" / "plan.md").is_file())

    def test_eject_all_instructions(self):
        with TempDir() as root:
            written = scaffold.eject_asset(root, "instructions")
            names = sorted(p.name for p in written)
            self.assertEqual(
                names,
                ["close.md", "diagnose.md", "execute.md", "plan.md",
                 "reconcile.md", "review.md"],
            )

    def test_eject_refuses_overwrite(self):
        with TempDir() as root:
            scaffold.eject_asset(root, "WORKER_SPEC.md")
            with self.assertRaises(scaffold.ScaffoldError):
                scaffold.eject_asset(root, "WORKER_SPEC.md")

    def test_eject_rejects_unknown(self):
        with TempDir() as root:
            with self.assertRaises(scaffold.ScaffoldError):
                scaffold.eject_asset(root, "PROJECT.md")


if __name__ == "__main__":
    unittest.main()
