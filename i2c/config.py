"""i2c project configuration — ``i2c.toml`` (§5.5).

A project records its default `i2c run` settings once in an ``i2c.toml`` at the
project root, rather than re-typing flags. Precedence is **CLI flag > i2c.toml >
built-in default** (the resolution lives in ``cli.cmd_run``; this module only
reads and validates the file).

Only the ``[run]`` table is read today (``backend`` / ``model`` /
``max_budget_usd``). Unknown keys are ignored for forward-compatibility.
Secrets / API keys do **not** belong here — use environment variables.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

CONFIG_FILENAME = "i2c.toml"
_BACKENDS = ("claude", "codex")


class ConfigError(Exception):
    """``i2c.toml`` is malformed or holds an invalid value."""


@dataclass
class RunConfig:
    """Defaults for ``i2c run`` read from ``[run]``. ``None`` = unset."""

    backend: str | None = None
    model: str | None = None
    max_budget_usd: float | None = None


def _find_config(start: Path) -> Path | None:
    """Walk up from ``start`` for ``i2c.toml``. Independent of ``.state`` so
    config discovery doesn't depend on project-root detection."""
    cur = start.absolute()
    for candidate in [cur, *cur.parents]:
        path = candidate / CONFIG_FILENAME
        if path.is_file():
            return path
    return None


def load_run_config(start: Path | None = None) -> RunConfig:
    """Load the ``[run]`` defaults from the nearest ``i2c.toml`` (or empty).

    Raises ``ConfigError`` on a parse failure or an invalid value.
    """
    path = _find_config(start or Path.cwd())
    if path is None:
        return RunConfig()

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as e:
        raise ConfigError(f"{path}: could not parse: {e}") from e

    run = data.get("run", {})
    if not isinstance(run, dict):
        raise ConfigError(f"{path}: [run] must be a table")

    backend = run.get("backend")
    if backend is not None and backend not in _BACKENDS:
        raise ConfigError(
            f"{path}: [run].backend is {backend!r}; expected one of {_BACKENDS}"
        )

    model = run.get("model")
    if model is not None and not isinstance(model, str):
        raise ConfigError(f"{path}: [run].model must be a string")

    budget = run.get("max_budget_usd")
    if budget is not None:
        if isinstance(budget, bool) or not isinstance(budget, (int, float)):
            raise ConfigError(f"{path}: [run].max_budget_usd must be a number")
        budget = float(budget)

    return RunConfig(backend=backend, model=model, max_budget_usd=budget)
