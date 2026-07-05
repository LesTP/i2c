# i2c Decisions — Index

A single index of every architecture decision recorded across i2c's design
docs, with current status and a pointer to the authoritative source. **This is
an index, not a second copy** — the rationale lives in the source doc; this file
exists so you can see the whole decision surface in one place and know which
decisions are still in force.

i2c is supervised work, not a self-governed i2c project, so decisions are
recorded in the design memos themselves (there is no `.state/decisions.json`
for the framework). Implementation commits reference the decision IDs.

**Status legend:**

- **Active** — in force; describes the system as built today.
- **Superseded** — replaced by a later decision (named in the row).
- **Historical** — a true record of a past choice that is no longer load-bearing
  (e.g. a one-time migration, a "why now" call).
- **Roadmap** — decided in principle; not yet implemented.

---

## Cross-cutting architecture decisions

### Governance / core model — `archive/DESIGN_governance_v3.md` §10 (D1–D21)

> Foundational design memo. Its **state model and several specifics are
> superseded** (see banner in that doc); the decisions below are individually
> annotated.

| ID | Decision (short) | Status |
|----|------------------|--------|
| D1 | Project name **i2c** | Active |
| D2 | Clean break — new projects, not an e2e migration | Active (copy-deployment specifics superseded by D-pkg-2) |
| D3 | No persistent rendered views (state is the source of truth) | Active |
| D4 | Write API is a CLI | Active (now the `i2c state` console, D-pkg-4) |
| D5 | Cold start = `project.json` + `PROJECT.md` | Active |
| D6 | Gotchas live in `project.json` | Active |
| D7 | No devlog compaction | Active |
| D8 | Four instruction files with conditional sections | Active |
| D9 | State machine in bash + jq | **Superseded** — rewritten as `state_machine.py` (Phase 3.A) |
| D10 | WORKER_SPEC and adapter kept separate | Active |
| D11 | No standalone GOVERNANCE.md | Active |
| D12 | Standalone repo at `p:\shared\i2c` | Active |
| D13 | Codexbot changes deferred (StateReader replaces LogReader) | **Superseded** — realized as `i2c.control` + packaging §7 |
| D14 | Full deterministic context assembly (worker reads zero governance files) | Active |
| D15 | Structured `═══`-delimited sections in the assembled prompt | Active |
| D16 | Assembler doubles as mid-step context provider | Active |
| D17 | Adapter file survives but shrinks | Active |
| D18 | State machine runs in the runner, not the worker | Active |
| D19 | Governance stays markdown; assembler does conditional filtering | Active |
| D20 | Supervised mode = same tools, different caller | Active |
| D21 | Toolkit validation copied in (not a dependency) | Active (now `i2c/validate.py`) |

### State lifecycle — `archive/DESIGN_state_lifecycle_v1.md` §9 (D-state-1..7)

> Shipped 2026-06-08; the 7-state model is **current**. The memo body is largely
> a historical implementation record, but these decisions are in force.

| ID | Decision (short) | Status |
|----|------------------|--------|
| D-state-1 | Drop `blocked`; expand `state` to 7 values | Active |
| D-state-2 | Split `audit_boundary` vs `audit_escalation` | Active |
| D-state-3 | Conservative closure: CLOSE always sets `audit_boundary`, never `done` | Active |
| D-state-4 | `audit_` prefix groups the two pause states | Active |
| D-state-5 | Assembler tolerates a missing phase record only under `--action plan` | Active |
| D-state-6 | No backwards-compat shim; one-time CC migration | Historical |
| D-state-7 | `done` is recoverable only by deliberate `set state=plan` | Active |

### Packaging / distribution / control surface — `DESIGN_packaging_v1.md` §12 (D-pkg-1..15)

> The **live** design track (Phase 1–2 shipped; Phase 3 in progress).

