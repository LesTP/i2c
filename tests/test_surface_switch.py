"""Guards the §5.1 worker tool-surface switch (DESIGN_packaging_v1.md D-pkg-4).

The worker-facing runtime surfaces — the action procedures (`instructions/*.md`)
and the backend adapters (`CLAUDE.md`, `CODEX.md`) — must drive state through
the stable `i2c` console command, never the raw `python3 tools/<x>.py` paths
that forced consumers to carry ABI-compatible tool copies. This test fails if
the old surface leaks back into those files.

Scope is deliberately the runtime worker surface only. Design / architecture
records (`DESIGN_*.md`, `ARCH_assembler.md`, `FOLLOWUPS.md`) intentionally
retain historical lineage, decision provenance, program interface specs, and
program-emitted strings, so they are not linted here.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent
INSTRUCTIONS_DIR = I2C_ROOT / "i2c" / "data" / "instructions"
ADAPTERS = [
    I2C_ROOT / "i2c" / "data" / "adapters" / "claude.md",
    I2C_ROOT / "i2c" / "data" / "adapters" / "codex.md",
]

# Old-surface invocation patterns that must not appear in worker runtime files.
_FORBIDDEN = re.compile(r"python3?\s+tools/|tools/state\.py|\bstate\.py\b")


def _worker_surface_files() -> list[Path]:
    return sorted(INSTRUCTIONS_DIR.glob("*.md")) + ADAPTERS


class TestSurfaceSwitch(unittest.TestCase):
    def test_no_old_tool_paths_in_worker_surface(self):
        offenders: list[str] = []
        for path in _worker_surface_files():
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _FORBIDDEN.search(line):
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], msg="\n".join(offenders))

    def test_instructions_use_i2c_state(self):
        # Action procedures that write state must reference the new surface at
        # least once. Read-only procedures (diagnose) legitimately don't write
        # state, so they're exempt.
        read_only = {"diagnose.md"}
        for path in sorted(INSTRUCTIONS_DIR.glob("*.md")):
            if path.name in read_only:
                continue
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                "i2c state", text, msg=f"{path.name} lacks an `i2c state` reference"
            )


class TestEntryPoint(unittest.TestCase):
    def test_console_script_declared(self):
        pyproject = (I2C_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('i2c = "i2c.cli:main"', pyproject)


if __name__ == "__main__":
    unittest.main()
