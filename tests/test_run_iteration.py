"""Tests for tools/run_iteration.py — single-iteration runner.

Mocks the ``claude -p`` subprocess via a `claude_invoker` seam so the
tests are deterministic and fast. The state_machine + assembler
subprocess calls are real — they're cheap and exercising them end-to-end
catches integration bugs that a pure-mock setup would miss.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = I2C_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import run_iteration as ri  # noqa: E402

FIXTURE = I2C_ROOT / "examples" / "initial_state"


# ---------------------------------------------------------------------------
# Test fixture: copy initial_state + framework files into a temp project.
# ---------------------------------------------------------------------------


class TempProject:
    """Copy initial_state + WORKER_SPEC + adapter + instructions/ into a temp root.

    Mirrors test_assemble_context.TempProject's `with_framework=True` shape
    since the runner shells out to ``assemble_context.py`` which needs
    those files. Module contract for the fixture's phase-2 module
    (``event_store``) is also synthesized so the assembler doesn't bail.
    """

    def __init__(self):
        self._tmp = None
        self.root: Path | None = None

    def __enter__(self) -> "TempProject":
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_run_")
        self.root = Path(self._tmp.name) / "project"
        shutil.copytree(FIXTURE, self.root)
        for name in ("WORKER_SPEC.md", "CLAUDE.md", "CODEX.md"):
            shutil.copy2(I2C_ROOT / name, self.root / name)
        shutil.copytree(I2C_ROOT / "instructions", self.root / "instructions")
        # Stub ARCH_event_store.md to satisfy the module-contract requirement
        # for the fixture's current phase. Content is irrelevant to runner
        # tests; the assembler just needs the file to exist.
        (self.root / "ARCH_event_store.md").write_text(
            "# ARCH event_store\n\n## Contract\n\nStub for tests.\n",
            encoding="utf-8",
        )
        self._prev_cwd = Path.cwd()
        os.chdir(self.root)
        return self

    def __exit__(self, *args):
        os.chdir(self._prev_cwd)
        self._tmp.cleanup()

    # --- mutators ----------------------------------------------------------

    def patch_project(self, **kwargs) -> None:
        path = self.root / ".state" / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(kwargs)
        # Defensive: 'blocked' was dropped per DESIGN_state_lifecycle_v1;
        # schema rejects it. Strip in case a test accidentally passes it.
        data.pop("blocked", None)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def patch_phase_status(self, phase_id: int, status: str) -> None:
        path = self.root / ".state" / "phases.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for p in data:
            if p["id"] == phase_id:
                p["status"] = status
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Synthetic exit signals (2-line block)
# ---------------------------------------------------------------------------


def signal_block(
    *,
    exit_code: int = 0,
    reason: str = "did the thing",
) -> str:
    return (
        "I did some work.\n"
        "\n"
        f"EXIT: {exit_code}\n"
        f"REASON: {reason}\n"
    )


# ---------------------------------------------------------------------------
# Fake claude invokers (the test seam)
# ---------------------------------------------------------------------------


def make_fake_invoker(captured: str, rc: int = 0, *, capture=None):
    """Return a callable shaped like invoke_claude that emits `captured`.

    The optional `capture` list collects (cwd, prompt, model, budget) tuples
    so tests can assert what the runner sent to the worker.
    """
    def fake(prompt, *, cwd, model, max_budget_usd):
        if capture is not None:
            capture.append({
                "cwd": cwd, "prompt": prompt, "model": model,
                "max_budget_usd": max_budget_usd,
            })
        return rc, captured
    return fake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_iter(*, backend="claude", invoker=None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = ri.run_iteration(
                backend=backend,
                model="sonnet",
                max_budget_usd=5.00,
                claude_invoker=invoker or make_fake_invoker(signal_block()),
            )
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


def read_summary(root: Path) -> str:
    summary = root / "logs" / "loop" / "summary.log"
    if not summary.is_file():
        return ""
    return summary.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStateMachineExit(unittest.TestCase):
    """When state_machine dispatches EXIT, no worker invocation happens."""

    def test_audit_boundary_short_circuits_to_exit_zero(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary")
            calls = []
            invoker = make_fake_invoker(signal_block(), capture=calls)
            rc, out, err = run_iter(invoker=invoker)
            self.assertEqual(rc, 0, msg=err)
            # claude was NOT invoked.
            self.assertEqual(len(calls), 0)
            # summary.log has an iter=1 EXIT line.
            summary = read_summary(p.root)
            self.assertIn("iter=1", summary)
            self.assertIn("action=EXIT", summary)
            self.assertIn("exit=0", summary)


class TestHappyPath(unittest.TestCase):
    """Fixture is phase=2 execute with 2 pending steps → EXECUTE/execute."""

    def test_runs_end_to_end_and_writes_logs(self):
        with TempProject() as p:
            calls = []
            invoker = make_fake_invoker(signal_block(), capture=calls)
            rc, out, err = run_iter(invoker=invoker)
            self.assertEqual(rc, 0, msg=err)
            # claude was invoked exactly once.
            self.assertEqual(len(calls), 1)
            # Prompt is non-trivial; it should at least contain a banner.
            self.assertIn("WORKER CONTRACT", calls[0]["prompt"])
            self.assertIn("ACTION CONTEXT", calls[0]["prompt"])
            # Logs were written.
            log_dir = p.root / "logs" / "loop"
            self.assertTrue((log_dir / "iteration_001_prompt.md").is_file())
            self.assertTrue((log_dir / "iteration_001.txt").is_file())
            summary = read_summary(p.root)
            self.assertIn("iter=1", summary)
            self.assertIn("action=EXECUTE", summary)
            self.assertIn("exit=0", summary)
            self.assertIn("did the thing", summary)

    def test_iteration_counter_increments(self):
        with TempProject() as p:
            invoker = make_fake_invoker(signal_block())
            rc1, _, _ = run_iter(invoker=invoker)
            self.assertEqual(rc1, 0)
            rc2, _, _ = run_iter(invoker=invoker)
            self.assertEqual(rc2, 0)
            log_dir = p.root / "logs" / "loop"
            self.assertTrue((log_dir / "iteration_001_prompt.md").is_file())
            self.assertTrue((log_dir / "iteration_002_prompt.md").is_file())


class TestExitSignalParsing(unittest.TestCase):
    def test_missing_signal_treated_as_exit_2(self):
        with TempProject() as p:
            invoker = make_fake_invoker("No signal here, sorry.\n", rc=0)
            rc, out, err = run_iter(invoker=invoker)
            self.assertEqual(rc, 2)
            summary = read_summary(p.root)
            self.assertIn("exit=2", summary)
            self.assertIn("exit signal missing", summary)

    def test_worker_exit_2_propagates(self):
        with TempProject() as p:
            invoker = make_fake_invoker(
                signal_block(exit_code=2, reason="escalating")
            )
            rc, _, _ = run_iter(invoker=invoker)
            self.assertEqual(rc, 2)
            self.assertIn("exit=2", read_summary(p.root))

    def test_parse_exit_signal_pure_helper(self):
        # Sanity: pure-function parser extracts the two fields.
        text = signal_block(exit_code=2, reason="phase escalated")
        signal = ri.parse_exit_signal(text)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["exit_code"], 2)
        self.assertEqual(signal["reason"], "phase escalated")
        # No legacy fields leak through.
        self.assertNotIn("action_type", signal)
        self.assertNotIn("action_id", signal)
        self.assertNotIn("steps_completed", signal)
        self.assertNotIn("next_action", signal)

    def test_parse_exit_signal_rejects_legacy_exit_code_one(self):
        # exit_code 1 was the pre-lifecycle-v1 "blocked on entry" value;
        # the state machine now short-circuits halt states before dispatch,
        # so the worker never has a reason to emit 1. Schema enum is {0, 2}.
        text = "EXIT: 1\nREASON: stale halt-on-entry meaning\n"
        signal = ri.parse_exit_signal(text)
        # Parser is strict (regex only matches 0 or 2).
        self.assertIsNone(signal)

    def test_parse_exit_signal_returns_none_when_missing(self):
        self.assertIsNone(ri.parse_exit_signal("nothing relevant"))


class TestCloseInvariantHalt(unittest.TestCase):
    """A CLOSE worker that doesn't set state=audit_boundary must trip FU-22."""

    def test_close_without_audit_boundary_halts_with_invariant_error(self):
        with TempProject() as p:
            # Drive state to "close" + phase 2 still pending, so the
            # state machine dispatches CLOSE.
            p.patch_project(state="close")
            # Worker emits a clean EXIT 0 signal but DOESN'T update
            # state to audit_boundary or mark phase complete. Runner must
            # detect via invariants.
            invoker = make_fake_invoker(
                signal_block(exit_code=0, reason="closed it")
            )
            rc, out, err = run_iter(invoker=invoker)
            self.assertEqual(rc, 2, msg=err)
            self.assertIn("post-CLOSE invariants failed", err)
            summary = read_summary(p.root)
            self.assertIn("exit=2", summary)
            self.assertIn("post-CLOSE invariants failed", summary)

    def test_close_with_proper_invariants_passes(self):
        with TempProject() as p:
            # Pre-stage the post-CLOSE state correctly: state=audit_boundary,
            # phase 2 complete. The fake worker doesn't actually call state.py,
            # so we patch the post-condition directly to simulate a worker
            # that did its job.
            p.patch_project(state="audit_boundary", phase=2)
            p.patch_phase_status(2, "complete")
            invoker = make_fake_invoker(
                signal_block(exit_code=0, reason="closed it cleanly")
            )
            rc, _, err = run_iter(invoker=invoker)
            self.assertEqual(rc, 0, msg=err)


