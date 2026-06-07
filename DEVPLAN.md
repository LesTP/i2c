# i2c — Active Work

This file tracks i2c framework work that is **committed and in flight**, in
contrast to `FOLLOWUPS.md` (deferred items that may or may not happen).
Items here get DONE or REMOVED. When all checkboxes in an active wave flip,
the wave section gets archived in a commit (or moved to a "Last completed
wave" line for git-blame breadcrumb).

---

## Wave: codexbot integration (started 2026-06-07)

**Goal.** Ship the framework-side pieces required for codexbot to dispatch
and inspect i2c projects with the same UX as e2e projects. Each item is
independently testable against clankercourts; no coordination with the
consumer side is needed until that side starts pulling on these contracts.

See `../codexbot/DEVPLAN.md` for the matching consumer-side wave that
depends on (some of) the items below.

Sequence them in any order. Recommended starting point is auto-advance
because it's smallest and resolves an existing FOLLOWUPS entry; the
others are mostly independent of each other.

### Items

- [ ] **Auto-advance phase at CLOSE** — resolves FU-26. close.md procedure
      grows a final step: worker sets `state=plan`, `phase=<lowest pending
      phase id>`, `blocked=true` in a single state.py call (or atomic
      sequence). The "no more pending phases" branch should set
      `blocked=true` and leave `phase` alone; `state_machine.py` should
      then dispatch EXIT. Update FU-26 in FOLLOWUPS.md to "closed" with
      a one-line resolution note. Verify against clankercourts by closing
      a phase and confirming the gate-clear is one field
      (`blocked=false`).

- [ ] **Runner `--step-budget N` flag** — mirrors e2e STEP_BUDGET=N.
      `run_iteration.py` accepts the flag; passes through to the worker
      prompt via `--step-budget`; assembler's `multi_step_only` marker
      already gates the LOOP discipline sections so this should plumb
      cleanly. WORKER_SPEC §2 multi-step subsection is already authored.
      Verify against clankercourts by running a phase with
      `--step-budget 3` and confirming 3 EXECUTE actions complete in one
      worker invocation. This unblocks codexbot's `/batch N to-review`
      for i2c projects.

- [ ] **Token / quota fields in summary.log** — runner parses claude
      `--json` output (codex JSONL when codex is wired) for
      `usage_input_tokens` and `usage_output_tokens`; appends
      `tokens_in=N tokens_out=M` to each summary line. Optionally
      `tokens_cumulative=K` per rolling window. Cross-provider — same
      fields, populated when extractable, `n/a` otherwise. Surfaces in
      downstream UIs without requiring per-provider rendering. Pirozhok
      README open item "Codex cost extraction from --json output" lives
      here.

- [ ] **Assembler `--section {decisions, escalation, iteration}`
      projections** — three new section types in `assemble_context.py`
      for structured artifact rendering:
      - `--section decisions [--status open|closed]` — filtered decision
        records as a flat table
      - `--section escalation --phase N` — last escalate devlog entry
        for phase N plus surrounding context (preceding 3 entries +
        relevant decisions)
      - `--section iteration --iter N` — captures from
        `logs/loop/iteration_NNN.txt` plus state snapshot at that point
      Each section is ~50-100 LOC. These replace the "20 screens of
      markdown dump" pattern with targeted, structured projections for
      downstream UIs (codexbot `/audit`, `/decisions`, `/escalation`,
      `/logs`, `/review`).

- [ ] **Runner emits Anthropic `cache_control` markers around stable
      prefix** — wraps WORKER CONTRACT and TOOL RULES regions with the
      Anthropic API's ephemeral cache_control blocks. Other providers
      (OpenAI, Gemini) auto-detect identical prefixes without explicit
      markers, so the existing prompt shape already benefits them; this
      item is opt-in for Anthropic specifically. ~10 LOC in
      `run_iteration.py`'s claude invocation path. Verify cache hits by
      observing `cache_read_tokens` in the API response on the second
      iteration of a phase.

### Verification protocol

Each item should:
1. Land as its own commit with a clear message
2. Run cleanly against `p:\shared\clankercourts\` (currently sitting at
   `phase=3 state=plan` post-Phase-2 close)
3. Not break diplomat or other consumers (where applicable)

### Archival

When all five items check off, replace this section with:
`Last completed wave: codexbot integration (2026-06-07 to YYYY-MM-DD)`
