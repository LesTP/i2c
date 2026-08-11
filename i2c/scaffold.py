"""i2c project scaffolding — ``i2c init`` and ``i2c eject`` (§5.4).

``init`` bootstraps a new i2c project in a directory: it seeds ``.state/``,
writes ``PROJECT.md`` / ``ARCHITECTURE.md`` from packaged templates, scaffolds
the per-backend adapter(s) (``CLAUDE.md`` / ``CODEX.md``), and gitignores
``logs/loop/``. ``eject`` materializes a packaged, override-resolved asset
(``WORKER_SPEC.md`` or an ``instructions/<action>.md``) into the project as a
local override for editing (the authoring counterpart to the §5.3 resolver).

Adapters and project-doc templates ship as package-data under ``i2c/data/``
(``adapters/``, ``templates/``); ``init`` is the supported way to obtain
adapters (runtime adapter resolution stays project-root-only per §5.3).
The seed ``project.json`` is stamped with ``CURRENT_SCHEMA_VERSION`` (§8).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from i2c import state
from i2c import validate as v
from i2c.assemble_context import ACTIONS, packaged_data_dir
from i2c.migrate import CURRENT_SCHEMA_VERSION

# Backend → scaffolded adapter filename at the project root.
_ADAPTER_TARGET = {"claude": "CLAUDE.md", "codex": "CODEX.md"}
BACKENDS = tuple(_ADAPTER_TARGET)

# [run.backends] activation policies for `i2c init --run-split`.
RUN_SPLIT_MODES = ("auto", "on", "off")

# The fleet per-action split scaffolded when the split is active (FU-60):
# judgment-heavy PLAN/REVIEW on claude, mechanical EXECUTE/CLOSE on codex.
# `tests` is deliberately omitted so it falls back to [run].backend (a capable
# tier per D-tests-6).
_FLEET_SPLIT = (
    ("plan", "claude"),
    ("execute", "codex"),
    ("review", "claude"),
    ("close", "codex"),
)

# Override-resolved assets that `eject` can materialize (§5.3). The special
# token "instructions" expands to every per-action procedure.
EJECTABLE = ("WORKER_SPEC.md", *(f"instructions/{a}.md" for a in ACTIONS))

_GITIGNORE_LINES = ("logs/loop/", "dashboard.html")


class ScaffoldError(Exception):
    """A scaffolding precondition failed (e.g. refusing to clobber)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _packaged_text(relpath: str) -> str:
    return (packaged_data_dir() / relpath).read_text(encoding="utf-8")


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:  # pragma: no cover - defensive
        return str(path)


def _write_text(
    root: Path, target: Path, content: str, report: list[str], *, force: bool
) -> None:
    rel = _rel(root, target)
    if target.exists() and not force:
        report.append(f"skipped {rel} (exists)")
        return
    verb = "overwrote" if target.exists() else "created"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    report.append(f"{verb} {rel}")


def _ensure_gitignore(root: Path, report: list[str]) -> None:
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    present = {ln.strip() for ln in existing.splitlines()}
    missing = [ln for ln in _GITIGNORE_LINES if ln not in present]
    if not missing:
        report.append("skipped .gitignore (entries already present)")
        return
    new = existing
    if new and not new.endswith("\n"):
        new += "\n"
    new += "".join(line + "\n" for line in missing)
    path.write_text(new, encoding="utf-8")
    report.append("updated .gitignore" if existing else "created .gitignore")


def _codex_available() -> bool:
    """Whether the ``codex`` CLI is on PATH *on this host* (see FU-60 caveat:
    ``init`` may run on a different host than autonomous runs)."""
    return shutil.which("codex") is not None


def _resolve_run_split(mode: str, adapters: tuple[str, ...]) -> tuple[bool, str | None]:
    """Decide whether to scaffold the ``[run.backends]`` split active.

    Returns ``(active, note)`` where ``note`` is an optional operator message
    appended to the init report. ``mode`` is one of ``RUN_SPLIT_MODES``.
    """
    if mode not in RUN_SPLIT_MODES:
        raise ScaffoldError(
            f"unknown --run-split {mode!r}; expected one of {RUN_SPLIT_MODES}"
        )
    if mode == "off":
        return False, None
    if mode == "on":
        if "codex" not in adapters:
            return True, (
                "note: [run.backends] routes execute/close to codex, but no "
                "CODEX.md was scaffolded (--backend claude). Re-run with "
                "--backend both or those actions will fail."
            )
        return True, "activated the [run.backends] split in i2c.toml (--run-split on)."
    # auto: activate only when codex is usable here (adapter scaffolded + on PATH).
    if "codex" in adapters and _codex_available():
        return True, "detected codex on PATH — activated the [run.backends] split in i2c.toml."
    return False, (
        "codex not detected on PATH here — scaffolded claude-only [run].backend. "
        "If this project runs where codex is available (e.g. the Pi), re-run "
        "`i2c init --run-split on`, or uncomment [run.backends] in i2c.toml."
    )