class TestSummaryLog(unittest.TestCase):
    def test_summary_line_format(self):
        with TempProject() as p:
            invoker = make_fake_invoker(
                signal_block(exit_code=0, reason="ok")
            )
            run_iter(invoker=invoker)
            summary = read_summary(p.root).strip().splitlines()[-1]
            # Expected components per the plan:
            # YYYY-MM-DDTHH:MM:SS+00:00 | iter=N | backend=claude | action=X | exit=N | reason="..."
            self.assertIn("| iter=1 ", summary)
            self.assertIn("| backend=claude ", summary)
            self.assertIn("| action=EXECUTE ", summary)
            self.assertIn("| exit=0 ", summary)
            self.assertIn('reason="ok"', summary)


# ---------------------------------------------------------------------------
# FU-33: per-iter token telemetry in summary.log
# ---------------------------------------------------------------------------


class TestParseClaudeOutput(unittest.TestCase):
    def test_extracts_result_and_usage_from_json(self):
        raw = json.dumps({
            "type": "result",
            "result": "the prose with the 2-line exit signal",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 800,
                "cache_creation_input_tokens": 200,
            },
        })
        text, usage = ri.parse_claude_output(raw)
        self.assertEqual(text, "the prose with the 2-line exit signal")
        # gross input = fresh + cache_read + cache_creation = 100 + 800 + 200
        self.assertEqual(usage, {"input": 1100, "output": 50, "cached": 800})

    def test_returns_text_only_when_usage_missing(self):
        raw = json.dumps({"type": "result", "result": "no usage field here"})
        text, usage = ri.parse_claude_output(raw)
        self.assertEqual(text, "no usage field here")
        self.assertIsNone(usage)

    def test_falls_back_to_raw_on_plain_text(self):
        raw = "I did some work.\n\nEXIT: 0\nREASON: ok\n..."
        text, usage = ri.parse_claude_output(raw)
        self.assertEqual(text, raw)
        self.assertIsNone(usage)

    def test_falls_back_to_raw_on_malformed_json(self):
        raw = "{not valid json"
        text, usage = ri.parse_claude_output(raw)
        self.assertEqual(text, raw)
        self.assertIsNone(usage)

    def test_falls_back_when_top_level_isnt_object(self):
        # Defensive: top-level array (unusual but possible).
        raw = '["just", "a", "list"]'
        text, usage = ri.parse_claude_output(raw)
        self.assertEqual(text, raw)
        self.assertIsNone(usage)


