"""Shared test fixtures (FU-18).

The canonical fixture (`examples/initial_state`) and the adapter files live on a
network share. Copying them from the share on every `TempProject` dominated
suite runtime. These helpers cache both locally once per process, so each test
copies from local disk instead of the share. The cache is read-only — every test
still mutates its own per-test copy.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = I2C_ROOT / "examples" / "initial_state"
_ADAPTERS_DIR = I2C_ROOT / "i2c" / "data" / "adapters"

_CACHE_TMP: tempfile.TemporaryDirectory | None = None
_CACHED_FIXTURE: Path | None = None
_ADAPTER_CACHE: dict[str, str] | None = None


def cached_fixture() -> Path:
    """A local one-time copy of FIXTURE; the copytree source for tests."""
    global _CACHE_TMP, _CACHED_FIXTURE
    if _CACHED_FIXTURE is None:
        _CACHE_TMP = tempfile.TemporaryDirectory(prefix="i2c_fixture_cache_")
        atexit.register(_CACHE_TMP.cleanup)
        dst = Path(_CACHE_TMP.name) / "fixture"
        shutil.copytree(FIXTURE, dst)
        _CACHED_FIXTURE = dst
    return _CACHED_FIXTURE


def copy_fixture(dst) -> None:
    """`copytree` the cached fixture into ``dst`` (local source, not the share)."""
    shutil.copytree(cached_fixture(), dst)


def cached_adapters() -> dict[str, str]:
    """Adapter file contents (CLAUDE.md/CODEX.md), read from the share once."""
    global _ADAPTER_CACHE
    if _ADAPTER_CACHE is None:
        _ADAPTER_CACHE = {
            "CLAUDE.md": (_ADAPTERS_DIR / "claude.md").read_text(encoding="utf-8"),
            "CODEX.md": (_ADAPTERS_DIR / "codex.md").read_text(encoding="utf-8"),
        }
    return _ADAPTER_CACHE


def write_adapters(root) -> None:
    """Write CLAUDE.md + CODEX.md into ``root`` from the cached adapter contents."""
    a = cached_adapters()
    Path(root, "CLAUDE.md").write_text(a["CLAUDE.md"], encoding="utf-8")
    Path(root, "CODEX.md").write_text(a["CODEX.md"], encoding="utf-8")
