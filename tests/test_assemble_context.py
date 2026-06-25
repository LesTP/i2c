"""Tests for tools/assemble_context.py — the context assembler.

Per ARCH_assembler.md (the spec). Per-renderer unit tests plus smoke-level
full-section invocations. Golden-output snapshots deferred per D-impl-2.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from i2c import assemble_context as ac

I2C_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = I2C_ROOT / "examples" / "initial_state"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TempProject:
    """Context manager: copy the canonical fixture into a temp dir.

    If `with_framework=True`, also copies WORKER_SPEC.md, instructions/,
    CLAUDE.md, and CODEX.md from the i2c root — enough to exercise the
    full-prompt renderers.
    """

    def __init__(
        self,
        *,
        with_extra: dict[str, str] | None = None,
        with_framework: bool = False,
    ):
        # `with_extra` is {relative_path: content} written into the project root.
        self.with_extra = with_extra or {}
        self.with_framework = with_framework

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_asm_")
        root = Path(self._tmp.name) / "project"
        shutil.copytree(FIXTURE, root)
        if self.with_framework:
            # Adapters are project-root assets; copy them from the packaged
            # templates. WORKER_SPEC.md and instructions/ now resolve from
            # package-data (§5.3), so they need not be copied into the project.
            adapters = I2C_ROOT / "i2c" / "data" / "adapters"
            shutil.copy2(adapters / "claude.md", root / "CLAUDE.md")
            shutil.copy2(adapters / "codex.md", root / "CODEX.md")
        for relpath, content in self.with_extra.items():
            target = root / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        self.root = root
        self._prev_cwd = Path.cwd()
        os.chdir(root)
        return root

    def __exit__(self, *args):
        os.chdir(self._prev_cwd)
        self._tmp.cleanup()


def run_cli(*argv: str) -> tuple[int, str, str]:
    """Run ac.main with argv; capture (exit_code, stdout, stderr)."""
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = ac.main(list(argv))
            if rc is None:
                rc = 0
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


def build_ctx(
    *,
    action: str | None = None,
    section: str | None = None,
    phase: int | None = None,
    mode: str | None = None,
    module: str | None = None,
    backend: str = "claude",
) -> ac.AssemblerContext:
    """Build a context against the current CWD's project."""
    import argparse
    ns = argparse.Namespace(
        action=action, section=section, phase=phase, mode=mode,
        module=module, backend=backend,
    )
    return ac.build_context(ns)


# ---------------------------------------------------------------------------
# Project root detection
# ---------------------------------------------------------------------------


class TestFindProjectRoot(unittest.TestCase):
    def test_finds_from_cwd(self):
        with TempProject() as root:
            self.assertEqual(ac.find_project_root().resolve(), root.resolve())

    def test_finds_from_subdir(self):
        with TempProject() as root:
            sub = root / "tools"
            sub.mkdir()
            os.chdir(sub)
            self.assertEqual(ac.find_project_root().resolve(), root.resolve())

    def test_exits_when_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev = Path.cwd()
            os.chdir(tmp)
            try:
                with self.assertRaises(SystemExit) as cm:
                    ac.find_project_root()
                self.assertEqual(cm.exception.code, 1)
            finally:
                os.chdir(prev)


# ---------------------------------------------------------------------------
# Marker parsing + heading parsing
# ---------------------------------------------------------------------------


class TestMarkerParsing(unittest.TestCase):
    def test_boolean_marker(self):
        self.assertEqual(
            ac.parse_marker("<!-- assembler:autonomous_only -->"),
            ("autonomous_only", None),
        )

    def test_kv_marker(self):
        self.assertEqual(
            ac.parse_marker("<!-- assembler:requires=dependencies_nonempty -->"),
            ("requires", "dependencies_nonempty"),
        )

    def test_non_marker(self):
        self.assertIsNone(ac.parse_marker("just a comment line"))
        self.assertIsNone(ac.parse_marker("<!-- other:comment -->"))


class TestHeadingParsing(unittest.TestCase):
    def test_h2(self):
        self.assertEqual(ac.heading_level("## Hello"), 2)

    def test_h4(self):
        self.assertEqual(ac.heading_level("#### Sub"), 4)

    def test_non_heading(self):
        self.assertIsNone(ac.heading_level("regular text"))
        self.assertIsNone(ac.heading_level("#no-space"))


# ---------------------------------------------------------------------------
# Shared snapshot renderers — against the fixture
#
# These leaf renderers feed the worker prompt. The operator-derived status /
# phase-summary / devlog sections were removed in Phase 3a (FU-39); their
# coverage now lives in tests/test_control.py + tests/test_cli.py.
# ---------------------------------------------------------------------------


class TestSharedRenderers(unittest.TestCase):
    def test_current_phase_steps_table_filters_to_current_phase(self):
        with TempProject():
            ctx = build_ctx(section="status")
            out = ac.render_current_phase_steps_table(ctx)
            self.assertTrue(out.startswith("## Current Phase Steps"))
            self.assertIn("| Step |", out)
            # Phase 2 has steps 1-4. Phase 1 steps must not appear.
            self.assertIn("2.1", out)
            self.assertIn("2.4", out)
            self.assertNotIn("1.1", out)
            self.assertNotIn("1.2", out)
            # Commit column shows the recorded hash for completed step.
            self.assertIn("1234567", out)

    def test_current_phase_steps_empty(self):
        with TempProject() as root:
            # Wipe steps for current phase.
            (root / ".state" / "steps.json").write_text(
                json.dumps([
                    {"phase": 1, "step": 1, "title": "x", "status": "complete"},
                ]),
                encoding="utf-8",
            )
            ctx = build_ctx(section="status")
            out = ac.render_current_phase_steps_table(ctx)
            self.assertIn("<!-- empty -->", out)

    def test_gotchas_renders_bullets(self):
        with TempProject():
            ctx = build_ctx(section="status")
            out = ac.render_gotchas(ctx)
            self.assertTrue(out.startswith("## Gotchas"))
            self.assertIn("- Always pass", out)

    def test_gotchas_empty_placeholder(self):
        with TempProject() as root:
            data = json.loads((root / ".state" / "project.json").read_text())
            data["gotchas"] = []
            (root / ".state" / "project.json").write_text(json.dumps(data))
            ctx = build_ctx(section="status")
            out = ac.render_gotchas(ctx)
            self.assertIn("<!-- empty -->", out)

    def test_recent_activity_takes_last_n_reversed(self):
        with TempProject():
            ctx = build_ctx(section="status")
            out = ac.render_recent_activity(ctx, n=3)
            self.assertTrue(out.startswith("## Recent Activity"))
            # Most recent first. Fixture's last entry is phase 2 step 1.
            lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
            self.assertEqual(len(lines), 3)
            self.assertIn("2.1 execute", lines[0])
            # commit '1234567' present on that entry
            self.assertIn("1234567", lines[0])

    def test_recent_activity_empty_devlog(self):
        with TempProject() as root:
            (root / ".state" / "devlog.jsonl").write_text("", encoding="utf-8")
            ctx = build_ctx(section="architecture")
            out = ac.render_recent_activity(ctx, n=3)
            self.assertIn("<!-- empty -->", out)


