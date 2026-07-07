"""i2c.dashboard — emit a self-contained HTML snapshot (DESIGN_dashboard_v1.md
§5, §10; v0, tables-only).

The generator half of the frozen-shell / bound-data split (D-dash-6..10): the
visual design is a human-owned, **frozen** shell shipped as package data
(``i2c/data/dashboard/{shell.html,style.css,bind.js}``) — Pico.css (classless)
makes semantic HTML look decent with zero design work. This module never
rewrites the shell; it only **binds** the allowlisted ``control.DashboardModel``
into it (JSON -> DOM via ``bind.js``) and inlines CSS + data + script into one
offline ``dashboard.html`` with **no external asset references** (self-contained,
works over the shared disk; D-dash-8).

Charts + the telemetry aggregator are deferred to v0.1; v0 renders tables/text.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from i2c.assemble_context import packaged_data_dir
from i2c.control import DashboardModel

_ASSET_SUBDIR = "dashboard"
_STYLE_TOKEN = "__I2C_STYLE__"
_DATA_TOKEN = "__I2C_DATA__"
_BIND_TOKEN = "__I2C_BIND__"


def packaged_asset(name: str) -> str:
    """Read a packaged dashboard shell asset (LF-normalized).

    Packaged-only — the shell is framework-frozen (D-dash-6), so there is no
    project-local override (unlike ``assemble_context.resolve_asset``). Mirrors
    the ``scaffold._packaged_text`` idiom.
    """
    text = (packaged_data_dir() / _ASSET_SUBDIR / name).read_text(encoding="utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _inline_style() -> str:
    """The vendored Pico.css, ready to inline in a ``<style>``. A leading
    ``@charset`` rule is only valid at the very start of an *external*
    stylesheet — inside an inline ``<style>`` it is ignored (and warns), so
    strip it."""
    css = packaged_asset("style.css")
    if css.startswith("@charset"):
        _, _, css = css.partition(";")
    return css.lstrip("\n")


def render_html(model: DashboardModel) -> str:
    """Inline the frozen shell + vendored CSS + bound model into one
    self-contained HTML string (LF newlines)."""
    shell = packaged_asset("shell.html")
    bind = packaged_asset("bind.js")

    payload = json.dumps(asdict(model), ensure_ascii=False)
    # Defensively prevent a literal "</script>" inside any state string from
    # closing the data <script> early; "<\/" is equivalent JS/JSON.
    payload = payload.replace("</", "<\\/")
    data = "window.__I2C__ = " + payload + ";"

    html = (
        shell.replace(_STYLE_TOKEN, _inline_style())
        .replace(_DATA_TOKEN, data)
        .replace(_BIND_TOKEN, bind)
    )
    return html if html.endswith("\n") else html + "\n"


def write_html(model: DashboardModel, out_path: Path) -> Path:
    """Render and write ``dashboard.html`` (LF newline). Returns the path."""
    out_path = Path(out_path)
    out_path.write_text(render_html(model), encoding="utf-8", newline="\n")
    return out_path