class TestParseCodexUsage(unittest.TestCase):
    def test_sums_usage_across_turn_completed_events(self):
        events = [
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "..."}},
            {"type": "turn.completed", "usage": {
                "input_tokens": 1000, "output_tokens": 50, "cached_input_tokens": 800,
            }},
        ]
        jsonl = "\n".join(json.dumps(e) for e in events)
        usage = ri.parse_codex_usage(jsonl)
        self.assertEqual(usage, {"input": 1000, "output": 50, "cached": 800})

    def test_sums_across_multiple_turns(self):
        # Hypothetical multi-turn iteration; helper should sum defensively.
        events = [
            {"type": "turn.completed", "usage": {
                "input_tokens": 100, "output_tokens": 10, "cached_input_tokens": 80,
            }},
            {"type": "turn.completed", "usage": {
                "input_tokens": 200, "output_tokens": 20, "cached_input_tokens": 150,
            }},
        ]
        jsonl = "\n".join(json.dumps(e) for e in events)
        usage = ri.parse_codex_usage(jsonl)
        self.assertEqual(usage, {"input": 300, "output": 30, "cached": 230})

    def test_returns_none_when_no_turn_completed_event(self):
        events = [
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "..."}},
        ]
        jsonl = "\n".join(json.dumps(e) for e in events)
        self.assertIsNone(ri.parse_codex_usage(jsonl))

    def test_returns_none_on_empty_input(self):
        self.assertIsNone(ri.parse_codex_usage(""))

    def test_ignores_malformed_lines(self):
        jsonl = (
            '{not valid json\n'
            + json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 5, "output_tokens": 1, "cached_input_tokens": 0,
            }}) + "\n"
        )
        usage = ri.parse_codex_usage(jsonl)
        self.assertEqual(usage, {"input": 5, "output": 1, "cached": 0})