# ---------------------------------------------------------------------------
# CLI argument errors (exit 2 path per §11.3)
# ---------------------------------------------------------------------------


class TestCliArgumentErrors(unittest.TestCase):
    def test_both_action_and_section_rejected(self):
        rc, _, err = run_cli("--action", "plan", "--phase", "1", "--section", "architecture")
        # argparse mutually-exclusive raises exit 2.
        self.assertEqual(rc, 2)

    def test_neither_action_nor_section_rejected(self):
        rc, _, err = run_cli("--phase", "1")
        self.assertEqual(rc, 2)

    def test_action_without_phase(self):
        with TempProject():
            rc, _, err = run_cli("--action", "plan")
            self.assertEqual(rc, 2)

    def test_phase_not_positive(self):
        with TempProject():
            rc, _, err = run_cli("--action", "plan", "--phase", "0")
            self.assertEqual(rc, 2)

    def test_section_module_requires_module(self):
        with TempProject():
            rc, _, err = run_cli("--section", "module")
            self.assertEqual(rc, 2)

    def test_mode_with_section_rejected(self):
        with TempProject():
            rc, _, err = run_cli("--section", "architecture", "--mode", "supervised")
            self.assertEqual(rc, 2)

    def test_emit_with_section_rejected(self):
        # FU-35: --emit is only meaningful with --action.
        rc, _, err = run_cli("--section", "architecture", "--emit", "user")
        self.assertEqual(rc, 2)

    def test_phase_with_section_architecture_rejected(self):
        rc, _, err = run_cli("--section", "architecture", "--phase", "3")
        self.assertEqual(rc, 2)

    def test_unknown_action_rejected(self):
        rc, _, err = run_cli("--action", "bogus", "--phase", "1")
        self.assertEqual(rc, 2)

    def test_unknown_section_rejected(self):
        rc, _, err = run_cli("--section", "bogus")
        self.assertEqual(rc, 2)


# ---------------------------------------------------------------------------
# Required-input failures (exit 1 path per §11.1)
# ---------------------------------------------------------------------------


class TestRequiredInputFailures(unittest.TestCase):
    def test_missing_project_json(self):
        with TempProject() as root:
            (root / ".state" / "project.json").unlink()
            # Without project.json, find_project_root will fail at startup.
            rc, _, err = run_cli("--section", "architecture")
            self.assertEqual(rc, 1)
            self.assertIn("ERROR:", err)

    def test_schema_invalid_project_json(self):
        with TempProject() as root:
            (root / ".state" / "project.json").write_text(
                json.dumps({"phase": 1, "state": "BOGUS"})
            )
            rc, _, err = run_cli("--section", "architecture")
            self.assertEqual(rc, 1)
            self.assertIn("ERROR:", err)
            self.assertIn("schema-invalid", err)


# ---------------------------------------------------------------------------
# PLAN-action tolerance for missing phase record (Stack B, DESIGN §6.4)
# ---------------------------------------------------------------------------


