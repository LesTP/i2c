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
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent

from i2c import run_iteration as ri
from tests._fixtures import copy_fixture, write_adapters

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
        copy_fixture(self.root)
        # Adapters are project-root assets (WORKER_SPEC.md + instructions/ resolve
        # from package-data, §5.3, so they aren't copied).
        write_adapters(self.root)
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

    The optional `capture` list collects call kwargs (cwd, prompt, model,
    budget, system_prompt_file) so tests can assert what the runner sent to
    the worker.
    """
    def fake(prompt, *, cwd, model, max_budget_usd, system_prompt_file=None):
        if capture is not None:
            capture.append({
                "cwd": cwd, "prompt": prompt, "model": model,
                "max_budget_usd": max_budget_usd,
                "system_prompt_file": system_prompt_file,
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
            # FU-35 split: the stdin prompt carries the volatile body
            # (ACTION CONTEXT) but NOT the cache-stable prefix, which now
            # rides in the system prompt file.
            self.assertIn("ACTION CONTEXT", calls[0]["prompt"])
            self.assertNotIn("WORKER CONTRACT", calls[0]["prompt"])
            # Logs were written, including the claude system-prompt file.
            log_dir = p.root / "logs" / "loop"
            self.assertTrue((log_dir / "iteration_001_prompt.md").is_file())
            self.assertTrue((log_dir / "iteration_001_system.md").is_file())
            self.assertTrue((log_dir / "iteration_001.txt").is_file())
            # The runner pointed claude at that system file, and it holds
            # the cache-stable prefix.
            self.assertEqual(
                calls[0]["system_prompt_file"],
                log_dir / "iteration_001_system.md",
            )
            sys_text = (log_dir / "iteration_001_system.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("WORKER CONTRACT", sys_text)
            self.assertIn("TOOL RULES", sys_text)
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


class TestInvokeClaudeCacheFlags(unittest.TestCase):
    """FU-35: invoke_claude wires the system-prompt + cache-reuse flags."""

    def _capture_cmd(self, **kwargs):
        captured: dict = {}

        class _Proc:
            returncode = 0
            stdout = "{}"
            stderr = ""

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["input"] = kw.get("input")
            return _Proc()

        orig = ri.subprocess.run
        ri.subprocess.run = fake_run
        try:
            ri.invoke_claude(
                "BODY", cwd=Path("."), model="sonnet",
                max_budget_usd=2.0, **kwargs,
            )
        finally:
            ri.subprocess.run = orig
        return captured

    def test_cache_flags_present_with_system_file(self):
        cap = self._capture_cmd(system_prompt_file=Path("sys.md"))
        self.assertIn("--append-system-prompt-file", cap["cmd"])
        idx = cap["cmd"].index("--append-system-prompt-file")
        self.assertEqual(cap["cmd"][idx + 1], str(Path("sys.md")))
        self.assertIn("--exclude-dynamic-system-prompt-sections", cap["cmd"])
        # The volatile body still rides on stdin, not in the system prompt.
        self.assertEqual(cap["input"], "BODY")

    def test_cache_flags_absent_without_system_file(self):
        cap = self._capture_cmd()
        self.assertNotIn("--append-system-prompt-file", cap["cmd"])
        self.assertNotIn("--exclude-dynamic-system-prompt-sections", cap["cmd"])


class TestCodexNoSplit(unittest.TestCase):
    """FU-35: codex keeps one combined prompt; no claude system file."""

    def test_codex_gets_full_prompt_and_no_system_file(self):
        with TempProject() as p:
            calls = []

            def fake_codex(prompt, *, cwd):
                calls.append(prompt)
                jsonl = json.dumps({
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": signal_block(exit_code=0, reason="ok"),
                    },
                }) + "\n"
                return 0, jsonl, signal_block(exit_code=0, reason="ok")

            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = ri.run_iteration(
                    backend="codex",
                    model="sonnet",
                    max_budget_usd=5.0,
                    codex_invoker=fake_codex,
                )
            self.assertEqual(rc, 0, msg=err.getvalue())
            # Full prompt: stable prefix AND volatile body in one string.
            self.assertEqual(len(calls), 1)
            self.assertIn("WORKER CONTRACT", calls[0])
            self.assertIn("ACTION CONTEXT", calls[0])
            # No claude system file for a codex run.
            log_dir = p.root / "logs" / "loop"
            self.assertFalse((log_dir / "iteration_001_system.md").is_file())
            self.assertTrue((log_dir / "iteration_001_prompt.md").is_file())


class TestAssemblePromptBackend(unittest.TestCase):
    """The assembler is invoked with the dispatched backend's adapter, not a
    hardcoded one — so codex runs get CODEX.md Tool Rules, claude gets
    CLAUDE.md. A regression (hardcoded backend) makes these identical.
    """

    def test_backend_selects_adapter(self):
        with TempProject() as p:
            claude_out = ri.assemble_prompt(
                p.root, "execute", 2, backend="claude", emit="full")
            codex_out = ri.assemble_prompt(
                p.root, "execute", 2, backend="codex", emit="full")
            self.assertTrue(claude_out)
            self.assertTrue(codex_out)
            self.assertNotEqual(claude_out, codex_out)


class TestBackendResolution(unittest.TestCase):
    """Per-action backend resolution: explicit override > map[action] > default.
    The fixture dispatches EXECUTE, so the 'execute' key (or default) decides."""

    def _run(self, *, backend=None, backend_map=None, default_backend="claude"):
        claude_calls: list[bool] = []
        codex_calls: list[bool] = []

        def fake_claude(prompt, *, cwd, model, max_budget_usd, system_prompt_file=None):
            claude_calls.append(True)
            return 0, signal_block()

        def fake_codex(prompt, *, cwd):
            codex_calls.append(True)
            jsonl = json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": signal_block()},
            }) + "\n"
            return 0, jsonl, signal_block()

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = ri.run_iteration(
                backend=backend,
                backend_map=backend_map,
                default_backend=default_backend,
                model="sonnet",
                max_budget_usd=5.0,
                claude_invoker=fake_claude,
                codex_invoker=fake_codex,
            )
        return rc, claude_calls, codex_calls, err.getvalue()

    def test_map_selects_codex_for_execute(self):
        with TempProject():
            rc, cc, xc, err = self._run(backend_map={"execute": "codex"})
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual((len(cc), len(xc)), (0, 1))

    def test_explicit_override_beats_map(self):
        with TempProject():
            rc, cc, xc, err = self._run(
                backend="claude", backend_map={"execute": "codex"}
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual((len(cc), len(xc)), (1, 0))

    def test_falls_back_to_default_when_action_absent_from_map(self):
        with TempProject():
            # action is EXECUTE; map only has 'plan' -> default_backend decides.
            rc, cc, xc, err = self._run(
                backend_map={"plan": "codex"}, default_backend="codex"
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual((len(cc), len(xc)), (0, 1))


class TestCommitState(unittest.TestCase):
    """commit_state / dirty_tracked_outside_state against a real temp git repo."""

    @staticmethod
    def _git(args, cwd):
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True)

    def _init_repo(self, root):
        self._git(["init", "-q"], root)
        self._git(["config", "user.email", "t@t.t"], root)
        self._git(["config", "user.name", "t"], root)
        (root / ".state").mkdir()
        (root / ".state" / "project.json").write_text('{"phase":1}\n', encoding="utf-8")
        (root / "README.md").write_text("x\n", encoding="utf-8")
        self._git(["add", "-A"], root)
        self._git(["commit", "-qm", "init"], root)

    def test_commits_state_only_and_flags_dangling(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._init_repo(root)
            (root / ".state" / "project.json").write_text('{"phase":2}\n', encoding="utf-8")
            (root / "README.md").write_text("changed\n", encoding="utf-8")  # tracked, dirty
            committed, note = ri.commit_state(root, phase=2)
            self.assertTrue(committed, note)
            names = self._git(["log", "-1", "--name-only", "--format="], root).stdout
            self.assertIn(".state/project.json", names)
            self.assertNotIn("README.md", names)  # scoped to .state/
            # README.md still dirty -> surfaced as a boundary-cleanliness warning.
            self.assertIn("README.md", ri.dirty_tracked_outside_state(root))

    def test_skips_clean_state(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._init_repo(root)
            committed, note = ri.commit_state(root, phase=1)
            self.assertFalse(committed)
            self.assertIn("nothing to commit", note)

    def test_non_git_dir_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            committed, _ = ri.commit_state(Path(d), phase=1)
            self.assertFalse(committed)


class TestCloseStateCommit(unittest.TestCase):
    """The runner invokes state_committer only after a *successful* CLOSE."""

    def test_committer_called_on_successful_close(self):
        with TempProject() as p:
            # state=close -> CLOSE dispatched; invariants patched to pass so we
            # reach the post-close commit (a fake worker can't set
            # audit_boundary itself).
            p.patch_project(state="close")
            calls = []
            orig = ri.invariants.check_post_action
            ri.invariants.check_post_action = lambda root, action: []
            try:
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = ri.run_iteration(
                        backend="claude", model="sonnet", max_budget_usd=5.0,
                        claude_invoker=make_fake_invoker(signal_block()),
                        state_committer=lambda root, *, phase: (
                            calls.append(phase) or (True, "ok")),
                    )
            finally:
                ri.invariants.check_post_action = orig
            self.assertEqual(rc, 0, msg=err.getvalue())
            self.assertEqual(calls, [2])  # fixture is phase 2

    def test_committer_not_called_on_execute(self):
        with TempProject():
            calls = []
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = ri.run_iteration(
                    backend="claude", model="sonnet", max_budget_usd=5.0,
                    claude_invoker=make_fake_invoker(signal_block()),
                    state_committer=lambda root, *, phase: (
                        calls.append(phase) or (True, "ok")),
                )
            self.assertEqual(rc, 0, msg=err.getvalue())
            self.assertEqual(calls, [])  # execute -> no state commit


class TestCommitExecute(unittest.TestCase):
    """FU-40 Inc 2: runner-owned EXECUTE code commit (commit_execute + snapshot)."""

    def _repo(self):
        tmp = tempfile.TemporaryDirectory(prefix="i2c_ce_")
        root = Path(tmp.name)

        def run(*a):
            return subprocess.run(
                ["git", *a], cwd=root, capture_output=True, text=True
            )

        run("init", "-q")
        run("config", "user.email", "t@t")
        run("config", "user.name", "t")
        (root / "seed.py").write_text("x = 1\n", encoding="utf-8")
        run("add", "-A")
        run("commit", "-q", "-m", "seed")
        return tmp, root, run

    def test_commits_worker_changes_fencing_off_wip(self):
        tmp, root, run = self._repo()
        try:
            # operator WIP: an untracked half-finished doc
            (root / "NOTES.md").write_text("draft...\n", encoding="utf-8")
            pre = ri._worker_dirty_paths(root)
            self.assertIn("NOTES.md", pre)
            # worker edits code (modify + new file)
            (root / "seed.py").write_text("x = 2\n", encoding="utf-8")
            (root / "new_mod.py").write_text("y = 3\n", encoding="utf-8")
            committed, chash, note = ri.commit_execute(
                root, phase=2, step=3, summary="do the thing", pre_dirty=pre,
            )
            self.assertTrue(committed, msg=note)
            self.assertTrue(note.startswith("2.3: do the thing"))
            self.assertTrue(chash)
            still = ri._worker_dirty_paths(root)
            self.assertIn("NOTES.md", still)        # WIP left uncommitted
            self.assertNotIn("seed.py", still)      # worker code committed
            self.assertNotIn("new_mod.py", still)
            self.assertEqual(
                run("log", "-1", "--pretty=%s").stdout.strip(), "2.3: do the thing"
            )
        finally:
            tmp.cleanup()

    def test_step_none_uses_phase_only_message(self):
        tmp, root, run = self._repo()
        try:
            pre = ri._worker_dirty_paths(root)
            (root / "seed.py").write_text("x = 9\n", encoding="utf-8")
            committed, _, note = ri.commit_execute(
                root, phase=14, step=None, summary="refine pass", pre_dirty=pre,
            )
            self.assertTrue(committed, msg=note)
            self.assertTrue(note.startswith("14: refine pass"))
        finally:
            tmp.cleanup()

    def test_no_worker_changes_no_commit(self):
        tmp, root, run = self._repo()
        try:
            committed, chash, _ = ri.commit_execute(
                root, phase=2, step=1, summary="noop",
                pre_dirty=ri._worker_dirty_paths(root),
            )
            self.assertFalse(committed)
            self.assertIsNone(chash)
        finally:
            tmp.cleanup()

    def test_overlap_wip_file_left_uncommitted(self):
        # A file already operator-WIP, then also touched by the worker, is fenced
        # off (D\W) and left uncommitted — the accepted trade-off (FU-40).
        tmp, root, run = self._repo()
        try:
            (root / "seed.py").write_text("x = 1  # wip\n", encoding="utf-8")
            pre = ri._worker_dirty_paths(root)
            self.assertIn("seed.py", pre)
            (root / "seed.py").write_text("x = 1  # wip + worker\n", encoding="utf-8")
            (root / "new.py").write_text("a = 1\n", encoding="utf-8")
            committed, _, _ = ri.commit_execute(
                root, phase=2, step=1, summary="s", pre_dirty=pre,
            )
            self.assertTrue(committed)
            still = ri._worker_dirty_paths(root)
            self.assertIn("seed.py", still)     # overlap left for the human
            self.assertNotIn("new.py", still)   # clean worker file committed
        finally:
            tmp.cleanup()

    def test_worker_dirty_paths_excludes_state(self):
        tmp, root, run = self._repo()
        try:
            (root / ".state").mkdir()
            (root / ".state" / "devlog.jsonl").write_text("{}\n", encoding="utf-8")
            (root / "code.py").write_text("z = 1\n", encoding="utf-8")
            paths = ri._worker_dirty_paths(root)
            self.assertIn("code.py", paths)
            self.assertFalse(any(p.startswith(".state") for p in paths))
        finally:
            tmp.cleanup()


class TestExecuteCommitWiring(unittest.TestCase):
    """FU-40 Inc 2: run_iteration drives execute_committer on a successful EXECUTE."""

    @staticmethod
    def _seed_devlog(root, *, outcome="complete"):
        (root / ".state" / "devlog.jsonl").write_text(
            json.dumps({
                "phase": 2, "step": 3, "action": "execute", "outcome": outcome,
                "summary": "did step 3", "timestamp": "2026-07-03T00:00:00Z",
            }) + "\n",
            encoding="utf-8",
        )

    def test_execute_commit_invoked_with_devlog_and_predirty(self):
        with TempProject() as tp:
            self._seed_devlog(tp.root)
            calls = []

            def fake(r, *, phase, step, summary, pre_dirty):
                calls.append((phase, step, summary, pre_dirty))
                return True, "abc1234", f"{phase}.{step}: {summary}"

            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = ri.run_iteration(
                    backend="claude", model="sonnet", max_budget_usd=5.0,
                    claude_invoker=make_fake_invoker(signal_block(exit_code=0)),
                    execute_committer=fake,
                )
            self.assertEqual(rc, 0, msg=err.getvalue())
            self.assertEqual(len(calls), 1)
            phase, step, summary, pre_dirty = calls[0]
            self.assertEqual((phase, step, summary), (2, 3, "did step 3"))
            self.assertIsInstance(pre_dirty, set)

    def test_execute_commit_not_invoked_on_worker_exit_2(self):
        with TempProject() as tp:
            self._seed_devlog(tp.root, outcome="failed")
            calls = []

            def fake(r, **kw):
                calls.append(kw)
                return False, None, "x"

            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = ri.run_iteration(
                    backend="claude", model="sonnet", max_budget_usd=5.0,
                    claude_invoker=make_fake_invoker(signal_block(exit_code=2)),
                    execute_committer=fake,
                )
            self.assertEqual(rc, 2)
            self.assertEqual(calls, [])  # no commit on a failed step


if __name__ == "__main__":
    unittest.main()
