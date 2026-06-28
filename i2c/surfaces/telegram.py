"""python-telegram-bot wiring for the i2c Telegram surface (the ``telegram``
extra). Deliberately thin: each command handler computes ``is_admin`` from the
sender, calls ``telegram_core.dispatch`` off the event loop, and sends the
``Reply``. All command logic lives in ``telegram_core`` (tested without this
dependency).

The transport library is imported lazily (``_require_ptb``) so the module is
importable — and ``i2c serve``'s "install the extra" message works — without
``python-telegram-bot`` present. The bot token comes only from the
``I2C_TELEGRAM_TOKEN`` environment variable; the admin allowlist + portfolio
root come from ``i2c.toml`` ``[telegram]``.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path

from i2c import config
from i2c.surfaces import telegram_core as tc

TOKEN_ENV = "I2C_TELEGRAM_TOKEN"
_TG_LIMIT = 4000  # Telegram hard-caps messages at 4096 chars; stay under it.


class MissingDependency(RuntimeError):
    """The ``telegram`` extra is not installed."""


class MissingToken(RuntimeError):
    """No bot token in the environment."""


def _require_ptb():
    try:
        from telegram.ext import Application, CommandHandler
    except ModuleNotFoundError as e:  # pragma: no cover - depends on install
        raise MissingDependency(
            "The Telegram surface needs the optional dependency: "
            "pip install i2c[telegram]"
        ) from e
    return Application, CommandHandler


class _ChatState:
    """Per-chat current project, persisted as a small JSON file. Operational
    state only — entirely separate from any project ``.state/``."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, str] = {}
        if path.is_file():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self._data = {}

    def get(self, chat_id: int) -> str | None:
        return self._data.get(str(chat_id))

    def set(self, chat_id: int, name: str) -> None:
        self._data[str(chat_id)] = name
        try:
            self.path.write_text(json.dumps(self._data), encoding="utf-8")
        except OSError:  # pragma: no cover - best-effort persistence
            pass


def _make_runner(root: Path):
    """Return ``fn(proj, backend=None) -> int`` that runs one worker iteration.

    Shells ``i2c run`` with the project as CWD because ``run_iteration``
    resolves its project from the working directory; this also gives process
    isolation for the long-running worker. Model/budget and the per-action
    backend map come from the project's ``i2c.toml`` (read by ``cmd_run``); a
    non-None ``backend`` forces a single backend for that invocation via
    ``--backend``."""
    def _run(proj: Path, backend: str | None = None) -> int:
        cmd = [sys.executable, "-m", "i2c.cli", "run"]
        if backend:
            cmd += ["--backend", backend]
        return subprocess.run(cmd, cwd=str(proj)).returncode

    return _run


async def _send(update, reply: tc.Reply) -> None:
    text = reply.text or "(no output)"
    for i in range(0, len(text), _TG_LIMIT):
        await update.message.reply_text(text[i:i + _TG_LIMIT])
    if reply.document:
        buf = BytesIO(reply.document.encode("utf-8"))
        buf.name = "transcript.txt"
        await update.message.reply_document(buf)


def build_application(token: str, root: Path, admins: frozenset[int], state_path: Path):
    """Build the python-telegram-bot Application with one handler per command."""
    Application, CommandHandler = _require_ptb()
    app = Application.builder().token(token).build()
    chat_state = _ChatState(state_path)
    runner = _make_runner(root)

    def make_handler(command: str):
        async def handler(update, context):
            user = update.effective_user
            is_admin = bool(user and user.id in admins)
            chat_id = update.effective_chat.id
            args = list(context.args or [])
            current = chat_state.get(chat_id)
            reply = await asyncio.to_thread(
                tc.dispatch,
                command,
                args,
                is_admin=is_admin,
                root=root,
                current=current,
                run_iteration_fn=runner,
            )
            if reply.set_current:
                chat_state.set(chat_id, reply.set_current)
            await _send(update, reply)

        return handler

    for command in sorted(tc.ALL_COMMANDS):
        app.add_handler(CommandHandler(command, make_handler(command)))
    return app


def serve(token: str | None = None, root: Path | None = None) -> int:
    """Resolve config + token and run the bot (blocking long-poll loop)."""
    token = token or os.environ.get(TOKEN_ENV)
    if not token:
        raise MissingToken(f"Set {TOKEN_ENV} to your bot token.")
    root = (root or Path.cwd()).absolute()
    tg_cfg = config.load_telegram_config(root)
    if tg_cfg.root:
        root = Path(tg_cfg.root).absolute()
    admins = frozenset(tg_cfg.admins)
    state_path = root / ".i2c-telegram.json"
    app = build_application(token, root, admins, state_path)
    app.run_polling()
    return 0
