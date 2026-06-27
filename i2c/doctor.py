"""i2c doctor — environment + install self-check.

Verifies the things the toolkit rollout proved easy to get wrong:

- the ``i2c`` console command is on ``PATH`` — load-bearing for autonomous
  runs, because the worker invokes bare ``i2c`` (e.g. ``i2c state ...``);
- crucially, ``i2c`` resolves in a ``bash -lc`` **login shell** — the
  environment the autonomous worker actually executes commands in (a login
  shell rebuilds PATH from profile, so a ``~/.local/bin`` ``--user`` install
  can pass the plain PATH check yet fail for the worker);
- the runtime dependency (``jsonschema``) is importable;
- the packaged JSON Schemas resolve from package data;
- an ``i2c.toml`` parser is available (stdlib ``tomllib`` or the ``tomli``
  backport on Python < 3.11);
- a backend CLI exists (advisory — required only for ``i2c run``);
- the current project's ``.state/`` validates, when run inside a project.

Each check yields ``ok`` / ``warn`` / ``fail``. Exit policy (applied by the
CLI): any ``fail`` → exit 1; ``warn``/``ok`` only → exit 0.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from i2c import validate as v

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    remedy: str = ""


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    def ok(self) -> bool:
        """True when no check failed (warnings do not fail the report)."""
        return all(c.status != FAIL for c in self.checks)


def _check_version() -> Check:
    from i2c.migrate import CURRENT_SCHEMA_VERSION

    try:
        from importlib.metadata import version

        ver = version("i2c")
    except Exception:  # pragma: no cover - metadata edge cases
        ver = "unknown"
    return Check(
        "i2c", OK, f"version {ver}, current schema v{CURRENT_SCHEMA_VERSION}"
    )


def _check_path() -> Check:
    resolved = shutil.which("i2c")
    if resolved:
        return Check("i2c on PATH", OK, resolved)
    return Check(
        "i2c on PATH",
        FAIL,
        "`i2c` is not resolvable as a command",
        remedy=(
            "Run `pipx ensurepath` (if installed via pipx), or add your Python "
            "scripts directory to PATH. Autonomous workers call bare `i2c` and "
            "fail without it; `python -m i2c.cli` is an operator-only fallback."
        ),
    )


def _check_login_shell_path() -> Check:
    """Verify `i2c` resolves in a `bash -lc` login shell.

    The autonomous worker runs every command via `bash -lc` (a login shell
    that rebuilds PATH from /etc/profile + ~/.profile, discarding the parent
    process env). A `--user` install in ~/.local/bin is often NOT on that PATH
    even when `shutil.which` finds it in the operator's env, so the plain PATH
    check can pass while the worker fails. This check simulates the worker's
    environment. POSIX-only; skipped on Windows and when no `bash` is present.
    """
    if sys.platform.startswith("win"):
        return Check(
            "i2c on login-shell PATH",
            OK,
            "skipped on Windows (autonomous workers run on POSIX hosts)",
        )
    bash = shutil.which("bash")
    if not bash:
        return Check(
            "i2c on login-shell PATH",
            OK,
            "no POSIX bash found; skipped (autonomous workers run on POSIX hosts)",
        )
    try:
        proc = subprocess.run(
            [bash, "-lc", "command -v i2c"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as e:  # pragma: no cover
        return Check(
            "i2c on login-shell PATH",
            WARN,
            f"login-shell probe failed to run: {e}",
        )
    resolved = proc.stdout.strip()
    if proc.returncode == 0 and resolved:
        return Check("i2c on login-shell PATH", OK, resolved)
    return Check(
        "i2c on login-shell PATH",
        FAIL,
        "`i2c` does not resolve in a `bash -lc` login shell",
        remedy=(
            "The autonomous worker runs commands via `bash -lc`, which rebuilds "
            "PATH from profile and ignores the parent env — a ~/.local/bin "
            "(--user) install is typically not on it. Install with pipx "
            "(`pipx ensurepath`), do a system install, or symlink i2c into "
            "/usr/local/bin."
        ),
    )


def _check_jsonschema() -> Check:
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        return Check(
            "jsonschema",
            FAIL,
            "not importable",
            remedy="Reinstall i2c — jsonschema is a required runtime dependency.",
        )
    try:
        from importlib.metadata import version

        ver = version("jsonschema")
    except Exception:  # pragma: no cover - metadata edge cases
        ver = "unknown"
    return Check("jsonschema", OK, f"version {ver}")


def _check_schemas() -> Check:
    try:
        names = set(v.SCHEMA_BY_FILENAME.values()) | {
            v.DEVLOG_ENTRY_SCHEMA,
            v.EXIT_SIGNAL_SCHEMA,
        }
        for name in sorted(names):
            v.load_schema(name)
        return Check(
            "packaged schemas",
            OK,
            f"{len(names)} schemas resolve from {v.schemas_dir()}",
        )
    except Exception as e:  # pragma: no cover - exercised via monkeypatch
        return Check(
            "packaged schemas",
            FAIL,
            str(e),
            remedy="Reinstall i2c; bundled package data appears to be missing.",
        )


def _check_toml() -> Check:
    if sys.version_info >= (3, 11):
        return Check("toml parser", OK, "stdlib tomllib")
    try:
        import tomli  # noqa: F401

        return Check("toml parser", OK, "tomli backport")
    except ImportError:
        return Check(
            "toml parser",
            WARN,
            "tomli missing on Python < 3.11",
            remedy="pip/pipx install tomli — needed to read i2c.toml on Python < 3.11.",
        )


def _check_backends() -> Check:
    claude = shutil.which("claude")
    codex = shutil.which("codex")
    detail = f"claude: {claude or 'not found'}; codex: {codex or 'not found'}"
    if claude or codex:
        return Check("backend CLI", OK, detail)
    return Check(
        "backend CLI",
        WARN,
        detail,
        remedy=(
            "No backend CLI found. Required only for autonomous runs "
            "(`i2c run`); supervised mode needs none."
        ),
    )


def _check_project() -> Check:
    from i2c import control, migrate

    try:
        root = control.find_project_root()
    except control.NotFoundError:
        return Check(
            "project .state",
            OK,
            "not inside an i2c project (no .state/); skipped",
        )
    state_dir = root / ".state"
    try:
        for name in ("project.json", "phases.json", "steps.json", "decisions.json"):
            v.validate_state_file(state_dir / name)
        v.validate_devlog_jsonl(state_dir / "devlog.jsonl")
    except Exception as e:
        return Check(
            "project .state",
            FAIL,
            f"{root}: {e}",
            remedy="Fix or re-create the invalid .state/ file.",
        )
    try:
        result = migrate.migrate_project(root, dry_run=True)
    except migrate.MigrationError as e:
        return Check(
            "project .state",
            FAIL,
            f"{root}: {e}",
            remedy="Upgrade i2c; this project targets a newer schema.",
        )
    if result.migrated:
        return Check(
            "project .state",
            WARN,
            f"{root}: migration needed (schema v{result.from_version} -> "
            f"v{result.to_version})",
            remedy="Run `i2c migrate`.",
        )
    return Check(
        "project .state", OK, f"{root}: valid, schema v{result.to_version}"
    )


def run_checks() -> DoctorReport:
    """Run all environment/install checks and return a structured report."""
    return DoctorReport(
        checks=[
            _check_version(),
            _check_path(),
            _check_login_shell_path(),
            _check_jsonschema(),
            _check_schemas(),
            _check_toml(),
            _check_backends(),
            _check_project(),
        ]
    )
