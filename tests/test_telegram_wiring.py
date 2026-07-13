"""Tests for i2c.surfaces.telegram — the PTB wiring shell.

These must pass whether or not `python-telegram-bot` is installed: the module
imports lazily, and the paths tested here (token check, chat-state persistence,
CLI error path) never reach the transport library.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from i2c import cli
from i2c.surfaces import telegram as tg


class TestWiring(unittest.TestCase):
    def test_module_imports_without_ptb(self):
        # Importing the shell must not require the telegram extra.
        self.assertTrue(hasattr(tg, "serve"))
        self.assertTrue(hasattr(tg, "build_application"))

    def test_serve_without_token_raises_missing_token(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(tg.TOKEN_ENV, None)
            with self.assertRaises(tg.MissingToken):
                tg.serve(token=None)

    def test_chat_state_roundtrip_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            st = tg._ChatState(path)
            self.assertIsNone(st.get(42))
            st.set(42, "alpha")
            self.assertEqual(st.get(42), "alpha")
            # A fresh instance reads it back from disk.
            self.assertEqual(tg._ChatState(path).get(42), "alpha")

    def test_cli_serve_missing_token_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(tg.TOKEN_ENV, None)
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = cli.main(["serve", "telegram", "--root", tmp])
        self.assertEqual(rc, 2)
        self.assertIn("ERROR", err.getvalue())

    def test_make_refine_runner_shells_refine_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            fn = tg._make_refine_runner(Path(tmp))
            with mock.patch.object(tg.subprocess, "run") as m:
                m.return_value = mock.Mock(returncode=0)
                rc = fn(proj, "FU-5", "codex")
            self.assertEqual(rc, 0)
            cmd, kwargs = m.call_args
            argv = cmd[0]
            self.assertEqual(argv[-4:], ["refine", "FU-5", "--backend", "codex"])
            self.assertEqual(kwargs["cwd"], str(proj))

    def test_make_refine_runner_without_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            fn = tg._make_refine_runner(Path(tmp))
            with mock.patch.object(tg.subprocess, "run") as m:
                m.return_value = mock.Mock(returncode=2)
                rc = fn(proj, "FU-9")
            self.assertEqual(rc, 2)
            argv = m.call_args[0][0]
            self.assertEqual(argv[-2:], ["refine", "FU-9"])
            self.assertNotIn("--backend", argv)


if __name__ == "__main__":
    unittest.main()