class TestFormatTokensSegment(unittest.TestCase):
    def test_empty_when_usage_is_none(self):
        self.assertEqual(ri.format_tokens_segment(None), "")

    def test_empty_when_usage_is_falsy(self):
        # {} is falsy; treat as "no telemetry."
        self.assertEqual(ri.format_tokens_segment({}), "")

    def test_emits_three_fields_with_leading_separator(self):
        seg = ri.format_tokens_segment({"input": 1100, "output": 50, "cached": 800})
        self.assertEqual(seg, " | tokens_in=1100 tokens_out=50 tokens_cached=800")


class TestSummaryLogTokens(unittest.TestCase):
    def test_summary_includes_tokens_when_claude_emits_json(self):
        # Fake invoker returns a JSON response with usage; runner extracts
        # tokens and appends them to the summary line.
        with TempProject() as p:
            raw = json.dumps({
                "type": "result",
                "result": signal_block(exit_code=0, reason="ok"),
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 800,
                    "cache_creation_input_tokens": 200,
                },
            })
            invoker = make_fake_invoker(raw)
            run_iter(invoker=invoker)
            summary = read_summary(p.root).strip().splitlines()[-1]
            # gross input = 100 + 800 + 200 = 1100
            self.assertIn("tokens_in=1100", summary)
            self.assertIn("tokens_out=50", summary)
            self.assertIn("tokens_cached=800", summary)
            # tokens segment should sit between exit= and reason=, not after.
            self.assertLess(summary.index("tokens_"), summary.index("reason="))

    def test_summary_omits_tokens_when_claude_emits_plain_text(self):
        # Backward-compat: plain-text claude output -> no tokens segment.
        with TempProject() as p:
            invoker = make_fake_invoker(
                signal_block(exit_code=0, reason="plain")
            )
            run_iter(invoker=invoker)
            summary = read_summary(p.root).strip().splitlines()[-1]
            self.assertNotIn("tokens_in=", summary)
            self.assertNotIn("tokens_out=", summary)


if __name__ == "__main__":
    unittest.main()
