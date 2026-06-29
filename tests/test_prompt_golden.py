"""Worker-prompt byte-stability golden snapshots.

Locks the exact bytes of the assembled worker prompt for every
action x backend x mode combination, plus the FU-35 emit split. This is the
safety net for FU-39 (Phase 3a): removing the assembler's operator-facing
``--section`` modes must not change a single byte of any worker prompt.

Goldens live in ``tests/golden/`` and are generated on first run (or when
``I2C_REGEN_GOLDEN=1``) from the *current* assembler, then asserted byte-for-byte
thereafter. Generate the baseline BEFORE the Phase-3 removal; it must stay green
after. (Also closes the D-impl-2 "golden snapshots deferred" gap.)
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from i2c import assemble_context as ac

I2C_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = I2C_ROOT / "examples" / "initial_state"
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
REGEN = os.environ.get("I2C_REGEN_GOLDEN") == "1"

# A fixed module contract for the fixture's phase-2 module (event_store). The
# committed fixture omits it; the worker prompt requires it (render_module_contract
# errors when a module is declared but its ARCH file is missing). Kept constant so
# the assembled prompt is deterministic.
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

ACTIONS = ("plan", "execute", "review", "close")
BACKENDS = ("claude", "codex")
MODES = ("autonomous", "supervised")


class _Project:
    """Copy the fixture + framework adapters + the fixed ARCH into a temp dir."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_golden_")
        root = Path(self._tmp.name) / "project"
        shutil.copytree(FIXTURE, root)
        adapters = I2C_ROOT / "i2c" / "data" / "adapters"
        shutil.copy2(adapters / "claude.md", root / "CLAUDE.md")
        shutil.copy2(adapters / "codex.md", root / "CODEX.md")
        (root / "ARCH_event_store.md").write_text(ARCH_EVENT_STORE, encoding="utf-8")
        self._prev = Path.cwd()
        os.chdir(root)
        return root

    def __exit__(self, *args):
        os.chdir(self._prev)
        self._tmp.cleanup()


def _full_prompt(action: str, backend: str, mode: str) -> str:
    ns = argparse.Namespace(
        action=action, section=None, phase=2, mode=mode, module=None,
        backend=backend,
    )
    return ac.build_full_prompt(ac.build_context(ns))


def _assert_golden(test: unittest.TestCase, name: str, content: str) -> None:
    GOLDEN_DIR.mkdir(exist_ok=True)
    path = GOLDEN_DIR / name
    if REGEN or not path.exists():
        path.write_text(content, encoding="utf-8", newline="\n")
    expected = path.read_text(encoding="utf-8")
    test.assertEqual(
        content, expected,
        msg=f"worker-prompt bytes changed vs golden {name}; "
            f"if intentional, regenerate with I2C_REGEN_GOLDEN=1",
    )


class TestPromptGolden(unittest.TestCase):
    def test_full_prompt_byte_stability(self):
        for action in ACTIONS:
            for backend in BACKENDS:
                for mode in MODES:
                    with self.subTest(action=action, backend=backend, mode=mode):
                        with _Project():
                            prompt = _full_prompt(action, backend, mode)
                        _assert_golden(
                            self, f"prompt_{action}_{backend}_{mode}.md", prompt
                        )

    def test_emit_split_identity_and_snapshot(self):
        # Lock the FU-35 split for one representative combo.
        with _Project():
            ns = argparse.Namespace(
                action="execute", section=None, phase=2, mode="autonomous",
                module=None, backend="claude",
            )
            ctx = ac.build_context(ns)
            full = ac.build_full_prompt(ctx)
            stable = ac.build_stable_prefix(ctx)
            volatile = ac.build_volatile_body(ctx)
        self.assertEqual(full, stable.rstrip() + "\n\n" + volatile)
        _assert_golden(self, "prefix_execute_claude_autonomous.md", stable)
        _assert_golden(self, "body_execute_claude_autonomous.md", volatile)


class TestRecoveryPromptGolden(unittest.TestCase):
    """Lock the recovery (diagnose/reconcile) prompt bytes.

    The cache-stable prefix (WORKER CONTRACT + TOOL RULES) is fully
    deterministic and always golden-asserted. The full prompt embeds the
    failure-context Region-3 section, which runs the git/disk drift audit — only
    deterministic when the temp project is not inside a git repo, so that golden
    is skipped in the rare environment where the OS temp dir is itself a repo.
    """

    RECOVERY = ("diagnose", "reconcile")

    @staticmethod
    def _ns(action: str, emit: str) -> argparse.Namespace:
        return argparse.Namespace(
            action=action, section=None, phase=2, mode="autonomous",
            module=None, backend="claude", target=None, step_budget=1, emit=emit,
        )

    def test_recovery_stable_prefix_golden(self):
        for action in self.RECOVERY:
            with self.subTest(action=action):
                with _Project():
                    prefix = ac.build_stable_prefix(
                        ac.build_context(self._ns(action, "system"))
                    )
                _assert_golden(
                    self, f"prefix_{action}_claude_autonomous.md", prefix
                )

    def test_recovery_full_prompt_golden(self):
        from i2c import recovery

        for action in self.RECOVERY:
            with self.subTest(action=action):
                with _Project() as root:
                    if recovery.is_git_repo(root):
                        self.skipTest(
                            "temp project is inside a git repo; failure-context "
                            "is not deterministic here"
                        )
                    prompt = ac.build_full_prompt(
                        ac.build_context(self._ns(action, "full"))
                    )
                _assert_golden(
                    self, f"prompt_{action}_claude_autonomous.md", prompt
                )


if __name__ == "__main__":
    unittest.main()
