"""Assembler purity lock (provenance Tier 1 — DESIGN_provenance_v1 §0, FU-63).

The assembled worker prompt must be a **pure function of the input set**: same
inputs → identical bytes, every time, regardless of when or where the assembler
runs. This is the property the model-benchmark thread relies on — the
runner records ``prompt_hash`` (telemetry.py) as a *replay key*, and that key is
only meaningful if re-assembly is reproducible (DESIGN_benchmark_v1 §8/§9.3).

``test_prompt_golden`` locks the prompt against a *stored* snapshot; this module
asserts purity as a *property* — double-assemble identity, working-directory
independence, and wall-clock independence — so a future impurity (a leaked
timestamp, an absolute path, nondeterministic ordering) fails here even before a
golden is regenerated.

**Scope: the five core lifecycle actions only** (plan/tests/execute/review/
close). The two recovery actions (diagnose/reconcile) are **deliberately
excluded**: their prompt embeds a live git/disk drift audit
(``render_failure_context``), so it is intentionally *not* reproducible and its
``prompt_hash`` is not a replay key (DESIGN_provenance_v1 §0). ``test_prompt_golden``
already treats the recovery full prompt as non-deterministic inside a git repo.
"""

from __future__ import annotations

import argparse
import os
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from i2c import assemble_context as ac
from tests._fixtures import copy_fixture, write_adapters

# The core lifecycle actions whose prompt purity we guarantee. Recovery actions
# (diagnose/reconcile) are excluded by design — see the module docstring.
CORE_ACTIONS = ("plan", "tests", "execute", "review", "close")
BACKENDS = ("claude", "codex")

# The committed fixture is a phase-2 project whose module is ``event_store`` but
# omits its ARCH file; the worker prompt requires it (render_contract errors when
# a module is declared but its contract is missing). Mirror test_prompt_golden so
# the assembled prompt is well-formed and deterministic.
ARCH_EVENT_STORE = """\
# ARCH: event_store

## Purpose

Append-only event storage with atomic writes.

## Interface

- `append(event) -> None`
- `read(since) -> list[Event]`

## Escalation Triggers

- Storage backend change requires re-architecture -> escalate.
"""


@contextmanager
def _temp_project():
    """Copy the fixture + adapters + fixed ARCH into a temp dir (not chdir'd)."""
    with tempfile.TemporaryDirectory(prefix="i2c_purity_") as tmp:
        root = Path(tmp) / "project"
        copy_fixture(root)
        write_adapters(root)
        (root / "ARCH_event_store.md").write_text(ARCH_EVENT_STORE, encoding="utf-8")
        yield root


@contextmanager
def _chdir(path: Path):
    prev = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def _assemble(action: str, backend: str) -> str:
    """Assemble the full prompt for the current working directory's project."""
    ns = argparse.Namespace(
        action=action, section=None, phase=2, mode="autonomous",
        module=None, backend=backend,
    )
    return ac.build_full_prompt(ac.build_context(ns))


@contextmanager
def _frozen_clock(t: float):
    """Freeze the wall clock at ``t`` for the duration of the block.

    Patches the ``time`` module's clock functions. If any assembler code path
    reads the wall clock, two assembles under *different* frozen clocks diverge
    and the test fails — which is the guard we want.
    """
    with mock.patch.object(time, "time", lambda: t), \
            mock.patch.object(time, "monotonic", lambda: t), \
            mock.patch.object(time, "time_ns", lambda: int(t * 1_000_000_000)):
        yield


class TestAssemblerPurity(unittest.TestCase):
    def test_double_assemble_is_byte_identical(self):
        """Same project, two consecutive assembles → identical bytes."""
        with _temp_project() as root, _chdir(root):
            for action in CORE_ACTIONS:
                for backend in BACKENDS:
                    with self.subTest(action=action, backend=backend):
                        first = _assemble(action, backend)
                        second = _assemble(action, backend)
                        self.assertEqual(first, second)

    def test_working_directory_independent(self):
        """Assembling from a nested subdir resolves the same project root and
        yields byte-identical output — no cwd-derived content leaks in."""
        with _temp_project() as root:
            nested = root / "sub" / "deep"
            nested.mkdir(parents=True)
            for action in CORE_ACTIONS:
                for backend in BACKENDS:
                    with self.subTest(action=action, backend=backend):
                        with _chdir(root):
                            from_root = _assemble(action, backend)
                        with _chdir(nested):
                            from_nested = _assemble(action, backend)
                        self.assertEqual(from_root, from_nested)

    def test_wall_clock_independent(self):
        """Two assembles under different frozen clocks → identical bytes.

        The core-action assembler reads no clock today; this locks that in so a
        future leaked timestamp is caught here.
        """
        with _temp_project() as root, _chdir(root):
            for action in CORE_ACTIONS:
                for backend in BACKENDS:
                    with self.subTest(action=action, backend=backend):
                        with _frozen_clock(1_000_000_000.0):
                            early = _assemble(action, backend)
                        with _frozen_clock(2_000_000_000.0):
                            late = _assemble(action, backend)
                        self.assertEqual(early, late)


if __name__ == "__main__":
    unittest.main()