def _backends_block(active: bool) -> str:
    """Render the ``[run.backends]`` region for the scaffolded i2c.toml."""
    if active:
        rows = "\n".join(f'{action} = "{backend}"' for action, backend in _FLEET_SPLIT)
        return (
            "# Per-action backend split: judgment-heavy PLAN/REVIEW on claude,\n"
            "# mechanical EXECUTE/CLOSE on codex. `i2c run --backend X` overrides\n"
            "# all. Only route an action to a backend whose CLI is on PATH.\n"
            "[run.backends]\n"
            f"{rows}"
        )
    return (
        "# Optional: per-action backend, overriding [run].backend for that action.\n"
        "# Lets you spread load across backends (e.g. heavy EXECUTE on codex, the\n"
        "# rest on claude) or use an independent reviewer. `i2c run --backend X`\n"
        "# overrides all. (Re-run `i2c init --run-split on` to activate this.)\n"
        "# [run.backends]\n"
        '# plan = "claude"\n'
        '# execute = "codex"\n'
        '# review = "claude"\n'
        '# close = "codex"'
    )


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def init_project(
    root: Path,
    *,
    name: str,
    backends: tuple[str, ...] = BACKENDS,
    pattern: str = "A",
    run_split: str = "auto",
    force: bool = False,
) -> list[str]:
    """Scaffold a new i2c project in ``root``. Returns a report of actions.

    ``pattern`` stamps ``project.json.pattern`` ("A" per-module ARCH files, or
    "B" single-document ARCHITECTURE.md; see ref/SPEC_architecture.md). It can be
    changed later with ``i2c state set project.json pattern=...``.

    ``run_split`` (one of ``RUN_SPLIT_MODES``) sets whether the scaffolded
    i2c.toml activates the ``[run.backends]`` per-action split: ``auto`` turns it
    on when the ``codex`` CLI is on PATH here (FU-60), ``split`` forces it on, and
    ``claude`` keeps the single-backend default.

    Raises ``ScaffoldError`` if ``.state/project.json`` already exists and
    ``force`` is not set.
    """
    root = Path(root)
    if pattern not in ("A", "B"):
        raise ScaffoldError(f"unknown pattern {pattern!r}; expected 'A' or 'B'")
    for b in backends:
        if b not in _ADAPTER_TARGET:
            raise ScaffoldError(f"unknown backend {b!r}; expected one of {BACKENDS}")
    split_active, split_note = _resolve_run_split(run_split, backends)

    state_dir = root / ".state"
    project_json = state_dir / "project.json"
    if project_json.exists() and not force:
        raise ScaffoldError(
            f".state/project.json already exists in {root}; "
            "refusing to overwrite (pass --force to re-scaffold)."
        )

    report: list[str] = []
    state_dir.mkdir(parents=True, exist_ok=True)

    # Seed .state/ (atomic, schema-validated writes).
    state.atomic_write_json(
        project_json,
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "pattern": pattern,
            "phase": 0,
            "state": "plan",
            "gotchas": [],
        },
    )
    report.append(f"created {_rel(root, project_json)}")
    for arr in ("phases.json", "steps.json", "decisions.json"):
        p = state_dir / arr
        state.atomic_write_json(p, [])
        report.append(f"created {_rel(root, p)}")
    devlog = state_dir / "devlog.jsonl"
    devlog.write_text("", encoding="utf-8")
    report.append(f"created {_rel(root, devlog)}")
    telemetry = state_dir / "telemetry.jsonl"
    telemetry.write_text("", encoding="utf-8")
    report.append(f"created {_rel(root, telemetry)}")

    # Self-check: seeded state must validate against the registered schemas.
    try:
        for name_ in ("project.json", "phases.json", "steps.json", "decisions.json"):
            v.validate_state_file(state_dir / name_)
        v.validate_devlog_jsonl(devlog)
        v.validate_jsonl(telemetry, v.TELEMETRY_ENTRY_SCHEMA)
    except ValueError as e:  # pragma: no cover - seeds are known-good
        raise ScaffoldError(f"seeded .state failed validation: {e}") from e

    # Project docs (from packaged templates, project-name substituted).
    for tmpl, target in (
        ("templates/PROJECT.md", "PROJECT.md"),
        ("templates/ARCHITECTURE.md", "ARCHITECTURE.md"),
    ):
        body = _packaged_text(tmpl).replace("[Project Name]", name)
        _write_text(root, root / target, body, report, force=force)

    # Adapter(s) (from packaged templates, project-name substituted).
    for b in backends:
        body = _packaged_text(f"adapters/{b}.md").replace("[Project Name]", name)
        _write_text(root, root / _ADAPTER_TARGET[b], body, report, force=force)

    # Starter i2c.toml (commented [run] defaults; §5.5). The commented backend
    # line reflects the run-relevant backend (claude when both are scaffolded);
    # the [Backends] region is rendered active or commented per run_split.
    run_backend = "claude" if "claude" in backends else backends[0]
    toml_body = _packaged_text("templates/i2c.toml").replace("[Backend]", run_backend)
    toml_body = toml_body.replace("[Backends]", _backends_block(split_active))
    _write_text(root, root / "i2c.toml", toml_body, report, force=force)

    _ensure_gitignore(root, report)
    if split_note:
        report.append(split_note)
    return report


# ---------------------------------------------------------------------------
# eject
# ---------------------------------------------------------------------------


def _expand_eject(asset: str) -> list[str]:
    if asset == "instructions":
        return [f"instructions/{a}.md" for a in ACTIONS]
    normalized = asset.replace("\\", "/")
    if normalized not in EJECTABLE:
        raise ScaffoldError(
            f"{asset!r} is not ejectable. Ejectable assets: "
            f"{', '.join(('instructions', *EJECTABLE))}"
        )
    return [normalized]


def eject_asset(root: Path, asset: str, *, force: bool = False) -> list[Path]:
    """Copy packaged override-resolved asset(s) into ``root`` for local editing.

    ``asset`` is ``WORKER_SPEC.md``, an ``instructions/<action>.md``, or the
    token ``instructions`` (all action procedures). Raises ``ScaffoldError`` on
    an unknown asset or when a local copy exists and ``force`` is not set.
    """
    root = Path(root)
    written: list[Path] = []
    for relpath in _expand_eject(asset):
        target = root / relpath
        if target.exists() and not force:
            raise ScaffoldError(
                f"{_rel(root, target)} already exists; pass --force to overwrite."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_packaged_text(relpath), encoding="utf-8")
        written.append(target)
    return written
