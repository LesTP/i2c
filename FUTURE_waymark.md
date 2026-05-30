# Future Work — Waymark Refit for i2c

**Status:** Roadmap. Implementation deferred until i2c is built and used on a real project.
**Date:** 2026-05-30
**Predecessor:** [waymark v1](https://github.com/LesTP/waymark) — VS Code extension that parses e2e governance files (DEVPLAN.md, DEVLOG.md, ARCHITECTURE.md) into a tree view.
**Decision:** **Replace, not fork.** v1 stays as the legacy e2e tool; a new extension is built for i2c.

---

## 1. Why Refit

Waymark v1 was built to make e2e project state visible without opening markdown files. It works in principle but in practice was **buggy, fragile, and not very informative** — to the point of being unused.

The fragility is structural, not implementation quality. v1 has five separate parsers (`devplanParser`, `architectureParser`, `devlogParser`, `stepProgress`, `projectState`) doing what its own DEVPLAN gotchas describe:

> - Markdown parsers must skip content inside code fences
> - Section parsers must track heading level — subsection headings are children, not exits
> - Use exact match, not substring — "Phase N complete" is not fully complete
> - DEVLOG parser must scope `completedSteps` to the current phase section — step numbers repeat across phases and will cause false matches

These are exactly the symptoms the i2c design names in §1: *"The worker still parses markdown for state. ... Ambiguity bugs (phase-scoped grep, empty value vs 0) trace directly to this impedance mismatch."* Same diagnosis, different consumer.

**i2c eliminates the cause.** State lives in `.state/*.json` and `devlog.jsonl`. A viewer over that is a JSON read, not a parser. The five v1 parsers collapse to one structured-state reader. Parser-fragility-driven bugs disappear by construction.

Beyond fragility, v1 was thin on information. Once the parsers reliably extract structured data, the door opens to richer views (timeline, cost, contracts, progress) that were impractical to compute from markdown.

The refit is **not a code migration** of v1 — it's a fresh build that throws away the parser layer.

---

## 2. What Changes from v1

| Concern | v1 (e2e) | Refit (i2c) |
|---------|----------|-------------|
| Data source | `DEVPLAN.md` frontmatter + body, `DEVLOG.md` prose, `ARCHITECTURE.md` table | `.state/project.json`, `phases.json`, `steps.json`, `devlog.jsonl`, `decisions.json` |
| Parsers | 5 TypeScript files (~300 LOC, regex + heading walkers) | 1 thin reader (~50 LOC, `JSON.parse`) |
| Activation trigger | Presence of `DEVPLAN.md` or `ARCHITECTURE.md` | Presence of `.state/project.json` |
| Refresh trigger | File watcher on governance markdown, 500ms debounce | File watcher on `.state/*.json` + `devlog.jsonl` |
| Failure mode | Silent stale data when parser disagrees with markdown drift | JSON schema validation (i2c ships schemas in `schemas/`) — bad state surfaces as a clear error |
| Step status derivation | Cross-reference DEVPLAN checklists with DEVLOG entries (brittle) | Read `steps.json[].status` directly |
| Multi-module support | Scan workspace for module directories with their own DEVPLAN/DEVLOG | One `.state/` per project; module concept lives in `phases.json[].module` |
| Phase timeline | Parse `## Phase N:` headings | Read `phases.json` array |
| Recent activity | Grep DEVLOG headings, parse fields by heuristic | `tail` of `devlog.jsonl`, each line a complete record |

### The structural parallel worth naming

Three components in the e2e era do the same wrong thing — parse markdown — for different consumers. i2c addresses two of them in the original design; this doc closes the third:

| Component | e2e form | i2c form | Where specified |
|-----------|----------|----------|-----------------|
| Worker writes | `sed` on DEVPLAN | `tools/state.py` | DESIGN_governance_v3 §3 (write API) |
| Codexbot reads | `LogReader` (~297 LOC regex) | `StateReader` (~50 LOC) | DESIGN_governance_v3 §Appendix C, D13 |
| **Waymark displays** | **5 TS parsers** | **This doc** | **— (was missing)** |

The refit is the missing third leg.

---

## 3. Scope — Incremental, Read-Only First

**Plan: ship Scope A (read-only) first, then add Scope B (interactive) capabilities incrementally as specific frictions emerge during real use.** Each B capability is an additive command — no architectural rework needed. Both scopes are documented below so the target shape is visible from day one, but the only commitment up front is to A.

The reason for staging:
- A is a clean win on its own — fixes v1's actual problem (fragility) and adds richer information surfaces — without taking on write-path risk.
- B's value depends on usage patterns that don't exist yet (how much supervised-mode work, how often the IDE is the primary surface vs codexbot). Building speculative interactivity is exactly the trap v1's deferred Phase 4 was meant to avoid.
- The same backend (`state.py`, `assemble_context.py`, atomic writes) serves both, so promoting A → B is additive, not a rewrite.

### Scope A — Read-only viewer, just more reliable

A direct functional successor to v1 with the parsers replaced. Same VS Code surface (sidebar tree view, auto-refresh on save), same read-only constraint, same activation pattern.

**What you get vs v1:**
- Zero parser fragility — bad state surfaces as a schema error, not silent garbage
- Reliable step status, phase status, recent activity, contract changes, decisions
- Faster (no markdown walking)
- Richer information surfaces that were impractical before:
  - Real progress bars (count by status from `steps.json`)
  - Per-phase contract changes (`devlog.jsonl` entries with non-empty `contracts` array)
  - Open decisions and their priority (`decisions.json` filtered by `status: "open"`)
  - Gotchas pinned at top (`project.json.gotchas`)
  - Cost overlay if `cost_ledger.jsonl` is present

**What you don't get:** Any way to act on the state from the sidebar. To mark a step complete or clear a phase gate, you still drop to the terminal and run `state.py`.

**Sketch of the tree:**

```
PROJECT_NAME (Phase 11 · execute · 2/4 steps)
├─ Phase 11: Orchestrator (Build)             in progress
│  ├─ 11.1  Core pipeline wiring              ✓ abc1234
│  ├─ 11.2  Event loop                        ✓ def5678
│  ├─ 11.3  Slash commands                    ⬜ pending
│  └─ 11.4  End-to-end test                   ⬜ pending
├─ Recent activity (devlog tail)
│  ├─ 11.2 execute → complete · 2026-05-27 04:15
│  └─ 11.1 execute → complete · 2026-05-27 04:00
├─ Open decisions (1)
│  └─ D-12 [critical · open] Round structure
├─ Gotchas (2)
│  ├─ jq empty string vs null: use // "default"
│  └─ sed -i behaves differently on macOS
└─ Flags (0)
```

### Scope B — Interactive control panel

Same tree view as A, **plus** the sidebar can invoke i2c's write tools. The sidebar becomes the supervised-mode UI for i2c, not just a viewer.

**Capabilities beyond A:**
- **Mark step complete** — right-click a pending step → `state.py complete steps.json --phase N --step M --commit HEAD`
- **Append gotcha** — quick-input prompt → `state.py append-gotcha project.json "..."`
- **Clear phase gate** — when `blocked=true` after CLOSE → button to set `blocked=false`, append audit log, transition `state=plan`
- **Append devlog entry** — form with phase/step/outcome/summary fields → `state.py append devlog.jsonl '{...}'`
- **Assemble context for current action** — button to run `assemble_context.py --action $STATE --phase $PHASE --mode supervised` and open the result in a new editor tab (drop-in replacement for cold-start cognition)
- **Show assembled section** — `assemble_context.py --section architecture | module $NAME | devlog $PHASE` → editor tab
- **Run iteration** — pass-through to `run-iteration.sh` with backend pick, live output streamed into an output channel (overlaps with codexbot's `/run`, but useful when working in VS Code directly)
- **Dispatch decisions** — open `decisions.json` entry in a form view, edit `status` / `decision` / `revisit_if`
- **State diff visualization** — when `.state/` files change, show what the worker just wrote in a delta panel (e.g., "11.2 → complete, +1 devlog entry, +1 contract on ARCH_orchestrator.md")

**Architectural implication:** Scope B duplicates the user-facing surface of codexbot's slash commands inside VS Code. That's not necessarily bad — codexbot is for remote (Telegram, mobile, automation), waymark is for the IDE — but the doctrine should be clear: **both surfaces, one backend.** Both shell out to `state.py`, `assemble_context.py`, `run-iteration.sh`. No third state path.

**i2c design alignment check:** Design §5 is explicit that *"there is no DEVPLAN.md or DEVLOG.md in i2c projects. The structured state in `.state/` is the single source of truth."* and that human-readable views are *"assembled on demand."* Both A and B fit this — they are on-demand views with no parallel rendered state stored anywhere.

D20 (supervised mode) is also relevant: scope B makes VS Code a first-class supervised-mode driver. The assembler's `--mode supervised` flag exists for exactly this consumer.

### Which scope, when

This table is for context — the plan is A first regardless. Use it to gauge how strong the eventual pull toward B will be:

| Decide based on… | Suggests A stays sufficient | Suggests promoting to B |
|------------------|----------------------------|--------------------------|
| You mostly use codexbot for dispatch and just want IDE awareness | ✓ | |
| You work in VS Code directly more than via Telegram | | ✓ |
| Supervised mode (D20) gets significant use | | ✓ |
| First version should ship fast and stay minimal | ✓ | |
| Risk tolerance for sidebar writing to project state | ✓ (none) | (some — though `state.py` is atomic) |

---

## 4. What Stays the Same as v1

- **VS Code extension**, TypeScript, tree view provider pattern, activity-bar icon
- **Single workspace project at a time** (multi-project portfolio view remains deferred)
- **Read on file change**, no polling
- **No telemetry, no marketplace publication** — personal use
- **No webview** in v1 of the refit either; tree view is enough
- **The waymark name** — but a new repo (clean break, same as e2e → i2c). Old waymark stays parked on its e2e-tied design.

---

## 5. What Becomes Newly Practical

Things that v1 either couldn't do or could only do badly because of markdown parsing:

- **Progress bars** — `complete / total` is a `jq length` query, not a checkbox count with phase scoping
- **Cost telemetry overlay** — if `cost_ledger.jsonl` is adopted (via toolkit's `cost_accountant`), per-phase / per-step cost surfaces here for free
- **Timeline view** — `devlog.jsonl` timestamps yield phase durations, average step time, projected completion. Already designed into codexbot's `/timeline`; same data, different surface.
- **Cross-phase contract changes** — filter `devlog.jsonl` for non-empty `contracts` arrays → "which ARCH files changed in which phases"
- **Health signals** — step success rate, failure clustering, retry patterns. Each is a `jq` aggregation.
- **Schema-validated state** — i2c ships `schemas/*.schema.json` (D21). The extension can show a clear "state file invalid" indicator instead of half-rendering garbage.
- **Decisions surfaced as first-class** — v1 ignored DECISIONS.md entirely. Refit reads `decisions.json` and pins open/critical entries.

---

## 6. What Stays Hard

- **Activation in workspaces with no `.state/`** — v1 had the same problem (no governance files = inactive). Acceptable: extension just doesn't activate.
- **Multi-project view** — still deferred. The same `StateReader` pattern would extend to a portfolio view by scanning a parent directory for `.state/` folders. Build only if used.
- **Concurrency** — if the worker writes `.state/` while waymark is reading, partial reads are possible. Mitigation: i2c's `state.py` uses atomic writes (write-temp + `os.replace()`), so reads either see the old or new file, never a half-written file. Same guarantee codexbot's StateReader gets.
- **JSONL tail performance at huge scale** — devlog.jsonl is append-only. v1's 50-phase project = ~10KB (per i2c §4); not a real concern. If it ever is, add an index file or last-N cache.

---

## 7. Sequencing

**Prerequisite:** i2c must be built and have a real project on it. Specifically:
- `.state/` schema finalized (i2c D13 already gates codexbot work on this)
- `schemas/*.schema.json` published
- `state.py`, `assemble_context.py` stable
- At least one pilot project running

**Refit build order (when prerequisites met):**

1. **New repo** (`github.com/LesTP/waymark-i2c` or rename old to `waymark-e2e` and reclaim the name)
2. **i2c project layout for waymark itself** — eat the dogfood. Waymark builds itself using i2c.
3. **Phase 1 — Reader + tree view** (Scope A)
   - StateReader (port from codexbot's TypeScript equivalent if/when that exists)
   - Tree view rendering (modules → phases → steps → details)
   - Schema validation surfacing
   - File watcher on `.state/` + `devlog.jsonl`
4. **Phase 2 — Richer views** (still Scope A)
   - Decisions panel
   - Gotchas panel
   - Recent activity tail
   - Flags
5. **Phase 3 — Use it for a while.** Collect what's missing. Same instinct as v1's deferred Phase 4 — but this time deferred between Scope A and Scope B, not after release.
6. **Phase 4 — Interactivity (Scope B)** — only the commands that earned their place from real usage.

---

## 8. Decisions to Make During Implementation

Not yet decided. Captured here so they're not forgotten.

| # | Question | Likely default |
|---|----------|----------------|
| W1 | Reader: port codexbot's StateReader (Python) into TypeScript, or call `state.py` / `jq` as a subprocess? | TypeScript port — keeps the extension self-contained, no Python runtime requirement on user machine |
| W2 | Refresh strategy: file watcher per file, or one watcher on `.state/` directory? | Directory watcher with debounce, same as v1's 500ms |
| W3 | Schema validation: run on every read, or just on activation + watcher hit? | Every read — schemas are small, JSON parsing already happens |
| W4 | Multi-module rendering: nested under phase (`Phase 11 / orchestrator`) or as top-level (`Module: orchestrator / Phase 11`)? | Phase-first — the phase is the unit of activity |
| W5 | Cost ledger integration: built-in if `cost_ledger.jsonl` exists, or separate command? | Built-in, conditional on file presence |
| W6 | Color/icon scheme: VS Code theme tokens or hardcoded? | Theme tokens — respects light/dark/high-contrast |
| W7 | (Scope B only) Confirmation prompts for writes: always, or only for `blocked` / phase transition? | Only for phase transitions and gotcha appends (the irreversible-ish ones); step complete is high-frequency and routine |
| W8 | Old waymark repo disposition: archive, rename, or leave as-is? | Rename to `waymark-e2e` and archive, claim `waymark` for the new one |

---

## 9. Non-Goals

Explicitly excluded so they don't accumulate.

- **LLM integration in the extension.** The orchestrator and worker do the AI work. Waymark stays deterministic.
- **Editing i2c markdown docs from the sidebar** (PROJECT.md, ARCHITECTURE.md, ARCH_*.md). Edit them in the editor like any other file.
- **Replacing codexbot.** Codexbot remains the remote / automated dispatcher. Waymark is the IDE companion.
- **Marketplace publication.** Personal use, same as v1.
- **Backward compatibility with e2e.** v1 stays for e2e projects. The refit is i2c-only.
- **Webviews, custom editors, language servers.** Stay within the tree view + commands surface — same minimalism that worked for v1's structure (even though its parser layer didn't).

---

## 10. References

- i2c design — `p:\shared\i2c\DESIGN_governance_v3.md`
- i2c workflow — `p:\shared\i2c\WORKFLOW.md`
- Waymark v1 — https://github.com/LesTP/waymark
- Codexbot StateReader sketch — `DESIGN_governance_v3.md` §Appendix C (the read-side parallel)
