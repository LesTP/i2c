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


if __name__ == "__main__":
    unittest.main()