class TestPlanActionToleratesMissingPhaseRecord(unittest.TestCase):
    """Per DESIGN_state_lifecycle_v1.md §6.4: --action plan against a phase
    with no phases.json record must render a stub instead of error_exit.
    PLAN's procedure (instructions/plan.md step 4) creates the record."""

    def test_phase_heading_renders_stub_under_action_plan(self):
        with TempProject():
            # Phase 99 has no phases.json record (fixture has 1-4).
            ctx = build_ctx(action="plan", phase=99, mode="autonomous")
            out = ac.render_phase_heading(ctx)
            self.assertIn("## Phase: 99", out)
            self.assertIn("(record to be created by PLAN)", out)

    def test_phase_heading_still_errors_under_other_actions(self):
        with TempProject():
            # Same missing-record condition under --action execute must still
            # error_exit — it indicates a real misdispatch.
            ctx = build_ctx(action="execute", phase=99, mode="autonomous")
            with self.assertRaises(SystemExit) as cm:
                ac.render_phase_heading(ctx)
            self.assertEqual(cm.exception.code, 1)

    def test_phase_heading_still_errors_for_section_request(self):
        with TempProject():
            # --section requests don't get the tolerance either.
            ctx = build_ctx(section="status", phase=99)
            # render_phase_heading isn't called for --section status, but if
            # render_current_phase is called explicitly with this ctx, no
            # tolerance should apply.
            with self.assertRaises(SystemExit) as cm:
                ac.render_phase_heading(ctx)
            self.assertEqual(cm.exception.code, 1)

    def test_current_phase_renders_helpful_placeholder_under_action_plan(self):
        with TempProject():
            ctx = build_ctx(action="plan", phase=99, mode="autonomous")
            out = ac.render_current_phase(ctx)
            self.assertTrue(out.startswith("## Current Phase"))
            self.assertIn("no phases.json record yet for phase 99", out)
            self.assertIn("PLAN will create it", out)
            self.assertIn("instructions/plan.md step 4", out)

    def test_current_phase_renders_empty_marker_for_other_actions(self):
        with TempProject():
            # Non-plan actions still get the bare empty marker (in practice
            # they error before reaching this renderer).
            ctx = build_ctx(action="execute", phase=99, mode="autonomous")
            out = ac.render_current_phase(ctx)
            self.assertIn(ac.PLACEHOLDER_EMPTY, out)
            self.assertNotIn("PLAN will create it", out)

    def test_dependencies_nonempty_evaluator_false_when_no_record(self):
        # The conditional dep-probe section strips when there is no record
        # to inspect — PLAN's prompt for a fresh phase has no probe; PLAN
        # creates the record (possibly non-leaf) and a follow-up assembler
        # call surfaces the probe section.
        with TempProject():
            ctx = build_ctx(action="plan", phase=99, mode="autonomous")
            self.assertFalse(
                ac._eval_dependencies_nonempty(ctx, value=None)
            )

    def test_plan_action_end_to_end_no_record(self):
        # Full CLI invocation: --action plan --phase 99 must succeed and
        # produce a prompt containing the stub heading + placeholder body.
        with TempProject(with_framework=True):
            rc, out, err = run_cli(
                "--action", "plan", "--phase", "99", "--mode", "supervised"
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("## Phase: 99", out)
            self.assertIn("(record to be created by PLAN)", out)
            self.assertIn("no phases.json record yet for phase 99", out)

    def test_plan_action_end_to_end_dep_probe_stripped(self):
        # The conditional dep-probe section in instructions/plan.md (gated
        # on dependencies_nonempty) must be absent from the PLAN prompt
        # when no record exists. Match the section heading "Pre-plan:
        # Dependency Probe" (with colon) — the no-colon variant appears in
        # plan.md's narrative outside the gated section.
        with TempProject(with_framework=True):
            rc, out, err = run_cli(
                "--action", "plan", "--phase", "99", "--mode", "supervised"
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertNotIn("Pre-plan: Dependency Probe", out)

    def test_execute_action_end_to_end_no_record_still_fails(self):
        # Negative: --action execute with no record must still fail. The
        # tolerance is plan-only.
        with TempProject(with_framework=True):
            rc, out, err = run_cli(
                "--action", "execute", "--phase", "99", "--mode", "supervised"
            )
            self.assertEqual(rc, 1)
            self.assertIn("ERROR:", err)
            self.assertIn("missing current-phase record", err)


# ---------------------------------------------------------------------------
# Conditional section stripping (§7)
# ---------------------------------------------------------------------------


class TestConditionalStripping(unittest.TestCase):
    def _ctx(self, mode: str = "autonomous", phase: int = 2) -> ac.AssemblerContext:
        return build_ctx(action="plan", phase=phase, mode=mode)

    def test_autonomous_only_kept_in_autonomous(self):
        with TempProject():
            md = (
                "# Title\n\n## Keep\nbody\n\n## Drop\n"
                "<!-- assembler:autonomous_only -->\n"
                "secret body\n\n## Tail\ntail body\n"
            )
            out = ac.strip_conditional_sections(md, self._ctx(mode="autonomous"))
            self.assertIn("## Drop", out)
            self.assertIn("secret body", out)
            # Marker line itself is removed.
            self.assertNotIn("autonomous_only", out)
            self.assertIn("## Tail", out)

    def test_autonomous_only_stripped_in_supervised(self):
        with TempProject():
            md = (
                "## Keep\nbody\n\n## Drop\n"
                "<!-- assembler:autonomous_only -->\n"
                "secret body\n\n## Tail\ntail body\n"
            )
            out = ac.strip_conditional_sections(md, self._ctx(mode="supervised"))
            self.assertIn("## Keep", out)
            self.assertNotIn("## Drop", out)
            self.assertNotIn("secret body", out)
            self.assertIn("## Tail", out)

    def test_requires_dependencies_nonempty_strip_when_empty(self):
        # Phase 2 in fixture has dependencies == [] → section should strip.
        with TempProject():
            md = (
                "## Always\nx\n\n## Probe\n"
                "<!-- assembler:requires=dependencies_nonempty -->\n"
                "probe body\n\n## End\ny\n"
            )
            out = ac.strip_conditional_sections(md, self._ctx())
            self.assertIn("## Always", out)
            self.assertNotIn("## Probe", out)
            self.assertNotIn("probe body", out)

    def test_requires_dependencies_nonempty_kept_when_present(self):
        # Phase 4 in fixture depends on ["event_store"] → keep section.
        with TempProject():
            md = (
                "## Always\nx\n\n## Probe\n"
                "<!-- assembler:requires=dependencies_nonempty -->\n"
                "probe body\n\n## End\ny\n"
            )
            out = ac.strip_conditional_sections(md, self._ctx(phase=4))
            self.assertIn("## Probe", out)
            self.assertIn("probe body", out)

    def test_marker_in_h3_strips_at_h3_boundary(self):
        with TempProject():
            md = (
                "## H2\n\n### keep1\nbody1\n\n"
                "### drop\n<!-- assembler:autonomous_only -->\n"
                "secret\n\n### keep2\nbody2\n"
            )
            out = ac.strip_conditional_sections(md, self._ctx(mode="supervised"))
            self.assertIn("### keep1", out)
            self.assertNotIn("### drop", out)
            self.assertNotIn("secret", out)
            self.assertIn("### keep2", out)


# ---------------------------------------------------------------------------
# Phase 3.A.1 evaluators: multi_step_only + omit_in_prompt
# ---------------------------------------------------------------------------


class TestMultiStepOnlyEvaluator(unittest.TestCase):
    """Marker `multi_step_only` strips when step_budget == 1, keeps when > 1."""

    def _ctx(self, *, step_budget: int) -> ac.AssemblerContext:
        ctx = build_ctx(action="plan", phase=2, mode="autonomous")
        ctx.step_budget = step_budget
        return ctx

    def test_strips_when_default_single_step(self):
        with TempProject():
            md = (
                "## Always\nx\n\n## Multi\n"
                "<!-- assembler:multi_step_only -->\n"
                "multi-step body\n\n## End\ny\n"
            )
            out = ac.strip_conditional_sections(md, self._ctx(step_budget=1))
            self.assertIn("## Always", out)
            self.assertNotIn("## Multi", out)
            self.assertNotIn("multi-step body", out)

    def test_keeps_when_step_budget_greater_than_one(self):
        with TempProject():
            md = (
                "## Always\nx\n\n## Multi\n"
                "<!-- assembler:multi_step_only -->\n"
                "multi-step body\n\n## End\ny\n"
            )
            out = ac.strip_conditional_sections(md, self._ctx(step_budget=3))
            self.assertIn("## Multi", out)
            self.assertIn("multi-step body", out)
            # Marker line itself is removed.
            self.assertNotIn("multi_step_only", out)


class TestOmitInPromptEvaluator(unittest.TestCase):
    """Marker `omit_in_prompt` strips unconditionally on every assembly."""

    def _ctx(self, **kwargs) -> ac.AssemblerContext:
        return build_ctx(action="plan", phase=2, mode="autonomous", **kwargs)

    def test_always_strips(self):
        with TempProject():
            md = (
                "## Keep\nbody\n\n## Drop\n"
                "<!-- assembler:omit_in_prompt -->\n"
                "operator-only prose\n\n## Tail\ntail body\n"
            )
            out = ac.strip_conditional_sections(md, self._ctx())
            self.assertIn("## Keep", out)
            self.assertNotIn("## Drop", out)
            self.assertNotIn("operator-only prose", out)
            self.assertIn("## Tail", out)


class TestStepBudgetFlag(unittest.TestCase):
    """--step-budget CLI flag parsing + validation."""

    def test_default_is_one(self):
        parser = ac.build_parser()
        args = parser.parse_args(
            ["--action", "plan", "--phase", "2", "--mode", "autonomous"],
        )
        self.assertEqual(args.step_budget, 1)

    def test_accepts_positive_int(self):
        parser = ac.build_parser()
        args = parser.parse_args(
            ["--action", "plan", "--phase", "2", "--step-budget", "5"],
        )
        self.assertEqual(args.step_budget, 5)

    def test_rejects_zero(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, _, err = run_cli(
                "--action", "plan", "--phase", "2",
                "--mode", "autonomous", "--step-budget", "0",
            )
            self.assertEqual(rc, 2)
            self.assertIn("--step-budget must be a positive integer", err)

    def test_rejects_negative(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, _, err = run_cli(
                "--action", "plan", "--phase", "2",
                "--mode", "autonomous", "--step-budget", "-3",
            )
            self.assertEqual(rc, 2)
            self.assertIn("--step-budget must be a positive integer", err)


# ---------------------------------------------------------------------------
# Section renderers — non-status sections
# ---------------------------------------------------------------------------


class TestWorkerSpecRenderer(unittest.TestCase):
    def test_worker_spec_under_autonomous_includes_output_contract(self):
        with TempProject(with_framework=True):
            ctx = build_ctx(action="execute", phase=2, mode="autonomous")
            out = ac.render_worker_spec(ctx)
            self.assertIn("Identity", out)
            self.assertIn("Main Loop", out)
            self.assertIn("Output Contract", out)
            self.assertIn("Autonomous Behavioral Rules", out)
            self.assertIn("Prohibitions", out)
            # Marker comment should not leak.
            self.assertNotIn("autonomous_only", out)
            # H1 title is stripped (we slice from first H2).
            self.assertNotIn("# Worker Spec", out)

    def test_worker_spec_under_supervised_strips_autonomous_only(self):
        with TempProject(with_framework=True):
            ctx = build_ctx(action="execute", phase=2, mode="supervised")
            out = ac.render_worker_spec(ctx)
            self.assertIn("Identity", out)
            self.assertNotIn("Output Contract", out)
            self.assertNotIn("Autonomous Behavioral Rules", out)
            self.assertIn("Prohibitions", out)


class TestActionContextRenderers(unittest.TestCase):
    def test_action_heading_autonomous(self):
        with TempProject():
            ctx = build_ctx(action="execute", phase=2, mode="autonomous")
            self.assertEqual(ac.render_action_heading(ctx), "## Action: EXECUTE")

    def test_action_heading_supervised(self):
        with TempProject():
            ctx = build_ctx(action="plan", phase=2, mode="supervised")
            self.assertEqual(ac.render_action_heading(ctx), "## Active Action: PLAN")

    def test_next_state_autonomous_present(self):
        with TempProject():
            ctx = build_ctx(action="plan", phase=2, mode="autonomous")
            self.assertEqual(ac.render_next_state(ctx), "## Next State: execute")

    def test_next_state_supervised_empty(self):
        with TempProject():
            ctx = build_ctx(action="plan", phase=2, mode="supervised")
            self.assertEqual(ac.render_next_state(ctx), "")

    def test_phase_heading_uses_em_dash(self):
        with TempProject():
            ctx = build_ctx(action="execute", phase=2)
            self.assertEqual(
                ac.render_phase_heading(ctx),
                "## Phase: 2 \u2014 Core storage (Build)",
            )

    def test_phase_heading_missing_record_exits_1(self):
        with TempProject():
            ctx = build_ctx(action="execute", phase=99)
            with self.assertRaises(SystemExit) as cm:
                ac.render_phase_heading(ctx)
            self.assertEqual(cm.exception.code, 1)

    def test_step_heading_execute_only(self):
        with TempProject():
            ctx_exec = build_ctx(action="execute", phase=2)
            # Fixture: 2.1 complete, 2.2/2.3/2.4 pending. Renderer picks the
            # lowest-numbered pending step — so 2.2.
            self.assertIn("## Step: 2.2", ac.render_step_heading(ctx_exec))
            # Non-execute actions return empty.
            self.assertEqual(ac.render_step_heading(build_ctx(action="plan", phase=2)), "")

    def test_step_heading_omitted_when_no_pending(self):
        with TempProject() as root:
            data = json.loads((root / ".state" / "steps.json").read_text())
            for s in data:
                if s["phase"] == 2:
                    s["status"] = "complete"
                    s["commit"] = "aaaa111"
            (root / ".state" / "steps.json").write_text(json.dumps(data))
            ctx = build_ctx(action="execute", phase=2)
            self.assertEqual(ac.render_step_heading(ctx), "")

    def test_instructions_includes_action_procedure(self):
        with TempProject(with_framework=True):
            ctx = build_ctx(action="plan", phase=4, mode="autonomous")
            out = ac.render_instructions(ctx)
            self.assertTrue(out.startswith("## Instructions"))
            self.assertIn("Procedure", out)
            # Phase 4 has dependencies — Pre-plan Dependency Probe kept.
            self.assertIn("Dependency Probe", out)

    def test_instructions_strips_dep_probe_for_leaf_module(self):
        with TempProject(with_framework=True):
            ctx = build_ctx(action="plan", phase=2, mode="autonomous")
            out = ac.render_instructions(ctx)
            # Phase 2 has dependencies == [] — Pre-plan Dependency Probe stripped.
            self.assertNotIn("Pre-plan: Dependency Probe", out)


class TestModuleContract(unittest.TestCase):
    def test_module_contract_required_when_module_present(self):
        # Phase 2 module is event_store; no ARCH_event_store.md ⇒ exit 1.
        with TempProject():
            ctx = build_ctx(action="execute", phase=2)
            with self.assertRaises(SystemExit) as cm:
                ac.render_module_contract(ctx)
            self.assertEqual(cm.exception.code, 1)

    def test_module_contract_renders_when_present(self):
        with TempProject(with_extra={
            "ARCH_event_store.md": "# Event Store\n\nIntro.\n\n## Surface\n\nDetails.\n",
        }):
            ctx = build_ctx(action="execute", phase=2)
            out = ac.render_module_contract(ctx)
            self.assertTrue(out.startswith("## Module Contract: event_store"))
            self.assertIn("Surface", out)

    def test_module_contract_omitted_when_no_module(self):
        with TempProject() as root:
            data = json.loads((root / ".state" / "phases.json").read_text())
            for p in data:
                if p["id"] == 2:
                    del p["module"]
            (root / ".state" / "phases.json").write_text(json.dumps(data))
            ctx = build_ctx(action="execute", phase=2)
            self.assertEqual(ac.render_module_contract(ctx), "")


class TestProjectContextRenderers(unittest.TestCase):
    def test_project_state_is_json_block(self):
        with TempProject():
            ctx = build_ctx(action="execute", phase=2)
            out = ac.render_project_state(ctx)
            self.assertTrue(out.startswith("## Project State"))
            self.assertIn("```json", out)
            self.assertIn('"phase": 2', out)

    def test_current_phase_table(self):
        with TempProject():
            ctx = build_ctx(action="execute", phase=2)
            out = ac.render_current_phase(ctx)
            self.assertIn("| id |", out)
            self.assertIn("event_store", out)
            self.assertIn("build", out)

    def test_phases_one_line_per_record(self):
        with TempProject():
            ctx = build_ctx(action="plan", phase=2)
            out = ac.render_phases(ctx)
            lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
            self.assertEqual(len(lines), 4)
            self.assertIn("orchestrator", out)

    def test_phase_devlog_filters_to_phase(self):
        with TempProject():
            ctx = build_ctx(action="review", phase=2)
            out = ac.render_phase_devlog(ctx)
            self.assertIn("2.1 execute", out)
            self.assertNotIn("1.1", out)

    def test_prior_phase_summary_omitted_for_phase_1(self):
        with TempProject() as root:
            data = json.loads((root / ".state" / "project.json").read_text())
            data["phase"] = 1
            (root / ".state" / "project.json").write_text(json.dumps(data))
            ctx = build_ctx(action="plan", phase=1)
            self.assertEqual(ac.render_prior_phase_summary(ctx), "")

    def test_prior_phase_summary_includes_prior(self):
        with TempProject():
            ctx = build_ctx(action="plan", phase=2)
            out = ac.render_prior_phase_summary(ctx)
            self.assertIn("Prior Phase Summary", out)
            # Phase 1 has 3 devlog entries; should include the last 3.
            lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
            self.assertEqual(len(lines), 3)

    def test_decisions_table_with_records(self):
        with TempProject():
            ctx = build_ctx(action="plan", phase=2)
            out = ac.render_decisions(ctx)
            self.assertIn("| id |", out)
            self.assertIn("D-1", out)
            self.assertIn("D-2", out)

    def test_decisions_empty_placeholder(self):
        with TempProject() as root:
            (root / ".state" / "decisions.json").write_text("[]")
            ctx = build_ctx(action="plan", phase=2)
            out = ac.render_decisions(ctx)
            self.assertIn("<!-- empty -->", out)


class TestProjectNarrativeRenderers(unittest.TestCase):
    def test_project_scope_missing_placeholder(self):
        with TempProject():
            ctx = build_ctx(action="plan", phase=2)
            out = ac.render_project_scope(ctx)
            self.assertIn("<!-- not present: PROJECT.md not found -->", out)

    def test_project_scope_renders(self):
        with TempProject(with_extra={
            "PROJECT.md": "# Project\n\n## Scope\n\nThis is scope.\n",
        }):
            ctx = build_ctx(action="plan", phase=2)
            out = ac.render_project_scope(ctx)
            self.assertIn("This is scope.", out)
            self.assertTrue(out.startswith("## Project Scope"))

    def test_architecture_missing_placeholder(self):
        with TempProject():
            ctx = build_ctx(action="plan", phase=2)
            out = ac.render_architecture(ctx)
            self.assertIn("<!-- not present: ARCHITECTURE.md not found -->", out)


class TestToolRulesAndAvailableModules(unittest.TestCase):
    def test_tool_rules_claude(self):
        with TempProject(with_framework=True):
            ctx = build_ctx(action="execute", phase=2, backend="claude")
            out = ac.render_tool_rules(ctx)
            self.assertIn("Claude-Specific Tool Rules", out)

    def test_tool_rules_codex(self):
        with TempProject(with_framework=True):
            ctx = build_ctx(action="execute", phase=2, backend="codex")
            out = ac.render_tool_rules(ctx)
            self.assertIn("Codex-Specific Tool Rules", out)

    def test_available_modules_placeholder_only_falls_back_to_arch(self):
        # Adapter ships with the placeholder comment only. ARCHITECTURE.md
        # provides Implementation Sequence.
        arch = (
            "# Arch\n\n## Components\n\nfoo\n\n"
            "## Implementation Sequence\n\n"
            "| id | module | regime | status |\n"
            "|----|--------|--------|--------|\n"
            "| 1  | event_store | build | done |\n"
        )
        with TempProject(with_framework=True, with_extra={"ARCHITECTURE.md": arch}):
            ctx = build_ctx(action="execute", phase=2, backend="claude")
            out = ac.render_available_modules(ctx)
            self.assertTrue(out.startswith("## Available Modules"))
            self.assertIn("event_store", out)

    def test_available_modules_falls_to_empty(self):
        with TempProject(with_framework=True):
            ctx = build_ctx(action="execute", phase=2, backend="claude")
            out = ac.render_available_modules(ctx)
            self.assertIn("<!-- empty -->", out)


# ---------------------------------------------------------------------------
# Banner assembler + full-prompt assembly (Milestone 1.3.C)
# ---------------------------------------------------------------------------


class TestBannerAssembler(unittest.TestCase):
    def test_banner_width_and_shape(self):
        b = ac.assemble_banner("WORKER CONTRACT")
        lines = b.splitlines()
        self.assertEqual(len(lines), 3)
        # Each band line is exactly 47 box-drawing characters.
        self.assertEqual(lines[0], ac.BANNER_CHAR * 47)
        self.assertEqual(lines[2], ac.BANNER_CHAR * 47)
        self.assertEqual(lines[1], "WORKER CONTRACT")


# Minimal complete fixture for full-prompt smoke tests.
_FRAMEWORK_EXTRAS = {
    "ARCH_event_store.md": (
        "# Event Store\n\nIntro.\n\n## Surface\n\nDetails.\n"
    ),
    "ARCH_orchestrator.md": (
        "# Orchestrator\n\nIntro.\n\n## Surface\n\nDetails.\n"
    ),
    "PROJECT.md": "# Project\n\n## Scope\n\nThis is scope.\n",
    "ARCHITECTURE.md": (
        "# Arch\n\n## Components\n\nfoo\n\n"
        "## Implementation Sequence\n\n"
        "| id | module |\n|----|--------|\n| 1  | event_store |\n"
    ),
}


class TestFullPromptAssembly(unittest.TestCase):
    def _expect_banners(self, out: str) -> None:
        for title in ("WORKER CONTRACT", "ACTION CONTEXT", "PROJECT CONTEXT", "TOOL RULES"):
            self.assertIn(title, out)
            self.assertIn(ac.BANNER_CHAR * 47, out)

    def test_plan_autonomous_smoke(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, out, err = run_cli(
                "--action", "plan", "--phase", "4",  # non-leaf phase
                "--mode", "autonomous",
            )
            self.assertEqual(rc, 0, msg=err)
            self._expect_banners(out)
            self.assertIn("Action: PLAN", out)
            self.assertIn("Next State:", out)  # autonomous keeps Next State
            self.assertIn("Phases", out)  # PLAN includes Phases
            self.assertIn("Project Scope", out)
            self.assertIn("Architecture", out)
            self.assertIn("Module Contract:", out)
            self.assertIn("Decisions", out)
            # Phase 4 is non-leaf — dep-probe should be present in instructions.
            self.assertIn("Dependency Probe", out)
            # FU-32 Δ2: escalation triggers section must travel into the prompt.
            self.assertIn("Escalation triggers", out)
            self.assertIn("Source-vs-ARCH drift", out)
            self.assertIn("Multi-regime scope", out)
            self.assertIn("Cross-module breakage", out)
            self.assertIn("Step-shape ambiguity", out)
            self.assertIn("Dep-probe contract mismatch", out)
            # Autonomous-only worker spec sections present.
            self.assertIn("Output Contract", out)
            self.assertIn("Autonomous Behavioral Rules", out)
            self.assertTrue(out.endswith("\n"))

    def test_plan_supervised_strips_autonomous_only(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, out, err = run_cli(
                "--action", "plan", "--phase", "4",
                "--mode", "supervised",
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("Active Action: PLAN", out)
            self.assertNotIn("Next State:", out)
            self.assertNotIn("Output Contract", out)
            self.assertNotIn("Autonomous Behavioral Rules", out)

    def test_execute_includes_step_and_recent_activity(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, out, err = run_cli("--action", "execute", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            self._expect_banners(out)
            self.assertIn("Action: EXECUTE", out)
            self.assertIn("## Step:", out)
            self.assertIn("Recent Activity", out)
            self.assertIn("Current Phase Steps", out)
            # PLAN-only sections must be absent.
            self.assertNotIn("## Phases", out)
            self.assertNotIn("## Project Scope", out)
            self.assertNotIn("## Architecture", out)
            # Phase 3.A.2: Decisions table is per-action; EXECUTE omits it
            # (project-wide decision history isn't per-step load-bearing).
            self.assertNotIn("## Decisions", out)

    def test_decisions_present_for_plan_review_close(self):
        # Sanity: dropping from EXECUTE didn't accidentally drop from the
        # other three actions where the Decisions table IS load-bearing.
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            for action, phase in (("plan", "4"), ("review", "2"), ("close", "2")):
                rc, out, err = run_cli("--action", action, "--phase", phase)
                self.assertEqual(rc, 0, msg=err)
                self.assertIn(
                    "## Decisions", out,
                    msg=f"Decisions missing from {action.upper()} prompt",
                )

    def test_review_includes_phase_devlog_and_architecture(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, out, err = run_cli("--action", "review", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("Action: REVIEW", out)
            self.assertIn("Phase Devlog", out)
            self.assertIn("Current Phase Steps", out)
            self.assertIn("## Architecture", out)
            # No Phases (PLAN only) or Project Scope (PLAN only).
            self.assertNotIn("## Phases", out)
            self.assertNotIn("## Project Scope", out)

    def test_close_includes_phase_devlog_no_architecture(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, out, err = run_cli("--action", "close", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("Action: CLOSE", out)
            self.assertIn("Phase Devlog", out)
            # ARCHITECTURE only for PLAN/REVIEW.
            self.assertNotIn("## Architecture", out)

    def test_full_prompt_deterministic(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc1, out1, _ = run_cli("--action", "execute", "--phase", "2")
            rc2, out2, _ = run_cli("--action", "execute", "--phase", "2")
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)
            self.assertEqual(out1, out2)

    def test_missing_local_instruction_uses_packaged_default(self):
        # §5.3: no project-local instructions/execute.md → the packaged default
        # resolves, so assembly succeeds (was: exit 1 under project-root-only).
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS) as root:
            self.assertFalse((root / "instructions" / "execute.md").exists())
            rc, out, err = run_cli("--action", "execute", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("## Instructions", out)

    def test_missing_adapter_file_exits_1(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS) as root:
            (root / "CLAUDE.md").unlink()
            rc, _, err = run_cli(
                "--action", "execute", "--phase", "2", "--backend", "claude",
            )
            self.assertEqual(rc, 1)

    def test_missing_local_worker_spec_uses_packaged_default(self):
        # §5.3: no project-local WORKER_SPEC.md → packaged default resolves.
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS) as root:
            self.assertFalse((root / "WORKER_SPEC.md").exists())
            rc, out, err = run_cli("--action", "execute", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("WORKER CONTRACT", out)


# ---------------------------------------------------------------------------
# Phase 3.A.1 — region reorder + Available Modules per-action gating
# ---------------------------------------------------------------------------


class TestRegionOrder(unittest.TestCase):
    """Banners appear in the new order: WORKER → TOOL → PROJECT → ACTION."""

    def _banner_positions(self, out: str) -> dict[str, int]:
        return {title: out.index(title) for title in (
            "WORKER CONTRACT", "TOOL RULES", "PROJECT CONTEXT", "ACTION CONTEXT",
        )}

    def test_execute_action_region_order(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, out, err = run_cli("--action", "execute", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            pos = self._banner_positions(out)
            self.assertLess(pos["WORKER CONTRACT"], pos["TOOL RULES"])
            self.assertLess(pos["TOOL RULES"], pos["PROJECT CONTEXT"])
            self.assertLess(pos["PROJECT CONTEXT"], pos["ACTION CONTEXT"])

    def test_plan_action_region_order(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, out, err = run_cli("--action", "plan", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            pos = self._banner_positions(out)
            self.assertLess(pos["WORKER CONTRACT"], pos["TOOL RULES"])
            self.assertLess(pos["TOOL RULES"], pos["PROJECT CONTEXT"])
            self.assertLess(pos["PROJECT CONTEXT"], pos["ACTION CONTEXT"])

    def test_action_context_is_last_region(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, out, err = run_cli("--action", "review", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            # Nothing meaningful should come after the Action heading except
            # the instruction body. Specifically, no other banner should
            # appear after ACTION CONTEXT.
            action_pos = out.index("ACTION CONTEXT")
            tail = out[action_pos + len("ACTION CONTEXT"):]
            for other in ("WORKER CONTRACT", "TOOL RULES", "PROJECT CONTEXT"):
                self.assertNotIn(other, tail)


class TestEmitSplit(unittest.TestCase):
    """FU-35: --emit {full,system,user} splits the prompt into a
    cache-stable prefix (system) and a per-iteration body (user)."""

    def test_concat_identity(self):
        # full == system.rstrip() + "\n\n" + user — no content lost or duped.
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            _, full, e1 = run_cli(
                "--action", "execute", "--phase", "2", "--emit", "full")
            _, system, e2 = run_cli(
                "--action", "execute", "--phase", "2", "--emit", "system")
            _, user, e3 = run_cli(
                "--action", "execute", "--phase", "2", "--emit", "user")
            self.assertEqual(
                full, system.rstrip() + "\n\n" + user, msg=e1 + e2 + e3)

    def test_full_is_default_emit(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            _, default_out, _ = run_cli("--action", "execute", "--phase", "2")
            _, full_out, _ = run_cli(
                "--action", "execute", "--phase", "2", "--emit", "full")
            self.assertEqual(default_out, full_out)

    def test_system_contains_only_stable_regions(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            _, system, err = run_cli(
                "--action", "execute", "--phase", "2", "--emit", "system")
            self.assertEqual(err, "")
            self.assertIn("WORKER CONTRACT", system)
            self.assertIn("TOOL RULES", system)
            self.assertNotIn("PROJECT CONTEXT", system)
            self.assertNotIn("ACTION CONTEXT", system)

    def test_user_contains_only_volatile_regions(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            _, user, err = run_cli(
                "--action", "execute", "--phase", "2", "--emit", "user")
            self.assertEqual(err, "")
            self.assertIn("PROJECT CONTEXT", user)
            self.assertIn("ACTION CONTEXT", user)
            self.assertNotIn("WORKER CONTRACT", user)
            self.assertNotIn("TOOL RULES", user)

    def test_system_prefix_insulated_from_body_churn(self):
        # The cache-stable prefix must NOT change when only per-iteration
        # state advances (the current step) — that's the cache-hit
        # precondition across consecutive same-phase iterations.
        with TempProject(
            with_framework=True, with_extra=_FRAMEWORK_EXTRAS
        ) as root:
            _, sys_a, _ = run_cli(
                "--action", "execute", "--phase", "2", "--emit", "system")
            _, usr_a, _ = run_cli(
                "--action", "execute", "--phase", "2", "--emit", "user")
            # Advance the body: complete the current pending step.
            steps_path = root / ".state" / "steps.json"
            steps = json.loads(steps_path.read_text(encoding="utf-8"))
            for s in steps:
                if s["phase"] == 2 and s["step"] == 2:
                    s["status"] = "complete"
                    s["commit"] = "abc1234"
            steps_path.write_text(
                json.dumps(steps, indent=2) + "\n", encoding="utf-8")
            _, sys_b, _ = run_cli(
                "--action", "execute", "--phase", "2", "--emit", "system")
            _, usr_b, _ = run_cli(
                "--action", "execute", "--phase", "2", "--emit", "user")
            self.assertEqual(sys_a, sys_b)      # prefix stable → cache hits
            self.assertNotEqual(usr_a, usr_b)   # body changed (step advanced)


class TestAvailableModulesGating(unittest.TestCase):
    """Available Modules drops out of PLAN/REVIEW (dedup with Architecture);
    stays in EXECUTE/CLOSE where Architecture is omitted."""

    def test_omitted_for_plan(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, out, err = run_cli("--action", "plan", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            self.assertNotIn("## Available Modules", out)
            # Architecture still present (its Component Map is the substitute).
            self.assertIn("## Architecture", out)

    def test_omitted_for_review(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, out, err = run_cli("--action", "review", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            self.assertNotIn("## Available Modules", out)
            self.assertIn("## Architecture", out)

    def test_present_for_execute(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, out, err = run_cli("--action", "execute", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("## Available Modules", out)
            # Architecture not in EXECUTE per matrix.
            self.assertNotIn("## Architecture\n", out)

    def test_present_for_close(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, out, err = run_cli("--action", "close", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("## Available Modules", out)
            self.assertNotIn("## Architecture\n", out)


# ---------------------------------------------------------------------------
# Mid-step --section invocations (§10 / Milestone 1.3.D)
# ---------------------------------------------------------------------------


class TestSectionArchitecture(unittest.TestCase):
    def test_renders_verbatim(self):
        arch_text = "# Arch\n\n## Components\n\nfoo\n"
        with TempProject(with_extra={"ARCHITECTURE.md": arch_text}):
            rc, out, err = run_cli("--section", "architecture")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("# Arch", out)
            self.assertIn("Components", out)
            self.assertTrue(out.endswith("\n"))

    def test_missing_degrades_to_placeholder(self):
        with TempProject():
            rc, out, err = run_cli("--section", "architecture")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("<!-- not present: ARCHITECTURE.md not found -->", out)


class TestSectionModule(unittest.TestCase):
    def test_renders_verbatim(self):
        with TempProject(with_extra={
            "ARCH_event_store.md": "# Event Store\n\n## Surface\n\nfoo\n",
        }):
            rc, out, err = run_cli(
                "--section", "module", "--module", "event_store",
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("# Event Store", out)
            self.assertIn("Surface", out)

    def test_missing_exits_1(self):
        with TempProject():
            rc, _, err = run_cli(
                "--section", "module", "--module", "missing_module",
            )
            self.assertEqual(rc, 1)
            self.assertIn("ERROR:", err)


class TestAssetOverrideResolution(unittest.TestCase):
    """§5.3: WORKER_SPEC.md + instructions/<action>.md resolve project-local
    override → packaged default (per file)."""

    def test_packaged_worker_spec_used_without_local(self):
        with TempProject(with_framework=True, with_extra=_FRAMEWORK_EXTRAS):
            rc, out, err = run_cli("--action", "execute", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            # Packaged WORKER_SPEC body (Main Loop heading) is present.
            self.assertIn("Main Loop", out)

    def test_local_worker_spec_overrides(self):
        sentinel = (
            "# Worker Spec\n\n## Sentinel Override Section\n\n"
            "OVERRIDE-WORKER-SPEC-MARKER\n"
        )
        with TempProject(
            with_framework=True,
            with_extra={**_FRAMEWORK_EXTRAS, "WORKER_SPEC.md": sentinel},
        ):
            rc, out, err = run_cli("--action", "execute", "--phase", "2")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("OVERRIDE-WORKER-SPEC-MARKER", out)

    def test_local_instruction_overrides_per_file(self):
        # Override only plan.md; execute.md must still use the packaged default.
        plan_override = "# Plan\n\n## Procedure\n\nOVERRIDE-PLAN-MARKER\n"
        with TempProject(
            with_framework=True,
            with_extra={
                **_FRAMEWORK_EXTRAS,
                "instructions/plan.md": plan_override,
            },
        ):
            rc_plan, out_plan, err_plan = run_cli("--action", "plan", "--phase", "2")
            self.assertEqual(rc_plan, 0, msg=err_plan)
            self.assertIn("OVERRIDE-PLAN-MARKER", out_plan)

            rc_exec, out_exec, err_exec = run_cli("--action", "execute", "--phase", "2")
            self.assertEqual(rc_exec, 0, msg=err_exec)
            self.assertNotIn("OVERRIDE-PLAN-MARKER", out_exec)
            self.assertIn("## Instructions", out_exec)

    def test_resolve_asset_prefers_local(self):
        with TempProject(with_extra={"WORKER_SPEC.md": "# x\n"}) as root:
            resolved = ac.resolve_asset(root, "WORKER_SPEC.md")
            self.assertEqual(resolved, root / "WORKER_SPEC.md")

    def test_resolve_asset_falls_back_to_packaged(self):
        with TempProject() as root:
            resolved = ac.resolve_asset(root, "instructions/plan.md")
            self.assertEqual(resolved, ac.packaged_data_dir() / "instructions" / "plan.md")
            self.assertTrue(resolved.is_file())


if __name__ == "__main__":
    unittest.main()
