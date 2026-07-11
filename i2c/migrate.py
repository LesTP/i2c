"""i2c schema versioning + in-place ``.state/`` migration (DESIGN_packaging_v1.md §8).

A ``.state/`` directory is shaped by the framework version that wrote it. This
module gives an installed ``i2c`` a way to (a) tell what version a project's
``.state/`` targets and (b) upgrade an older ``.state/`` in place.

Model (per the approved §8 clarifications):
  - ``project.json.schema_version`` is **optional**; **absent ⇒ version 0**
    (legacy / pre-versioning).
  - ``CURRENT_SCHEMA_VERSION`` is the version the current code expects.
  - Migrations are ordered, keyed by from-version, and run sequentially
    (``cur → cur+1 → … → CURRENT``). Each step touches only the files it needs,
    so future steps can transform any ``.state/`` file.
  - Runtime tools are **not** gated on the version; drift is opt-in via
    ``i2c migrate --check``.

The ``0 → 1`` migration drops the removed ``blocked``
field (retired in DESIGN_state_lifecycle_v1) and stamps ``schema_version``.
The ``1 → 2`` migration is a no-op that only bumps the stamp — a forward-compat
guard for the ``tests`` action's additive enum values (DESIGN_tests_action_v1.md
§10, D-tests-7). The ``2 → 3`` migration is likewise a no-op stamp bump — a
forward-compat guard for the additive optional ``project.json.pattern`` field
(FU-48): an older i2c whose schema is ``additionalProperties: false`` would
otherwise reject a ``pattern``-stamped project with an opaque validation error
instead of a clean "upgrade i2c". The registry is the extension point for future
versions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from i2c import state as _state
from i2c import validate as v


CURRENT_SCHEMA_VERSION = 3


class MigrationError(Exception):
    """A migration could not be performed (newer-than-current project, a
    post-migration validation failure, or an unreadable ``project.json``)."""


@dataclass
class MigrationResult:
    from_version: int
    to_version: int
    changes: list[str]
    migrated: bool


# ---------------------------------------------------------------------------
# Raw reads (deliberately bypass validate.* — a legacy file may be schema-
# invalid, e.g. it still carries the dropped ``blocked`` field)
# ---------------------------------------------------------------------------


def _read_raw(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise MigrationError(f"{path} not found.") from e
    except json.JSONDecodeError as e:
        raise MigrationError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise MigrationError(f"{path} must contain a JSON object.")
    return data


def project_version(state_dir: Path) -> int:
    """Return the ``schema_version`` stamped on ``project.json``, or 0 if absent.

    Reads ``project.json`` **raw** (not via ``validate_state_file``) because a
    legacy file may be schema-invalid; we still need to read its version marker.
    A present-but-non-integer ``schema_version`` is itself a corruption we raise
    on (rather than letting ``int()`` throw an unwrapped ``TypeError``).
    """
    project_path = Path(state_dir) / "project.json"
    data = _read_raw(project_path)
    raw = data.get("schema_version", 0)
    # bool is an int subclass; reject it so `true` isn't read as version 1.
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise MigrationError(
            f"{project_path}: schema_version must be an integer, got {raw!r}."
        )
    return raw


# ---------------------------------------------------------------------------
# Migration steps (keyed by from-version)
# ---------------------------------------------------------------------------


def _migrate_0_to_1(state_dir: Path, *, dry_run: bool = False) -> list[str]:
    """0 → 1: drop the legacy ``blocked`` field from ``project.json``.

    ``blocked`` was retired by the 7-state lifecycle redesign
    (DESIGN_state_lifecycle_v1). The ``schema_version`` stamp itself is applied
    by ``migrate_project`` after all steps run.
    """
    project_path = Path(state_dir) / "project.json"
    data = _read_raw(project_path)
    changes: list[str] = []
    if "blocked" in data:
        changes.append("project.json: removed legacy 'blocked' field")
        if not dry_run:
            del data["blocked"]
            _state.atomic_write_json(project_path, data)
    return changes


def _migrate_1_to_2(state_dir: Path, *, dry_run: bool = False) -> list[str]:
    """1 → 2: no-op transform (forward-compat guard for the ``tests`` action).

    The ``tests`` action (DESIGN_tests_action_v1.md, D-tests-7) adds only
    additive enum values (a new ``state``/``action`` value), so existing
    ``.state/`` files keep validating with no data transform. The version bump
    exists purely so an *older* i2c hitting a ``state=tests`` project fails the
    ``migrate_project`` newer-than-current guard cleanly ("upgrade i2c") instead
    of crashing in the state machine. The ``schema_version`` stamp is applied
    centrally by ``migrate_project``.
    """
    return []


def _migrate_2_to_3(state_dir: Path, *, dry_run: bool = False) -> list[str]:
    """2 → 3: no-op transform (forward-compat guard for ``project.json.pattern``).

    FU-48 adds an optional ``pattern`` field to ``project.json``. Existing
    ``.state/`` files omit it and keep validating (absent ⇒ Pattern A), so there
    is no data transform. The version bump exists so an *older* i2c — whose
    project schema is ``additionalProperties: false`` and has no ``pattern``
    property — rejects a ``pattern``-stamped project via the newer-than-current
    guard ("upgrade i2c") rather than an opaque validation failure. The
    ``schema_version`` stamp is applied centrally by ``migrate_project``.
    """
    return []


# Ordered registry: from-version → step. Sequential application from the
# project's current version up to (but not including) CURRENT_SCHEMA_VERSION.
_MIGRATIONS: dict[int, Callable[..., list[str]]] = {
    0: _migrate_0_to_1,
    1: _migrate_1_to_2,
    2: _migrate_2_to_3,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def needs_migration(root: Path) -> bool:
    """True when the project's ``.state/`` targets an older schema than current.

    A newer-than-current project does **not** "need migration" (there is no
    forward step to apply); it needs a newer ``i2c`` — surfaced as a
    ``MigrationError`` by ``migrate_project``.
    """
    return project_version(Path(root) / ".state") < CURRENT_SCHEMA_VERSION


def _stamp_version(state_dir: Path, version: int) -> None:
    project_path = Path(state_dir) / "project.json"
    data = _read_raw(project_path)
    data["schema_version"] = version
    _state.atomic_write_json(project_path, data)


def _validate_state_dir(state_dir: Path) -> None:
    """Validate every present ``.state/`` file; raise ``MigrationError`` on failure.

    Only files that exist are checked, so a partial ``.state/`` (e.g. a project
    with no ``decisions.json`` yet) still migrates cleanly.
    """
    try:
        for name in ("project.json", "phases.json", "steps.json", "decisions.json"):
            p = Path(state_dir) / name
            if p.is_file():
                v.validate_state_file(p)
        devlog = Path(state_dir) / "devlog.jsonl"
        if devlog.is_file():
            v.validate_devlog_jsonl(devlog)
    except ValueError as e:
        raise MigrationError(f"post-migration validation failed: {e}") from e


def migrate_project(root: Path, *, dry_run: bool = False) -> MigrationResult:
    """Migrate a project's ``.state/`` from its current schema to ``CURRENT``.

    Applies each registered step for ``v in range(cur, CURRENT)``, then stamps
    ``schema_version = CURRENT`` on ``project.json``. With ``dry_run`` no file is
    written — only the would-be changes are collected.

    Raises ``MigrationError`` when the project targets a *newer* schema than this
    ``i2c`` supports, or (non-dry-run) when a resulting file fails validation.
    """
    state_dir = Path(root) / ".state"
    cur = project_version(state_dir)

    if cur > CURRENT_SCHEMA_VERSION:
        raise MigrationError(
            f"project targets framework schema v{cur}; this i2c supports "
            f"v{CURRENT_SCHEMA_VERSION}. Upgrade i2c."
        )
    if cur == CURRENT_SCHEMA_VERSION:
        return MigrationResult(
            from_version=cur, to_version=cur, changes=[], migrated=False
        )

    changes: list[str] = []
    for v_from in range(cur, CURRENT_SCHEMA_VERSION):
        step = _MIGRATIONS.get(v_from)
        if step is None:
            # Registry gap — CURRENT_SCHEMA_VERSION was advanced without a step.
            raise MigrationError(
                f"no migration step registered for schema v{v_from} "
                f"(i2c bug: CURRENT_SCHEMA_VERSION advanced without a step)."
            )
        changes.extend(step(state_dir, dry_run=dry_run))

    if not dry_run:
        # Validate the migrated files *before* stamping the version, so a
        # validation failure leaves the project unstamped (version unchanged)
        # and the migration re-runnable — rather than silently "current but
        # invalid" on the next run.
        _validate_state_dir(state_dir)
        _stamp_version(state_dir, CURRENT_SCHEMA_VERSION)
    changes.append(f"project.json: stamped schema_version={CURRENT_SCHEMA_VERSION}")

    return MigrationResult(
        from_version=cur,
        to_version=CURRENT_SCHEMA_VERSION,
        changes=changes,
        migrated=True,
    )
