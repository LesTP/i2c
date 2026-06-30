"""i2c project configuration — ``i2c.toml`` (§5.5).

A project records its default `i2c run` settings once in an ``i2c.toml`` at the
project root, rather than re-typing flags. Precedence is **CLI flag > i2c.toml >
built-in default** (the resolution lives in ``cli.cmd_run``; this module only
reads and validates the file).

Only the ``[run]`` and ``[telegram]`` tables are read today. Unknown keys are
ignored for forward-compatibility. Secrets / API keys (the bot token, provider
keys) do **not** belong here — use environment variables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

CONFIG_FILENAME = "i2c.toml"
_BACKENDS = ("claude", "codex")
# Lifecycle actions plus the out-of-band recovery actions; [run.backends] may
# map a backend for any of them (e.g. diagnose = claude).
_RUN_ACTIONS = ("plan", "execute", "review", "close", "diagnose", "reconcile")


class ConfigError(Exception):
    """``i2c.toml`` is malformed or holds an invalid value."""


@dataclass
class RunConfig:
    """Defaults for ``i2c run`` read from ``[run]``. ``None`` = unset."""

    backend: str | None = None
    model: str | None = None
    max_budget_usd: float | None = None
    backends: dict[str, str] = field(default_factory=dict)
    """Optional per-action backend overrides from ``[run.backends]`` — maps a
    worker action (plan/execute/review/close) to a backend. Empty when unset."""


@dataclass
class TelegramConfig:
    """Non-secret settings for the Telegram surface, read from ``[telegram]``.
    The bot token is **not** here — it comes from the ``I2C_TELEGRAM_TOKEN``
    environment variable. ``admins`` is the allowlist of Telegram user IDs
    permitted to issue mutating commands; ``root`` is the portfolio folder the
    bot scans (default: the bot's working directory)."""

    admins: tuple[int, ...] = ()
    root: str | None = None


@dataclass
class TelemetryConfig:
    """Settings for the telemetry sidecar, read from ``[telemetry]``.

    ``test_cmd`` is the opt-in tests oracle: a shell command the runner executes
    in the project root after each worker invocation to record ``tests_pass``;
    unset means the oracle is off. ``pricing`` holds optional per-model rate
    overrides from ``[telemetry.pricing]`` that layer over the bundled pricing
    table used to derive ``cost_usd`` / ``tier``."""

    test_cmd: str | None = None
    pricing: dict = field(default_factory=dict)


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

    backends_raw = run.get("backends", {})
    if not isinstance(backends_raw, dict):
        raise ConfigError(f"{path}: [run.backends] must be a table")
    backends: dict[str, str] = {}
    for action, be in backends_raw.items():
        if action not in _RUN_ACTIONS:
            raise ConfigError(
                f"{path}: [run.backends] key {action!r} is not a valid action; "
                f"expected one of {_RUN_ACTIONS}"
            )
        if be not in _BACKENDS:
            raise ConfigError(
                f"{path}: [run.backends].{action} is {be!r}; "
                f"expected one of {_BACKENDS}"
            )
        backends[action] = be

    return RunConfig(
        backend=backend, model=model, max_budget_usd=budget, backends=backends
    )


def load_telegram_config(start: Path | None = None) -> TelegramConfig:
    """Load the ``[telegram]`` settings from the nearest ``i2c.toml`` (or empty).

    Raises ``ConfigError`` on a parse failure or an invalid value. The bot token
    is never read from here — only from the environment.
    """
    path = _find_config(start or Path.cwd())
    if path is None:
        return TelegramConfig()

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as e:
        raise ConfigError(f"{path}: could not parse: {e}") from e

    tg = data.get("telegram", {})
    if not isinstance(tg, dict):
        raise ConfigError(f"{path}: [telegram] must be a table")

    admins_raw = tg.get("admins", [])
    if not isinstance(admins_raw, list) or any(
        isinstance(a, bool) or not isinstance(a, int) for a in admins_raw
    ):
        raise ConfigError(
            f"{path}: [telegram].admins must be a list of integer Telegram user IDs"
        )

    root = tg.get("root")
    if root is not None and not isinstance(root, str):
        raise ConfigError(f"{path}: [telegram].root must be a string")

    return TelegramConfig(admins=tuple(admins_raw), root=root)


def load_telemetry_config(start: Path | None = None) -> TelemetryConfig:
    """Load the ``[telemetry]`` settings from the nearest ``i2c.toml`` (or empty).

    Raises ``ConfigError`` on a parse failure or an invalid value. Callers that
    must never fail (the runner) catch ``ConfigError`` and fall back to an empty
    config so a malformed file degrades telemetry rather than breaking a run.
    """
    path = _find_config(start or Path.cwd())
    if path is None:
        return TelemetryConfig()

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as e:
        raise ConfigError(f"{path}: could not parse: {e}") from e

    tele = data.get("telemetry", {})
    if not isinstance(tele, dict):
        raise ConfigError(f"{path}: [telemetry] must be a table")

    test_cmd = tele.get("test_cmd")
    if test_cmd is not None and not isinstance(test_cmd, str):
        raise ConfigError(f"{path}: [telemetry].test_cmd must be a string")

    pricing_raw = tele.get("pricing", {})
    if not isinstance(pricing_raw, dict):
        raise ConfigError(f"{path}: [telemetry.pricing] must be a table")
    pricing: dict = {}
    for model, entry in pricing_raw.items():
        if not isinstance(entry, dict):
            raise ConfigError(
                f"{path}: [telemetry.pricing].{model} must be a table of rate fields"
            )
        pricing[model] = entry

    return TelemetryConfig(test_cmd=test_cmd, pricing=pricing)
