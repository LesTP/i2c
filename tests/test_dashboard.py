"""Tests for i2c.dashboard — the self-contained HTML emitter (v0).

Two layers, per the design's Build/Refine split:
- ``render_html`` is a **smoke test** (self-containment + structure), not a
  byte-golden: the full HTML embeds ``doctor`` output + env-variant
  ``run_config``, which vary per machine.
- The **state-derived** slice of the model (``project``) IS byte-goldable, so
  it gets an ``I2C_REGEN_GOLDEN`` golden — excluding the ``health`` and
  ``run_config`` fields, which are environment-dependent.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from dataclasses import asdict
from pathlib import Path

from i2c import control, dashboard

I2C_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = I2C_ROOT / "examples" / "initial_state"
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
REGEN = os.environ.get("I2C_REGEN_GOLDEN") == "1"


def _assert_golden(test: unittest.TestCase, name: str, content: str) -> None:
    GOLDEN_DIR.mkdir(exist_ok=True)
    path = GOLDEN_DIR / name
    if REGEN or not path.exists():
        path.write_text(content, encoding="utf-8", newline="\n")
    expected = path.read_text(encoding="utf-8")
    test.assertEqual(
        content, expected,
        msg=f"dashboard model bytes changed vs golden {name}; "
        "re-run with I2C_REGEN_GOLDEN=1 if intended.",
    )


class TestRenderHtmlSelfContained(unittest.TestCase):
    def setUp(self):
        self.html = dashboard.render_html(control.dashboard_model(FIXTURE))

    def test_no_leftover_placeholder_tokens(self):
        for token in ("__I2C_STYLE__", "__I2C_DATA__", "__I2C_BIND__"):
            self.assertNotIn(token, self.html)

    def test_has_inlined_model(self):
        self.assertIn("window.__I2C__", self.html)

    def test_has_mount_point_anchors(self):
        self.assertIn('id="portfolio"', self.html)
        self.assertIn('id="project"', self.html)
        self.assertIn('id="health"', self.html)

    def test_no_external_asset_references(self):
        # Self-contained/offline (D-dash-8): no network-loaded assets. Inline
        # data: URIs and the vendored license comment are fine; external
        # src=/href=/url()/@import to http(s) are not.
        self.assertNotIn("src=", self.html)
        self.assertFalse(re.search(r'href="https?://', self.html))
        self.assertFalse(re.search(r'url\(\s*["\']?https?://', self.html))
        self.assertNotIn("@import", self.html)

    def test_lf_only(self):
        self.assertNotIn("\r", self.html)


class TestModelGolden(unittest.TestCase):
    def test_project_slice_byte_golden(self):
        model = control.dashboard_model(FIXTURE)
        # Only the state-derived slice is deterministic across machines.
        slice_ = asdict(model.project)
        content = json.dumps(slice_, indent=2, ensure_ascii=False) + "\n"
        _assert_golden(self, "dashboard_project_model.json", content)


if __name__ == "__main__":
    unittest.main()
