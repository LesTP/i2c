# i2c Followups — Design Notes and Tooling Gaps

Running list of items deferred or noted during build sessions. Lower-priority
than the rollout plan phases; revisited when triggers surface (real friction,
Phase 2 pilot feedback, or downstream work that needs the gap closed).

Distinct from `FUTURE_waymark.md` (a roadmap for one specific deferred
initiative) — this is the catch-all log of "noticed during the build, doesn't
block the current deliverable, worth tracking."

ID scheme: `FU-N` (Follow-Up). Status: `open` / `accepted` (will do, scheduled
to a phase) / `partially closed` / `closed` / `wontfix`.

---

## Cold-start summary (next session entry point)

**Where we are (2026-06-09).** i2c data foundation, prose layer,
autonomous-loop foundation, **state lifecycle v1** (FU-30 closed; 7-state
enum: `plan`, `execute`, `review`, `close`, `audit_boundary`,
`audit_escalation`, `done`), and **autonomous-PLAN readiness work**
(FU-32 Δ1/Δ2/Δ4 + `--section phase-summary`) all shipped. CC has run
Phases 2–4 autonomously; Phase 4 is currently at `state=audit_boundary,
phase=4` awaiting the boundary-clear decision before Phase 5+. ARCH
template authored at `ref/SPEC_architecture.md` + `ref/GUIDE_architecture.md`,
awaiting first-real-use validation on CC's next module.

**Epistemic note (carried over from the original Phase 1 pilot debrief,
2026-06-06):** the original clankercourts Phase 1 ran inside the same
continuous session that built i2c itself — operator knew the framework's
contracts intimately and caught friction (FU-12, FU-19, FU-21, FU-22)
before it compounded. "Worker contract held" claims from that period
reflect session discipline, not framework enforcement. Subsequent CC
Phases 2–4 ran autonomously (separate sessions, no operator in the
loop mid-iteration) and validated the framework properly.

FU-32 below carries the live spec for the remaining autonomous-PLAN
readiness work (Δ5 + CC ARCH validation).

