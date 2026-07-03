"""Tests for i2c/render.py — followup renderers (Proposal A step 3)."""

from __future__ import annotations

import unittest

from i2c import control, render


def _fu(**overrides) -> control.FollowupView:
    base = {"id": "FU-1", "title": "prose pass", "kind": "prose", "status": "open"}
    base.update(overrides)
    return control.FollowupView(**base)


class TestRenderFollowupsList(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(render._render_followups([]), "(no follow-ups)")

    def test_lists_ids_and_fields(self):
        out = render._render_followups([_fu(), _fu(id="FU-2", kind="dead-surface")])
        self.assertIn("FU-1", out)
        self.assertIn("FU-2", out)
        self.assertIn("prose", out)

    def test_includes_trigger_when_present(self):
        out = render._render_followups([_fu(trigger="next adapter touch")])
        self.assertIn("next adapter touch", out)

    def test_includes_priority_when_present(self):
        out = render._render_followups([_fu(priority="immediate")])
        self.assertIn("immediate", out)


class TestRenderFollowupsTables(unittest.TestCase):
    def _tables(self):
        return render._render_followups_tables([
            _fu(id="FU-1", status="open", title="prose pass"),
            _fu(id="FU-2", status="accepted", kind="cli-ergonomics"),
            _fu(id="FU-3", status="closed", kind="dead-surface",
                resolution="removed dead flag"),
            _fu(id="FU-4", status="wontfix", resolution="declined"),
        ])

    def test_open_section_has_open_items(self):
        out = self._tables()
        self.assertIn("## Follow-ups (open)", out)
        self.assertIn("| FU-1 |", out)
        self.assertIn("| FU-2 |", out)

    def test_closed_section_has_closed_items(self):
        out = self._tables()
        self.assertIn("## Closed / decided", out)
        self.assertIn("| FU-3 |", out)
        self.assertIn("removed dead flag", out)
        # wontfix counts as closed too
        self.assertIn("| FU-4 |", out)

    def test_open_and_closed_partitioned(self):
        out = self._tables()
        open_part, closed_part = out.split("## Closed / decided")
        self.assertIn("FU-1", open_part)
        self.assertNotIn("FU-3", open_part)
        self.assertIn("FU-3", closed_part)
        self.assertNotIn("FU-1", closed_part)

    def test_pipe_in_cell_escaped(self):
        out = render._render_followups_tables([
            _fu(title="a | b", trigger="x | y"),
        ])
        self.assertIn("a \\| b", out)
        self.assertIn("x \\| y", out)

    def test_empty_sections_render_none(self):
        out = render._render_followups_tables([])
        # Both sections present with an explicit (none) marker.
        self.assertEqual(out.count("_(none)_"), 2)


if __name__ == "__main__":
    unittest.main()