| ID | Decision (short) | Status |
|----|------------------|--------|
| D-pkg-1 | Public open-source distribution (PyPI + public Git) | Active (Roadmap to publish) |
| D-pkg-2 | Installed-package dependency model; maximally self-contained | Active |
| D-pkg-3 | Abstract the backend; claude + codex first; respect provider caching | Active (scope/timing open) |
| D-pkg-4 | Worker tool surface is the `i2c` console, not `python tools/<x>.py` | Active |
| D-pkg-5 | Prompt-caching is a backend capability flag over the `--emit` split | Active |
| D-pkg-6 | License is MIT | Active |
| D-pkg-7 | `i2c.control` returns structured data; no LLM logic in any surface | Active |
| D-pkg-8 | Orchestrator is an optional pluggable driver over `i2c.control` | Active (Roadmap impls) |
| D-pkg-9 | Three independent axes: transport, worker backend, orchestrator | Active |
| D-pkg-10 | Transport adapters are thin optional extras over `i2c.control` | Roadmap |
| D-pkg-11 | Instructions ship as package-data (per-file override); adapters scaffolded on `init` | Active |
| D-pkg-12 | Defer the full backend protocol until the 3rd backend (Gemini) | Active (Roadmap) |
| D-pkg-13 | Roadmap backends: Gemini (agentic CLI) + OpenRouter (raw-API + harness) | Roadmap |
| D-pkg-14 | `control` is the single projection/command layer; assembler operator `--section` modes deprecated | Active (Phase 3a shipped 2026-06-25 — operator sections removed) |
| D-pkg-15 | Worker-prompt assembly stays isolated; de-dup must not alter prompt bytes | Active |

### Recovery — `archive/DESIGN_recovery_v1.md` (D-recovery-1..6)

> Reconcile-first recovery v1 (shipped 2026-06-29).

| ID | Decision (short) | Status |
|----|------------------|--------|
| D-recovery-1 | Recovery owns **workflow-state drift only**; code/spec/env are orthogonal (REVIEW regime) | Active |
| D-recovery-2 | `diagnose` is the single deterministic-first entry point: drift audit runs first, then classify | Active |
| D-recovery-3 | Drift audit is deterministic, reusing `invariants._check_close` + `state_machine` + `control.load_state`; extends detect-and-halt into detect-and-reconcile | Active |
| D-recovery-4 | `reconcile` is human-gated (dry-run default) and mutates only via the `state.py` path; never marks a code-blocked step complete | Active |
| D-recovery-5 | Recovery actions dispatch out-of-band (`i2c run --action … --target N`), bypassing the state machine and emitting no Next State | Active |
| D-recovery-6 | Full `fix` code-repair agent deferred | Roadmap (see `FUTURE_recovery.md`) |

### Runner / iteration model - `ARCH_assembler.md` (section 10) (D-run-1..2)

> Single-iteration-per-invocation (decided 2026-07-05); resolves the STATUS
> section-2 multi-iteration-loop open item. Rationale lives in
> `ARCH_assembler.md` section 10.

| ID | Decision (short) | Status |
|----|------------------|--------|
| D-run-1 | One worker invocation performs exactly one ACTION; the runner re-invokes for the next. No worker-side multi-step loop and no `--step-budget` flag (both removed). | Active |
| D-run-2 | Cross-action multi-step is rejected: one invocation is one backend, so it is incompatible with per-action routing (`[run.backends]`). The only coherent unit - a single invocation running several EXECUTE steps for continuous context - is deferred pending model-benchmark evidence, since it forfeits the per-step commit and per-(action, step) telemetry granularity FU-40 established. | Active |

---

## Component-local decisions (kept with their contract)

These decision families are local to a single component and live *with* that
component's contract — indexed here, not copied, so the source stays the single
authority (the same anti-duplication principle behind D-pkg-14).

| Family | Lives in | Covers |
|--------|----------|--------|
| `D-impl-*` | `ARCH_assembler.md` | Assembler implementation choices (e.g. D-impl-3 NEXT-state table, D-impl-4 single-file module organization) |
| `D-arch-*` | `ARCH_assembler.md` | Assembler contract decisions (e.g. D-arch-12, the `--emit` split per FU-35) |
| `D-prose-*` | `instructions/*.md`, `templates/README.md` | Worker-facing prose / template choices (e.g. D-prose-4 per-project slash commands, D-prose-8 retained production-incident anecdotes) |

---

## How to use this file

- **Adding a decision:** record it in the appropriate design doc (that stays the
  source of truth), then add a one-line row here with its status and pointer.
- **Superseding a decision:** flip its status to **Superseded** and name the
  decision that replaces it. Don't delete the row — the trail is the value.
- Cross-cutting architecture decisions get a central row here; component-local
  decisions stay with their component and are indexed by family above.