**Tooling now available:**
- `python tools/state.py {append,append-record,update-record,append-gotcha} --from-file <path>` for `$`-laden / multi-line payloads (FU-21 closed).
- Bare schema filenames (`steps.json`, `phases.json`, ...) auto-resolve to `.state/<name>` when CWD has `.state/` (FU-19 closed).
- `python tools/state_machine.py` outputs ACTION + NEXT (read-only).
- `python tools/invariants.py --action <name>` checks the post-action invariants from FU-22.
- `python tools/run_iteration.py [--backend claude|codex] [--model sonnet] [--max-budget-usd 5.00]` drives one cold-start worker invocation end-to-end. **Codex backend is functional** (Phase 3.D shipped, modulo the FU-29 Output Contract issue that's been partially patched in adapters).
- `python tools/assemble_context.py --section status | architecture | module | devlog | phase-summary` for single-section views. `--section phase-summary --phase N` is the operator's audit_boundary view (ARCH_assembler.md §8b).
- `python tools/assemble_context.py --step-budget N` controls whether `multi_step_only` subsections appear (default 1 strips; >1 keeps). Runner still hard-codes 1 — multi-iteration loop is Phase 3.C, not yet shipped.

**Active priorities (smallest → largest scope):**
1. **FU-32 Δ4 validation** — author CC Phase 5+ ARCH against the new template (`ref/SPEC_architecture.md`). First real exercise of `## Phasing in This Pilot` + `## Escalation Triggers` in collaborative authoring. Feeds template refinement before Δ5.
2. **FU-32 Δ5** — PLAN precondition check that escalates if ARCH lacks Required sections. Deferred until template stabilizes. ~15 lines of doc once we know what works.
3. **Phase 3.C** — multi-iteration loop. Wrap `run_iteration.py` once we have real data on what single-iteration shape misses. The `multi_step_only` marker mechanism is forward-compatible; runner just needs to start passing `--step-budget > 1`.
4. **FU-7** — tighten `exit_signal.schema.json`. CC has accumulated real exit-signal samples in `logs/loop/iteration_*.txt`; the contract can now be locked from observed shapes.

**Pending operational items:**
- **FU-12** — verified `--from-file` shipped, but adapter `CLAUDE.md` / `CODEX.md` Tool Rules don't recommend it yet. Doc update only.
- **FU-28** — "laptop = supervised-only; server = autonomous" note still missing from Invocation guidance section below.
- **FU-29** — CODEX/CLAUDE adapter Output Contract patched in all four files; full closure waits for a `templates/` layer for adapters.

**Quick orientation commands** (from a project root that already has
`.state/`):

```powershell
$env:PYTHONIOENCODING="utf-8"
python tools\state_machine.py
python tools\assemble_context.py --section status
python tools\assemble_context.py --section phase-summary --phase <N>
python -m unittest discover -s tests
python examples\smoke_test.py
```

To dry-run the runner (writes to `logs/loop/` but invokes a real `claude -p`):

```powershell
python tools\run_iteration.py --backend claude --max-budget-usd 2.00
```

**Canonical references:**
- Build status: `README.md` table
- Assembler contract: `ARCH_assembler.md` (per-section spec; §8b for phase-summary)
- Architectural rationale: `DESIGN_governance_v3.md`; state lifecycle: `DESIGN_state_lifecycle_v1.md`
- ARCH-file authoring template: `ref/SPEC_architecture.md` + `ref/GUIDE_architecture.md`
- Workflow diagrams: `WORKFLOW.md`
- This file: the rolling backlog + live spec for FU-32 (progress log below)
- Pilot project: `p:\shared\clankercourts\` (first real consumer)

---

## Tooling — state.py CLI gaps

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-2 | No subcommand to append a new step mid-phase | partially closed | `state.py append-record steps.json '{...}'` exists (covers PLAN-time and in principle mid-phase). The original framing was specifically about EXECUTE-time step creation; `instructions/execute.md` still funnels deferred work through devlog `Deferred:` flags so PLAN owns step authoring. If a real workflow demands runtime step append from EXECUTE, lift the restriction in execute.md prose - the CLI now supports it. | Phase 2 pilot shows execute-time step append is wanted; loosen the prose. |
| FU-3 | `state.py set` only handles JSON object files, not arrays | partially closed | `update-record` (added for review/close authoring) covers single-record updates on array files via `--match KEY=VALUE`. Generic-set-on-array still isn't supported (e.g., updating fields on N records at once). | A real workflow needs bulk update across multiple records (rare). Until then, `update-record` covers the gap that drove FU-3 in practice. |
| FU-4 | No named lifecycle-transition op (e.g., `state.py escalate --reason "..."`) | open (low priority) | Post-FU-30 the `blocked` field is gone; lifecycle transitions use `state.py set project.json state=<enum>`. The dense `set` syntax works for autonomous and supervised use alike. A named op like `state.py escalate --reason "..."` (or `state.py raise-gate`) could log a structured reason and read more naturally, but adds API surface for little gain. | Revisit if supervised UI grows a need for richer transition flows. |
| FU-14 | No read-side query helper in `state.py` (e.g., `state.py query devlog.jsonl --where 'contracts != []'`) | accepted (deferred) | The assembler (Phase 1.3) exposes pre-formatted views: `--section devlog --phase N` gives a bulleted phase tail, `--section status` an orientation snapshot. Ad-hoc queries still fall back to `jq`. Per ARCH §2 / §10, the assembler intentionally does not absorb general-purpose queries. | Phase 2 pilot reveals a repeated query pattern worth absorbing into `--section`. |
| FU-19 | Instruction examples use bare filenames (`phases.json`) but `state.py` is CWD-relative — works only if CWD is `.state/` | **closed** (Phase 3.A) | See resolution note below. Bare filename auto-resolution shipped via `resolve_state_path()` in `state.py`; instruction examples now work as written without `.state/` prefixes. |
| FU-21 | `state.py {append,append-gotcha,append-record,update-record} --from-file <path>` for multi-line / `$`-laden payloads | **closed** (Phase 3.A) | See resolution note below. `--from-file` shipped across all four payload-bearing subcommands; mutually exclusive with the inline positional. Closes FU-12 in practice once operators adopt the flag. |

## Tooling — assembler (`assemble_context.py`)

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-7 | `exit_signal.schema.json` is permissive (`additionalProperties: true`) | open (actionable) | Schema is deliberately loose for v1. Autonomous runs have shipped (Phases 2–4 on CC), and `logs/loop/iteration_*.txt` carries real exit-signal samples to lock the contract from observed shapes. | Read the CC corpus, settle the required field set, set `additionalProperties: false`, validate the runner emits what it claims. |
| FU-15 | `Module Contract` section is hard-required when `phases.json[current].module` is set | open (mitigated by discipline) | Per ARCH §11.1 / §4.1, the assembler exits 1 if a phase declares a `module` field but no `ARCH_<module>.md` file exists. This is strict by spec — useful for catching missing contracts early — and the FU-32 Δ4 ARCH-authoring discipline (per `ref/SPEC_architecture.md`) now codifies pre-authoring as the standard: every module's ARCH is written during architecture-phase work *before* any PLAN runs on that phase. **Original CC pilot (2026-06-05)** workaround (pre-author `ARCH_resolver.md` at bootstrap) is now the documented norm rather than a workaround. | Only revisit if a future workflow genuinely needs to plan against an unwritten ARCH (e.g., research/spike phases). For now the strictness is correct. |
| FU-16 | Available Modules ARCHITECTURE.md fallback is naive | open | When the adapter's `## Available Modules` section is placeholder-only, the assembler grabs `## Implementation Sequence` from `ARCHITECTURE.md` verbatim and surfaces its body. If projects use richer Implementation Sequence tables (extra columns, longer prose), the rendered Available Modules section will be noisy. | Phase 2 / 3 pilots show real-world Implementation Sequence tables overflow the section. Tighten the fallback to extract only module names, or document a project convention for the fallback shape. |
| FU-17 | `--phase` accepted (but ignored) with `--section status` | open | Per ARCH §8, `--section status` does not accept `--phase` — it always reports on `project.json.phase`. The implementation accepts `--phase` at the argparse layer and silently uses `project.json.phase` in `build_section_status`. Functionally correct but not strict per spec. | Either Phase 2 pilot surfaces confusion (operator passes `--phase 3` and expects status for phase 3), OR a spec-compliance pass. Fix is one branch in `_validate_args`: reject `--phase` when `--section == status`. |
| FU-18 | Assembler tests slow on Windows network share | open | `tests/test_assemble_context.py` runs in ~60s on `\\192.168.0.50\shared\...`. Primary cost: `TempProject(with_framework=True)` copies the full `instructions/` directory and WORKER_SPEC + both adapters per test invocation. | If iteration cost becomes painful, refactor `TempProject` to copy only what each test class needs (most renderer tests don't read instructions), or cache the framework copy per pytest session. Not a correctness issue. |
| FU-23 | Assembler `--section status` omits `Budget:` line when `budget_type` is set but no counter populated | open (pilot-cosmetic) | clankercourts' `project.json` after Phase 1 close has `budget_type: "steps"` but no `steps_remaining`. The renderer's check is `if "steps_remaining" in p` and `elif p.get("budget_type") == "time" and "time_budget_seconds" in p` — both branches need the counter present. Result: the Budget line is silently omitted. Correct per ARCH §8 (which shows the line with a counter), but a stronger snapshot would render `**Budget:** steps (no remaining count set)` so the operator sees the mode even when the runner hasn't populated the count yet. | Cosmetic; address opportunistically. |
| FU-34 | Additional `--section` projections for codexbot consumers | open (partially closed by `--section phase-summary`) | `--section phase-summary --phase N` shipped 2026-06-09 (covers operator's boundary-review use case). Two more projections still useful for codexbot integration: `--section escalation --phase N` (last `escalate` devlog entry + surrounding context: preceding 3 entries + relevant decisions); `--section iteration --iter N` (captures from `logs/loop/iteration_NNN.txt` plus state snapshot at that point). Each ~50-100 LOC. Together replace the "20 screens of markdown dump" pattern with targeted structured projections for downstream UIs (`/audit`, `/escalation`, `/logs`, `/review`). | Codexbot integration session starts implementing `/escalation` and `/logs` commands and needs structured projections instead of raw file reads. |

## Tooling — runner

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-22 | Runner post-close invariant check - assert `blocked == true` + current phase `status: complete` after every CLOSE | **closed** (Phase 3.A) | See resolution note below. Shipped as `tools/invariants.py` (`check_post_action(root, action)`); the single-iteration runner calls it after every CLOSE dispatch and halts-and-surfaces on failure. Reusable from supervised tooling too. |
| FU-33 | Runner doesn't surface token/quota counts in `summary.log` | open | `run_iteration.py` parses claude's `--json` output (and codex JSONL) for exit-signal handling but doesn't extract `usage_input_tokens` / `usage_output_tokens` and surface them. Codexbot's eventual `/cost` or `/tokens` command needs per-iteration cost visibility, and the operator wants the same signal during supervised runs. Pirozhok README has an open item "Codex cost extraction from --json output" that maps to this. | Add `tokens_in=N tokens_out=M` (and optionally `tokens_cumulative=K`) to each `summary.log` line. Cross-provider: same field names, populated when extractable, `n/a` otherwise. ~30 LOC in `run_iteration.py`. |
| FU-35 | Runner doesn't emit Anthropic `cache_control` markers around stable prompt prefix | open | Wrapping WORKER CONTRACT and TOOL RULES regions with Anthropic's ephemeral cache_control blocks lets us hit the prompt cache on iter 2+ within a phase — meaningful cost win once Phase 3.C ships multi-iteration. Other providers (OpenAI, Gemini) auto-detect identical prefixes without explicit markers, so the current prompt shape already benefits them; this item is Anthropic-specific. | Ship after Phase 3.C (multi-iteration loop). ~10 LOC in `run_iteration.py`'s claude invocation path. Verify cache hits by observing `cache_read_tokens` in API response on the second iteration of a phase. |
| FU-32 | PLAN action not yet autonomous-capable; needs five framework deltas + ARCH-file discipline | **partially closed** (in progress; see progress log below) | After CC Phase 4 EXECUTE shipped supervised (commit `97e9ea4`), the meta-question surfaced: i2c's autonomous loop runs EXECUTE/REVIEW/CLOSE cleanly, but PLAN's step-breakdown step still requires human authoring because ARCH files aren't constrained enough to drive mechanical step decomposition. e2e solves this via a two-step workflow (pre-arch design separately, autonomous batch implementation); i2c lacks the ARCH-authoring discipline and the safety-net escalation triggers that make autonomous PLAN safe. Five deltas identified — see the progress log below for current state and the Δ5 spec. | Continue with CC Phase 5+ ARCH authoring against the new template; Δ5 follows once the template is validated. |

### FU-32 progress log

This entry carries both the historical record and the live spec for any
remaining deltas. When the last delta (Δ5) lands, FU-32 flips to fully
closed and the live-spec subsection is dropped.

**Decisions closed (Q1–Q5 from the original plan):**

- **Q1 — template placement.** `i2c/ref/` (operator/assistant reference, not assembler input). 2026-06-09.
- **Q2 — Δ5 escalation names missing section.** Yes — devlog message names which Required section is missing. To be implemented when Δ5 lands. 2026-06-09.
- **Q3 — decisions.json phase field optional or required.** Optional (existing records lack it; back-fill via `state.py update-record decisions.json --match id=D-N phase=K`). 2026-06-09.
- **Q4 — phase-end review checklist.** Defer. Insufficient autonomous phase-boundary review experience to write a checklist that holds up. After 2–3 autonomous phases under the new template, what gets missed becomes empirical. Tracked as a potential Δ6. 2026-06-09.
- **Q5 — what counts as a "module".** Don't constrain. Template supports per-module ARCH (default), combined-spec single-file (for projects where boundaries aren't real), MVP/full split (phosphene pattern, for staged delivery). 2026-06-09.

**Path picked:** Path A (CC-first authoring → codify framework deltas from lessons). Inverse of the original plan's Path B preference; landed because the ARCH template needs validation in real authoring before its details lock. 2026-06-09.

**Deltas:**

- **Δ1 — optional `phase: integer` field on `decisions.schema.json`.** ✓ Shipped 2026-06-09. Schema accepts the field; `state.py update-record` validates correctly; back-filled on CC's D-18/D-19/D-20 (Phase 4 decisions). 4 schema tests + integration with `--section phase-summary`. ~10 LOC + 4 tests.
- **Δ2 — `plan.md` escalation triggers enumeration.** ✓ Shipped 2026-06-09. Added step 2.5 to `instructions/plan.md`: 5 project-general triggers (source-vs-ARCH drift, multi-regime scope, cross-module breakage at plan time, step-shape ambiguity, dep-probe contract mismatch) in a table with detect-when / reason-string / resolution columns; plus module-specific triggers reference (pulls from ARCH's `## Escalation Triggers` section per Δ4 template); plus how-to-escalate snippet. Synced to CC. Verified via the `test_plan_autonomous_smoke` assembler test (asserts all 5 trigger names travel into the worker prompt). ~58 lines of doc; no code.
- **Δ3 — `--section decisions --phase N`.** ✓ Obviated 2026-06-09 by the broader `--section phase-summary --phase N` (same filter + steps + devlog + open items + header). Δ3 dropped as a standalone deliverable.
- **Δ4 — ARCH template port from e2e + augment.** ✓ Authored 2026-06-09 at `i2c/ref/SPEC_architecture.md` + `i2c/ref/GUIDE_architecture.md`. **v2 pass shipped 2026-06-09** — restructured around Pattern A (per-module ARCH files) / Pattern B (single-document ARCHITECTURE.md) collapse after reviewing operator's prior projects (lyonel, noise-machine, PoP_port) showed 0/3 used the per-module shape that v1 documented as the default. Pattern B added with optional Layer Contracts sub-section (PoP_port exemplar) and worked-example pointers covering its expressive range — flat (lyonel), multi-phase (noise-machine), layered (PoP_port). Required / Recommended / Optional taxonomy preserved within each pattern. Original 4-pattern sketch (Combined-Spec-small, Combined-Spec-multi-phase, Single-doc-with-inline-contracts, Per-module) collapsed to 2 because the first three are operator-choice variations within Pattern B, not separate authoring disciplines. Escalation Triggers re-classified from "0/11 organic" (v1) to "1/12 organic" after PoP_port's Layer 1 `**Escalation triggers:**` was added to the sample — the pattern is rare-but-real, not novel. SPEC: 480 lines; GUIDE: 749 lines. **Awaiting first-real-use validation** on CC Phase 5+ ARCH (Pattern A) and on a future Pattern B consumer.
- **Δ5 — PLAN precondition check on ARCH completeness.** Deferred until Δ4 template validates (≥1 real CC ARCH authored + 1 autonomous phase run under it). **Spec (lands in `instructions/plan.md` step 1 or 2):** PLAN reads the assembled `Module Contract` section; if it lacks `## Phasing in This Pilot` or `## Escalation Triggers`, worker writes a devlog entry naming the missing section (per Q2) and sets `state=audit_escalation` with reason `"ARCH lacks autonomous-PLAN-ready sections — needs collaborative authoring session per ref/SPEC_architecture.md"`. ~15 lines of doc; or ~10 LOC if enforced at the assembler level instead (worker procedure is simpler — defer the choice to implementation time). Cheap to add once Δ4 stabilizes.

**Adjacent work shipped this session:**

- **`--section phase-summary --phase N` on the assembler.** Operator's `state=audit_boundary` view: header + steps + decisions-added-this-phase (Δ1-dependent filter) + phase devlog + open items. Distinct from `--section status` (project-wide, current-state) and `--section devlog` (just the devlog tail). Spec in `ARCH_assembler.md` §8b. ~80 LOC + 10 tests. Validated against CC Phase 4 end-to-end (3 phase-tagged decisions surfaced cleanly; 17 untagged decisions properly noted via back-fill footer; full step+devlog narrative reads in one screen-and-a-half).

### Invocation guidance: running the loop from an i2c-consumer project

Once `tools/run_iteration.py` ships (Phase 3.A), the canonical invocation from
a consumer project (e.g. clankercourts) is:

```powershell
cd /path/to/your-consumer-project
python3 ../i2c/tools/run_iteration.py --backend claude --model sonnet --max-budget-usd 5.00
```

**Why run from i2c upstream rather than copy the runner into the consumer.**
The runner imports `assemble_context`, `state_machine`, `invariants`, and
`validate` as Python siblings. Python adds the script's directory
(`i2c/tools/`) to `sys.path[0]`, so all framework code resolves to i2c
upstream. The consumer-local `tools/` is only invoked when the **worker's
procedure text** tells it to (`python3 tools/state.py ...`) — at which point
CWD is the consumer root (set explicitly via `subprocess.run(..., cwd=root)`),
so the consumer's local `state.py` runs.

This gives a clean version split:

- **Framework pipeline** (runner, state_machine, assembler, invariants) → i2c
  upstream. One source of truth, hermetic upgrades.
- **Worker tool surface** (`tools/state.py` invoked by the worker for state
  writes) → consumer-local, **but must be ABI-compatible with the procedure
  text**. ABI here means both the subcommand set *and* the argument-form
  features the procedures assume (FU-19 bare-filename auto-resolve;
  FU-21 `--from-file` payload path; future flag additions). The
  consumer-local copy must move in lockstep with i2c upstream — sync it
  every time you sync `instructions/*.md`. There is no "may lag" mode that
  is actually safe; pre-FU-19 state.py fails every worker write because
  the procedure examples write bare filenames.
  lacks.
- **Procedure prose** (`instructions/*.md`) → consumer-local. The assembler
  reads whatever the consumer has on disk and embeds it in the prompt.
- **Project state + contracts** (`.state/`, `PROJECT.md`, `ARCH_*.md`) →
  consumer-local, as expected.

**Preflight diff — before each consumer's first autonomous run, and after
each i2c upstream pull.** Compare these files between the consumer and i2c
upstream:

PowerShell:

```powershell
cd /path/to/your-consumer-project
foreach($f in @(
  'instructions/plan.md','instructions/execute.md',
  'instructions/review.md','instructions/close.md',
  'tools/state.py','tools/assemble_context.py','tools/validate.py'
)) {
  $cc = "$f"; $i2 = "../i2c/$f"
  if ((Test-Path $cc) -and (Test-Path $i2)) {
    $h1 = (Get-FileHash $cc -Algorithm SHA256).Hash
    $h2 = (Get-FileHash $i2 -Algorithm SHA256).Hash
    if ($h1 -eq $h2) { Write-Host "MATCH    $f" }
    else { Write-Host "DIFFER   $f  delta=$((Get-Item $i2).Length - (Get-Item $cc).Length)" }
  }
}
```

bash:

```bash
cd /path/to/your-consumer-project
for f in \
  instructions/plan.md instructions/execute.md \
  instructions/review.md instructions/close.md \
  tools/state.py tools/assemble_context.py tools/validate.py
do
  if [ -f "$f" ] && [ -f "../i2c/$f" ]; then
    if cmp -s "$f" "../i2c/$f"; then echo "MATCH    $f"
    else
      d=$(( $(stat -c%s "../i2c/$f") - $(stat -c%s "$f") ))
      echo "DIFFER   $f  delta=$d"
    fi
  fi
done
```

Acceptance criteria for invoking from i2c upstream:

| File | Must match? | If divergent |
|---|---|---|
| `instructions/*.md` | **Yes** | Worker reads stale procedure text the assembler (newer) and invariants (newer) may reject or work against. Sync into the consumer with an explicit commit before the autonomous run. |
| `tools/validate.py` | **Yes** | Schema validation must agree across the pipeline. Sync. |
| `tools/state.py` | **Yes** (procedures depend on FU-19 / FU-21 features) | Pre-FU-19 state.py fails every worker write — instruction examples write bare filenames like `state.py append devlog.jsonl '...'` and require auto-resolve to `.state/devlog.jsonl`. Pre-FU-21 state.py works only with shell-quoted JSON (PowerShell `$`-interpolation surface area). Sync state.py whenever you sync `instructions/*.md`. |
| `tools/assemble_context.py` | No (i2c is canonical) | Read from i2c upstream via sibling import. The consumer's local copy is unused for autonomous runs and may safely lag. |

**Alternative — pin framework into the consumer (hermetic builds).** Copy
`tools/{run_iteration,state_machine,invariants}.py` from i2c and overwrite
`tools/{state,assemble_context}.py` and `instructions/*.md` in the consumer,
then invoke `python3 tools/run_iteration.py` locally. Each i2c upgrade
becomes a "framework snapshot" commit in the consumer's history. Use when
the consumer needs framework versions pinned to commits in its own repo
(audit, compliance, or air-gapped deploy scenarios).

(First documented during CC autonomous-loop preflight, 2026-06-06.)

## Prose — instructions, WORKER_SPEC, adapters

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-8 | execute.md commit-format suggestion (`phase.step: title`) is not enforced anywhere | open | The prose says "default commit message format `phase.step: short title`" but nothing validates it. A pre-commit hook or a `state.py complete --validate-commit-msg` check could enforce. | Phase 2 pilot reveals workers drift from the format and downstream tooling (codexbot `/diff <phase>`, waymark commit-by-phase view) needs consistency. |
| FU-9 | Refine regime in execute.md uses `step: null` for devlog entries | open | The schema allows `step: null` and the prose recommends it for Refine iterations. But there's no constraint that ties a Refine entry to *which* iteration (no iteration counter field). The commit message carries it (`14.iter3:`) but the structured data doesn't. | Phase 2 pilot does enough Refine work that iteration-by-iteration analytics matter. Add `iteration: int` optional field to `devlog_entry.schema.json`. |
| FU-10 | Production-incident anecdotes in WORKER_SPEC §3 are e2e-vintage | open | Per D-prose-8 the Codex 105k-char and Claude 5-3 incidents stay verbatim — they have pedagogical value. But once i2c has its own incidents, those should be added or substituted to keep the pedagogy current. | i2c accumulates 2+ documented loop-discipline failures of its own. Add a refresh pass to WORKER_SPEC §3. |
| FU-11 | Per-file JSON-example validation isn't automated | open | The only check that `instructions/*.md` examples validate against the schemas is manual. A test that lifts every fenced JSON block in `instructions/**.md` and validates against the registered schema would catch drift. | A schema change breaks an instruction example silently. Pattern: parse markdown code fences, route by surrounding prose hint or filename hint. |
| FU-26 | `close.md` and `plan.md` disagree on who advances `project.json.phase` | **closed** (2026-06-08 lifecycle redesign) | See resolution note below. |
| FU-29 | i2c's `CODEX.md` and `CLAUDE.md` adapters lacked an inline `## Output Contract` section; codex skips the 5-line exit signal as a result | open (pilot-confirmed; partial fix applied) | The e2e template (`templates/CODEX_worker.md` line 136) and diplomat (`CODEX.md` line 188) both ship an explicit `## Output Contract` section that reads *"End every invocation with exactly these five lines — no additional text after"* plus the 5-line example and an exit-code table. i2c's adapters only reference `WORKER_SPEC.md` (which contains the contract) without inlining it. Claude is robust enough to follow the contract from the reference alone; codex is not. **CC Phase 3 iter 15 (2026-06-07):** first codex-on-i2c production run after the runner gained `--backend codex` support. Codex completed step 3.4 correctly — commit `f7620cc`, 228/228 tests pass, `state.py` writes for steps.json and devlog.jsonl landed coherently — but emitted prose-only output with no 5-line EXIT signal. Runner correctly reported `exit=2 "signal missing or malformed"` even though the work was substantively successful. **Partial fix applied (2026-06-07):** ported the e2e Output Contract section verbatim (with i2c-specific wording — `.state/project.json` rather than `DEVPLAN`) into `i2c/CODEX.md`, `i2c/CLAUDE.md`, `clankercourts/CODEX.md`, `clankercourts/CLAUDE.md`. Section sits between `## Runner Info` and `## Mode`. Re-firing the next codex iter on clankercourts validates the fix. | **Remaining work for full resolution:** (a) once i2c grows a `templates/` directory for bootstrap adapters (today the top-level CODEX.md / CLAUDE.md double as both reference and template), make sure the templates include the Output Contract section so every new i2c project ships with it; (b) consider whether claude adapters genuinely need it — claude historically follows the contract from WORKER_SPEC alone, but inlining is belt-and-suspenders and harmless. The CC pilot's own adapters have been patched, so this FU stays open until the template-layer fix lands. |

## Cross-platform

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-12 | Multi-line JSON / `$`-laden state.py payloads on PowerShell | **closed** (2026-06-09) | See resolution note below. Code fix shipped via FU-21 (`--from-file`); adapter Tool Rules now recommend the `--from-file` path in all four CLAUDE/CODEX adapters (i2c + CC). |
| FU-20 | Templates assume `.claude/commands/` autoloads in Devmate; project-level `.llms/commands/` also doesn't get picked up in the workflows seen so far — operator-global `~/.llms/commands/` is the only reliable surface | open (pilot-confirmed) | `templates/README.md` states "Devmate / Claude Code picks up the project's `.claude/commands/` automatically — no extra configuration step." True for Claude Code, false for Devmate. Devmate's `agent_customization` skill documents `.llms/commands/` as the project-level convention. **CC pilot (2026-06-05/06) found both don't work in practice for the current Devmate session:** the operator's Devmate session is reading commands from `C:\Users\myeluashvili\.llms\commands\` only; neither `p:\shared\clankercourts\.claude\commands\*.md` nor `p:\shared\clankercourts\.llms\commands\*.md` showed up in the personal_context skills list. Possible causes: network-share path not scanned, workspace-root mismatch, requires Devmate restart to pick up new project-level commands, or project-level scanning not actually implemented for this Devmate build. **Workaround applied:** copied the 5 i2c slash commands to `~/.llms/commands/i2c-*.md` (operator-global with `i2c-` prefix). Now `/i2c-phase-plan`, `/i2c-cold-start`, etc. are available in any Devmate session; global `/phase-plan` continues to call e2e (Diplomat workflow unaffected). The commands shell out to `python tools/...` so they only do useful work inside an i2c project root; elsewhere they fail with a clear error. | Address before next i2c bootstrap: **(a)** update templates to ship the `i2c-` prefixed commands at both `templates/.claude/commands/` (for Claude Code) and `templates/.llms/commands/` (for Devmate at project-level if/when that works), AND document a manual copy-to-global step for Devmate users (`xcopy templates\.llms\commands\* %USERPROFILE%\.llms\commands\`); **(b)** investigate whether Devmate project-level `.llms/commands/` actually loads — may be a config / workspace setup question, or a network-share limitation. If (b) confirms project-level works under some setup, document the prerequisites. |
| FU-27 | Windows `Path.cwd().resolve()` expands mapped network drives to UNC; breaks `subprocess.run(cwd=...)` for `claude.exe` | **closed** (2026-06-06) | See resolution note below. `find_project_root` now uses `.absolute()`, preserving the mapped-drive letter and avoiding the UNC-cwd that Windows CMD rejects under `claude.exe`'s plugin loader. |
| FU-28 | Meta laptop sandbox prevents `claude -p` subprocess autonomy; consumer projects must invoke the loop from a server | **wontfix** (2026-06-09) | See resolution note below. Constraint is real and unchanged; documentation deemed unnecessary — operator has internalized that autonomous runs always invoke from `pirozhok` over SSH, supervised runs happen on the laptop. The original misfire was a miscommunication about *where* the loop was running, not a missing constraint doc. |

---

## Closed / decided

Items resolved, with a one-line resolution note. Historical context is cheap.

| ID | Resolution |
|----|------------|
| FU-2 (CLI side) | `append-record` subcommand added when authoring `instructions/plan.md` surfaced that PLAN needs to write new records to all three JSON-array files (steps, phases, decisions). Generic over per-record-type subcommands per design discussion: same shape as the existing `append` for JSONL, schema-validated, atomic. The prose-side framing (EXECUTE defers to PLAN via `Deferred:`) is unchanged — the CLI gap that drove FU-2 is closed but the design rule it embodied stands. |
| FU-5 | Phase 1.3 `tools/assemble_context.py` implements the conditional-section marker mechanism per ARCH §7 (evaluator registry, `requires=dependencies_nonempty`, `autonomous_only`, `supervised_only`). Markers in `instructions/plan.md` and `instructions/close.md` strip deterministically. |
| FU-6 | Phase 1.3 tests cover both leaf and non-leaf paths; `examples/smoke_test.py` also exercises `--section status` end-to-end. |
| FU-13 | `update-record FILE --match KEY=VALUE field=value ...` added when authoring `instructions/review.md` + `close.md` surfaced the need to close open decisions and flip phase status mid-flight. Generic: matches one record by a single key=value (errors on no-match or multi-match), updates one-or-more fields, validates the whole array, atomic write. Sibling to `append-record` in pattern. |
| FU-19 | Phase 3.A: `tools/state.py` ships `resolve_state_path()` — if `arg` doesn't exist and CWD has a `.state/` directory and `arg` is a bare schema filename (any key of `SCHEMA_BY_FILENAME` or `devlog.jsonl`), auto-resolve to `.state/<arg>`. Applied in every payload-handling subcommand. Instruction examples now work as written without a `.state/` prefix; explicit paths still work unchanged. Test coverage in `tests/test_state.py::TestResolveStatePath` (6 cases). |
| FU-21 | Phase 3.A: `--from-file PATH` flag landed on `append`, `append-record`, `update-record`, `append-gotcha`. Manually-enforced mutex with the positional payload (argparse's `add_mutually_exclusive_group` doesn't compose with `nargs='*'` positionals). For `update-record`, the file content must be a JSON object of field updates; for the other three it's UTF-8 text (JSON for append/append-record; plain prose for append-gotcha). Bypasses PowerShell `$`-interpolation and heredoc edge cases. Test coverage in `tests/test_state.py::TestFromFile*` (14 cases including the `$`-laden gotcha round-trip from FU-12). |
| FU-22 | Phase 3.A: `tools/invariants.py` provides `check_post_action(root, action)` returning a list of failure messages. v1 invariants cover CLOSE (`blocked == true` + phase `status == complete`), REVIEW (`state == close`), PLAN (`state == execute`), EXECUTE (`state ∈ {execute, review}`). `tools/run_iteration.py` calls it after every CLOSE dispatch and halts-and-surfaces (exit 2) on failure. Reusable from supervised tooling — operators can run `python tools/invariants.py --action close` after a manual close to catch drift. Test coverage in `tests/test_invariants.py` (18 cases). |
| FU-24 | Phase 3.A.1: prompt compaction + region reorder shipped after reading a real assembled prompt (~744 lines / ~70 KB). Two new evaluators (`multi_step_only`, `omit_in_prompt`) added to `assemble_context.py` plus a `--step-budget` flag. `WORKER_SPEC.md` multi-step subsections + production-incident anecdotes marked `multi_step_only` (strips at the v1 single-step default); shell-command discipline moved into `CLAUDE.md` and `CODEX.md` Tool Rules; `instructions/*.md` Examples / Known tooling gaps / Behavior modes marked `omit_in_prompt`; `Available Modules` gated to EXECUTE/CLOSE only (dedup with `Architecture`); regions reordered to **WORKER CONTRACT → TOOL RULES → PROJECT CONTEXT → ACTION CONTEXT** so the procedure lands at the prompt tail where model recency works in our favor. Net: ~55% token reduction with no information loss for what the action actually needs. Test coverage in `tests/test_assemble_context.py` (14 new cases: multi_step_only, omit_in_prompt, --step-budget validation, region order, Available Modules gating). |
| FU-25 | Phase 3.A.2: `Decisions` table dropped from EXECUTE recipe in `_PROJECT_CONTEXT_BY_ACTION`. Project-wide decision history is reference, not per-step load-bearing; PLAN / REVIEW / CLOSE still include it. Worker can pull mid-step via `--section` if needed. ~22 lines saved per EXECUTE iteration (multiplied across N steps per phase). Test coverage: `test_execute_includes_step_and_recent_activity` asserts `## Decisions` is absent; `test_decisions_present_for_plan_review_close` guards against accidental drop on other actions. Build/Refine regime split deferred per session discussion — savings are smaller and risk of harming silent-drift recognition outweighs the win until autonomous evidence accrues. |
| FU-1 | **Wontfix.** Phase 3 close (2026-06-07): `in_progress` dropped entirely from `phases.json` and `steps.json` schemas. Binary `pending` / `complete` is sufficient — the active phase is identified by `project.json.phase`, not by an in-flight status field; the active step is identified by being the lowest-numbered `pending` step in the current phase. Same logic resolves the previously-undocumented contradiction between `plan.md` (leaves phases `pending`) and `close.md` (had expected `in_progress`). Both procedure files now agree on binary status. The "promote pending → in_progress" CLI op (FU-1's original ask) is no longer needed; no code path writes the dropped value. `update-record` covers any remaining mid-flight field updates on phase/step records. Files touched: `schemas/{phases,steps}.schema.json`, `instructions/{plan,execute,close}.md`, mirrored to clankercourts. See iter 21 / iter 22 of CC autonomous loop for the symptom that surfaced this. |
| FU-31 | Post-clankercourts-Phase-3 audit (2026-06-07) surfaced that `instructions/close.md` had no step for `ARCHITECTURE.md`. Only step 5 covered per-module `ARCH_<module>.md` propagation and step 7 covered optional PROJECT.md risks; the project-wide `ARCHITECTURE.md` (Implementation Sequence table + Component Map + Coupling Notes + Key Decisions summary) was nobody's job. Result: clankercourts' Implementation Sequence still said Phases 1-3 were "Not started" even though `.state/phases.json` had them all `complete`. Fix: inserted new step 7 "Update ARCHITECTURE.md" (renumbering 7→8 through 11→12), placed after the decisions-closing step so it can reference closed decisions in the optional Key Decisions summary update. Implementation Sequence status flip is required; Component Map / Coupling Notes / Key Decisions edits are optional and conditional on what the phase actually changed. Commit step now includes `ARCHITECTURE.md` in the default `git add`; both worked examples and devlog summary examples updated. Mirrored to clankercourts (`instructions/close.md` hashes match). Clankercourts ARCHITECTURE.md immediate-drift fix (Phases 1-3 → Complete) shipped in the same session. **Note:** commits 41bcc9d (i2c) and aa08991 (CC) reference this work as "FU-29" by mistake — the open FU-29 (CODEX.md output contract) was already in use at the time; the entry was renumbered to FU-31 in Stack E of the state-lifecycle redesign. |
| FU-30 | Stack A–D of the state-lifecycle redesign (DESIGN_state_lifecycle_v1.md, 2026-06-08) replaced the three-way overload of `blocked` with the 7-state `state` enum (`plan`, `execute`, `review`, `close`, `audit_boundary`, `audit_escalation`, `done`). State machine returns `ACTION: EXIT` for all three halt states; `audit_boundary` covers post-CLOSE gate (was: `state=close, blocked=true`); `audit_escalation` covers mid-phase halts (was: any `blocked=true` set by EXECUTE/REVIEW escalations); `done` is the terminal state — distinct from `audit_boundary`, recoverable only by deliberate `set state=plan` write. The terminus ambiguity FU-30 originally flagged is gone: `done` and `audit_boundary` are different enum values with different machine behavior and different human-recovery procedures. Conservative closure per D-state-3: CLOSE worker always transitions to `audit_boundary` and never sets `done` directly — the human/wrapper makes the "more phases or terminate" call. Files touched: schema, state_machine, invariants, assembler renderer + tolerance, every instruction file, WORKER_SPEC, CLAUDE/CODEX adapters, slash-command templates, DESIGN_governance_v3 banner, fixture + smoke test + 50+ tests; mirrored to clankercourts with `.state/project.json` migrated in place. Commits: i2c 224aaf5 (memo), e2a71ec (Stack A), 9e53e62 (Stack B), a4d88b5 (Stack C); CC 1c126db (Stack D). |
| FU-26 | Lifecycle redesign (FU-30) collapsed the contradiction. Today's procedure: `close.md` step 12 sets `state=audit_boundary` and does NOT touch `phase`; the human/wrapper clears the gate by setting `phase=N+1 state=plan` atomically; `plan.md` step 1 catches mis-dispatch against a completed phase by escalating to `audit_escalation` (reason "plan called on completed phase"). All three positions now agree. No code changes beyond the lifecycle work itself; verified in current `instructions/{close,plan}.md` (2026-06-09). |
| FU-27 | `tools/assemble_context.py::find_project_root` switched to `.absolute()` instead of `.resolve()` (2026-06-06). `.absolute()` preserves the mapped-drive letter (avoids UNC expansion that breaks Windows CMD `cwd=` for `claude.exe`'s plugin loader); doesn't normalize `..` segments but that's irrelevant for CWD-derived paths. POSIX behavior unchanged. |
| FU-12 | Code fix shipped via FU-21 (Phase 3.A: `--from-file PATH` on `append`, `append-record`, `update-record`, `append-gotcha`). Doc fix shipped 2026-06-09: all four CLAUDE.md / CODEX.md adapters (i2c + CC) now carry a Tool Rules bullet recommending `--from-file` for multi-line or `$`-laden payloads, with a one-sentence pointer to the PowerShell interpolation gotcha. Closes the production hazard end-to-end. |
| FU-28 | **Wontfix** (2026-06-09). The technical constraint is real: Meta-issued Windows laptops cannot run `claude -p` as a subprocess (sandbox restricts non-interactive child-process semantics; CC autonomous-loop iteration 1 attempt hung 25min before manual kill). The standard operational practice — invoke `run_iteration.py` from `pirozhok` over SSH, Samba-mounted shared disk so `.state/` is seen by both laptop and server — has been in production since 2026-06-06 and worked cleanly for CC Phases 2–4. Original incident was a miscommunication about where the loop was running, not a missing constraint doc. Operator has internalized the laptop-vs-server rule and explicitly declined the doc-side fix. |

---

## How to use this file

- When you notice a gap or design note during a build session, add it as a new `FU-N` row in the right section. One-line title, brief context, explicit trigger.
- When you act on one, move it to **Closed / decided** with a one-line resolution note. Don't delete — historical context is cheap and useful.
- Reference these IDs from instructions/, plans, or commit messages when relevant.
- This file does not gate any phase. It is a backlog, not a blocker list.
- When picking up after a break, read the **Cold-start summary** above first — it captures where things were left and what to do next.
