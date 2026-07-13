"""Tests for i2c/config.py — i2c.toml [run] defaults (§5.5)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from i2c import config


class TempDir:
    def __init__(self):
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.root: Path | None = None

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_config_")
        self.root = Path(self._tmp.name)
        return self.root

    def __exit__(self, *args):
        self._tmp.cleanup()


def _write(root: Path, text: str) -> None:
    (root / "i2c.toml").write_text(text, encoding="utf-8")


class TestLoadRunConfig(unittest.TestCase):
    def test_no_file_returns_empty(self):
        with TempDir() as root:
            cfg = config.load_run_config(root)
            self.assertEqual(
                (cfg.backend, cfg.model, cfg.max_budget_usd), (None, None, None)
            )

    def test_reads_run_table(self):
        with TempDir() as root:
            _write(
                root,
                '[run]\nbackend = "codex"\nmodel = "opus"\nmax_budget_usd = 2.5\n',
            )
            cfg = config.load_run_config(root)
            self.assertEqual(cfg.backend, "codex")
            self.assertEqual(cfg.model, "opus")
            self.assertEqual(cfg.max_budget_usd, 2.5)

    def test_int_budget_coerced_to_float(self):
        with TempDir() as root:
            _write(root, "[run]\nmax_budget_usd = 3\n")
            cfg = config.load_run_config(root)
            self.assertIsInstance(cfg.max_budget_usd, float)
            self.assertEqual(cfg.max_budget_usd, 3.0)

    def test_unknown_key_ignored(self):
        with TempDir() as root:
            _write(root, '[run]\nbackend = "claude"\nfuture_key = 1\n')
            cfg = config.load_run_config(root)
            self.assertEqual(cfg.backend, "claude")

    def test_no_backends_is_empty_dict(self):
        with TempDir() as root:
            _write(root, '[run]\nbackend = "claude"\n')
            cfg = config.load_run_config(root)
            self.assertEqual(cfg.backends, {})

    def test_reads_backends_map(self):
        with TempDir() as root:
            _write(
                root,
                '[run]\nbackend = "claude"\n'
                '[run.backends]\nplan = "claude"\nexecute = "codex"\n',
            )
            cfg = config.load_run_config(root)
            self.assertEqual(cfg.backends, {"plan": "claude", "execute": "codex"})

    def test_backends_map_accepts_tests_action(self):
        with TempDir() as root:
            _write(
                root,
                '[run.backends]\ntests = "claude"\nexecute = "codex"\n',
            )
            cfg = config.load_run_config(root)
            self.assertEqual(cfg.backends, {"tests": "claude", "execute": "codex"})

    def test_backends_map_accepts_refine_action(self):
        with TempDir() as root:
            _write(
                root,
                '[run.backends]\nrefine = "codex"\nexecute = "codex"\n',
            )
            cfg = config.load_run_config(root)
            self.assertEqual(cfg.backends, {"refine": "codex", "execute": "codex"})

    def test_backends_invalid_action_key_raises(self):
        with TempDir() as root:
            _write(root, '[run.backends]\nbuild = "codex"\n')
            with self.assertRaises(config.ConfigError):
                config.load_run_config(root)

    def test_backends_invalid_backend_value_raises(self):
        with TempDir() as root:
            _write(root, '[run.backends]\nexecute = "gemini"\n')
            with self.assertRaises(config.ConfigError):
                config.load_run_config(root)

    def test_found_from_subdir(self):
        with TempDir() as root:
            _write(root, '[run]\nbackend = "codex"\n')
            sub = root / "a" / "b"
            sub.mkdir(parents=True)
            cfg = config.load_run_config(sub)
            self.assertEqual(cfg.backend, "codex")

    def test_invalid_backend_raises(self):
        with TempDir() as root:
            _write(root, '[run]\nbackend = "gemini"\n')
            with self.assertRaises(config.ConfigError):
                config.load_run_config(root)

    def test_bad_budget_type_raises(self):
        with TempDir() as root:
            _write(root, '[run]\nmax_budget_usd = "lots"\n')
            with self.assertRaises(config.ConfigError):
                config.load_run_config(root)

    def test_malformed_toml_raises(self):
        with TempDir() as root:
            _write(root, "[run\nbackend = ")
            with self.assertRaises(config.ConfigError):
                config.load_run_config(root)


class TestLoadTelegramConfig(unittest.TestCase):
    def test_no_file_returns_empty(self):
        with TempDir() as root:
            cfg = config.load_telegram_config(root)
            self.assertEqual(cfg.admins, ())
            self.assertIsNone(cfg.root)

    def test_reads_telegram_table(self):
        with TempDir() as root:
            _write(root, '[telegram]\nadmins = [111, 222]\nroot = "/work"\n')
            cfg = config.load_telegram_config(root)
            self.assertEqual(cfg.admins, (111, 222))
            self.assertEqual(cfg.root, "/work")

    def test_admins_must_be_ints(self):
        with TempDir() as root:
            _write(root, '[telegram]\nadmins = ["nope"]\n')
            with self.assertRaises(config.ConfigError):
                config.load_telegram_config(root)

    def test_bool_is_not_a_valid_admin_id(self):
        with TempDir() as root:
            _write(root, "[telegram]\nadmins = [true]\n")
            with self.assertRaises(config.ConfigError):
                config.load_telegram_config(root)

    def test_root_must_be_string(self):
        with TempDir() as root:
            _write(root, "[telegram]\nroot = 5\n")
            with self.assertRaises(config.ConfigError):
                config.load_telegram_config(root)

    def test_absent_table_is_empty(self):
        with TempDir() as root:
            _write(root, '[run]\nbackend = "claude"\n')
            cfg = config.load_telegram_config(root)
            self.assertEqual(cfg.admins, ())


if __name__ == "__main__":
    unittest.main()
