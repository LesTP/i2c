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

from pathlib import Path

from i2c import state
from i2c import validate as v
from i2c.assemble_context import ACTIONS, packaged_data_dir
from i2c.migrate import CURRENT_SCHEMA_VERSION

# Backend → scaffolded adapter filename at the project root.
_ADAPTER_TARGET = {"claude": "CLAUDE.md", "codex": "CODEX.md"}
BACKENDS = tuple(_ADAPTER_TARGET)

# Override-resolved assets that `eject` can materialize (§5.3). The special
# token "instructions" expands to every per-action procedure.
EJECTABLE = ("WORKER_SPEC.md", *(f"instructions/{a}.md" for a in ACTIONS))

_GITIGNORE_LINE = "logs/loop/"


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
    lines = existing.splitlines()
    if _GITIGNORE_LINE in (ln.strip() for ln in lines):
        report.append("skipped .gitignore (logs/loop/ already present)")
        return
    new = existing
    if new and not new.endswith("\n"):
        new += "\n"
    new += _GITIGNORE_LINE + "\n"
    path.write_text(new, encoding="utf-8")
    report.append(("updated .gitignore" if existing else "created .gitignore"))


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def init_project(
    root: Path,
    *,
    name: str,
    backends: tuple[str, ...] = BACKENDS,
    force: bool = False,
) -> list[str]:
    """Scaffold a new i2c project in ``root``. Returns a report of actions.

    Raises ``ScaffoldError`` if ``.state/project.json`` already exists and
    ``force`` is not set.
    """
    root = Path(root)
    for b in backends:
        if b not in _ADAPTER_TARGET:
            raise ScaffoldError(f"unknown backend {b!r}; expected one of {BACKENDS}")

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

    # Self-check: seeded state must validate against the registered schemas.
    try:
        for name_ in ("project.json", "phases.json", "steps.json", "decisions.json"):
            v.validate_state_file(state_dir / name_)
        v.validate_devlog_jsonl(devlog)
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
    # line reflects the run-relevant backend (claude when both are scaffolded).
    run_backend = "claude" if "claude" in backends else backends[0]
    toml_body = _packaged_text("templates/i2c.toml").replace("[Backend]", run_backend)
    _write_text(root, root / "i2c.toml", toml_body, report, force=force)

    _ensure_gitignore(root, report)
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
    token ``instructions`` (all four). Raises ``ScaffoldError`` on an unknown
    asset or when a local copy exists and ``force`` is not set.
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
