# DESIGN: Investigations v1

**Status:** Proposed.
**Scope:** new optional `.state/investigations.json` collection; schema and
cross-record validation; state/scaffold/migration integration; structured read
surface; action-specific assembler context; tests and documentation.
**Reference implementation:** D119641713, `[state-tracker] Add investigations
collection and context facets`, landed as
`4cdf03bf5abe2f0db2b426673c70659c5fafd7cc`.
**Motivating consumer:** masorah Phase 8 clustering-coherence spike
(`DESIGN_clustering_coherence_spike.md`).

---

## 0. Summary

Add a structured investigations collection to i2c without adding a new lifecycle
state. An investigation records one bounded question, its assumptions and
method, evidence and artifacts, result and conclusion, limitations, confidence,
and follow-up work. It can form parent/child and supersession chains.

Investigations are **epistemic state**. The existing i2c lifecycle remains
operational state:

- `project.json.state` still determines PLAN / TESTS / EXECUTE / REVIEW / CLOSE.
- A phase with research as its deliverable still uses regime `explore`.
- `audit_escalation` remains the human gate between checks or judgments.
- Investigations tell workers what is being checked and what is currently known;
  they do not dispatch actions themselves.

The design adapts the landed state-tracker feature rather than copying it. The
reference tracker is app-centric and schema-discovered; i2c is phase-centric,
uses an explicit schema registry, owns a versioned `.state/` shape, assembles
worker prompts by action, and has a runner-owned commit model.

---

## 1. Problem

Today an i2c Explore phase spreads research state across four surfaces:

1. narrative intent in `ARCHITECTURE.md` or a `DESIGN_*.md` file;
2. an open record in `decisions.json` for the eventual verdict;
3. `probe` entries in `devlog.jsonl` for individual checks;
4. private or tracked artifacts containing methods and raw results.

This works for a single linear check. It becomes hard to resume when research
branches:

- several parameter arms are compared one at a time;
- a result spawns a narrower follow-up question;
- one finding supersedes another only after validation;
- a preliminary conclusion must carry into later work without injecting every
  historical result;
- a phase ends inconclusively but must preserve limitations and a concrete
  follow-up;
- raw evidence is too large or private to place in worker prompts.

The masorah clustering-coherence spike is a concrete example: baseline,
conservative-EOM, conservative-leaf, possible higher-dimension follow-up, and
Sonnet adjudication are one parent question with several checks and a human
selection gate. A flat decision plus prose devlog can represent the history but
cannot expose the current knowledge state compactly and deterministically.

---

## 2. Goals and Non-goals

### Goals

- Represent bounded research questions and nested checks as validated state.
- Keep lifecycle position and investigation status orthogonal.
- Preserve uncertainty explicitly through `status` and `confidence`.
- Carry forward only supported conclusions chosen for future context.
- Keep large methods, SQL, private text, logs, charts, and raw outputs in
  referenced artifacts rather than `.state/`.
- Reject invalid lifecycle transitions and broken references before writing.
- Make missing `investigations.json` read as an empty collection for backward
  compatibility.
- Give workers only the investigation context relevant to their current action.
- Provide complete operator/audit access through structured control and CLI
  surfaces.
- Reuse i2c's atomic state writes, schema versioning, assembler, control API,
  renderers, action contracts, and runner-owned commits.

### Non-goals (v1)

- No new `project.json.state` named `investigation` or `research`.
- No autonomous promotion of probe results into conclusions.
- No model-generated confidence without explicit worker/human judgment.
- No storage of raw evidence or copied artifact contents in `.state/`.
- No deletion command for investigations; history ends through conclusion,
  inconclusive status, or supersession.
- No generic runtime schema discovery; i2c keeps its explicit registry.
- No cross-project investigation index.
- No dashboard graph or dedicated UI in the first increment.
- No automatic conversion of existing decisions/devlog entries.

---

## 3. Evidence from D119641713

The landed state-tracker implementation establishes several useful semantics:

- one record holds one question/check/result/conclusion chain;
- `open`, `checking`, `concluded`, `inconclusive`, and `superseded` are distinct;
- confidence is independent of lifecycle status;
- only concluded records may carry forward;
- concluded records require nonblank result and conclusion;
- inconclusive records require a result, limitations, and follow-up;
- parent and supersession references are validated prospectively;
- an open successor does not hide a valid predecessor conclusion;
- a concluded successor hides the superseded conclusion;
- a later `superseded` status preserves that hide relationship;
- missing investigation state is treated as empty;
- compact carry-forward context and a complete audit table are separate views;
- mutations validate the prospective state before writing;
- tests verify failed writes leave both state and audit log unchanged.

These semantics port well. App/current-thread coupling, generic collection CRUD,
self-commit behavior, generated tracker snapshots, and gaps/gotchas integration
do not port directly.

---

## 4. Proposed State Contract

### 4.1 File

`.state/investigations.json` is an array of investigation records. It is optional
at read time and created for new or migrated projects.

### 4.2 Record

```json
{
  "id": "I-12",
  "phase": 8,
  "thread": "topic-timeline/clustering-coherence",
  "parent_id": "I-10",
  "question": "Does conservative leaf selection improve semantic coherence?",
  "assumptions": [
    "Existing multilingual vectors are valid and immutable",
    "UMAP-15 remains fixed for this arm"
  ],
  "method": "Run HDBSCAN min_cluster_size=5, min_samples=5, selection=leaf",
  "sources": [
    "DESIGN_clustering_coherence_spike.md",
    ".private/store/analysis/topic_timeline/baseline/manifest.json"
  ],
  "scope": "8698 primary user prompts, corpus snapshot 2026-10-13",
  "as_of": "2026-10-13T00:00:00Z",
  "result": "TBD",
  "conclusion": "TBD",
  "limitations": [],
  "status": "checking",
  "confidence": "unvalidated",
  "carry_forward": false,
  "follow_up": [],
  "artifacts": [
    ".private/store/analysis/topic_timeline/coherence_spike/leaf/"
  ],
  "supersedes": null
}
```

### 4.3 Fields

| Field | Required | Contract |
|---|---:|---|
| `id` | yes | String matching `^I-[0-9]+$`; unique project-wide. |
| `phase` | yes | Existing `phases.json.id`; scopes audit and prompt context. |
| `thread` | yes | Nonblank free-form research stream; stable across child checks. |
| `parent_id` | no | Existing investigation in the same phase/thread. |
| `question` | yes | Nonblank bounded question. |
| `assumptions` | no | Array of nonblank strings. |
| `method` | no | Compact method/check description; long methods live in artifacts. |
| `sources` | no | Array of nonblank references or URLs; never copied contents. |
| `scope` | no | Population, snapshot, environment, or boundary of the finding. |
| `as_of` | no | ISO date/date-time for the evidence cut, not mutation time. |
| `result` | conditional | Observed evidence summary. |
| `conclusion` | conditional | Judgment supported by the result. |
| `limitations` | conditional | Array of nonblank strings. |
| `status` | yes | `open`, `checking`, `concluded`, `inconclusive`, `superseded`. |
| `confidence` | yes | `unvalidated`, `preliminary`, `supported`, `confirmed`. |
| `carry_forward` | yes | Whether the current conclusion enters future compact context. |
| `follow_up` | conditional | Array of nonblank next checks/actions. |
| `artifacts` | no | Array of paths/URLs containing evidence; no automatic loading. |
| `supersedes` | no | Existing record in the same phase/thread that this record replaces. |

`phase` is the principal i2c adaptation. The reference tracker resolves `thread`
against `apps.id`; i2c has no apps collection. In i2c, phase supplies the
validated lifecycle association and `thread` remains a free-form branch key.

---

## 5. Lifecycle and Reference Invariants

### 5.1 Status rules

- `open`: question recorded; result/conclusion optional; `carry_forward=false`.
- `checking`: method may be in progress; result/conclusion optional;
  `carry_forward=false`.
- `concluded`: nonblank `result` and `conclusion` required. Confidence must not be
  `unvalidated`. `carry_forward` may be true or false.
- `inconclusive`: nonblank `result`, nonempty `limitations`, and nonempty
  `follow_up` required; `carry_forward=false`.
- `superseded`: historical record retained; `carry_forward=false`.

`result="TBD"` and `conclusion="TBD"` are permitted only while status is open or
checking. Whitespace-only values are invalid where nonblank content is required.

### 5.2 Reference rules

- `phase` must exist in `phases.json`.
- `parent_id` and `supersedes` must resolve to existing IDs.
- A record cannot reference itself.
- Parent and supersession targets must share the same `phase` and `thread`.
- Parent and supersession graphs must be acyclic.
- IDs are immutable after creation.

### 5.3 Carry-forward selection

A record is current carry-forward context when:

1. `status == "concluded"`;
2. `carry_forward == true`;
3. no record that names it in `supersedes` has reached `concluded` or
   `superseded`.

An open/checking successor does not hide a validated predecessor. Once the
successor concludes, the old conclusion disappears from compact context. If the
successor later becomes superseded, the chain remains hidden behind the newest
valid conclusion; an older conclusion must never reappear accidentally.

### 5.4 Prospective validation

Every append or update validates the entire candidate investigations array and
its references to `phases.json` before atomic write. A failed mutation writes
nothing.

Unlike the state-tracker reference, i2c begins from schema-valid versioned state.
V1 may reject mutation when pre-existing relevant investigation state is invalid
rather than implementing differential "new errors only" validation. If legacy
or external writers make that operationally painful, prospective-delta
validation can be added later.

---

## 6. State Integration and Migration

### 6.1 Schema registration

Add `investigations.json -> investigations.schema.json` to
`i2c.validate.SCHEMA_BY_FILENAME`. Use Draft 2020-12 conditionals for local
lifecycle rules. Keep cross-file references and graph checks in Python.

### 6.2 Read compatibility

`control.load_state` and `assemble_context` load investigations as an optional
array. Missing file returns `[]`. This lets a newer i2c read an unmigrated
project without changing it.

### 6.3 Write compatibility

Current `i2c state append-record` requires the target file to exist. Do not
silently create unknown/missing state during a worker action. Instead:

- `i2c init` creates empty `investigations.json` for new projects;
- schema migration v5 -> v6 creates it for existing projects and stamps v6;
- a write against a missing file fails with a remedy to run `i2c migrate`.

Bump `CURRENT_SCHEMA_VERSION` to 6. Older i2c installations then fail cleanly on
v6 projects instead of silently ignoring investigation context.

### 6.4 Atomic writes and commits

Reuse `state.atomic_write_json`. Do not port state-tracker self-commit behavior;
the i2c runner remains the sole commit owner. Investigation mutations occur
inside normal lifecycle actions and are covered by the existing `.state/`
action-contract allowance.

### 6.5 No removal

Do not add generic `rm` to i2c state. Investigation IDs may be referenced from
parents, supersession chains, decisions, devlog entries, and artifacts. Records
end through `concluded`, `inconclusive`, or `superseded`.

---

## 7. Control and CLI Surfaces

### 7.1 Structured view

Add `InvestigationView` with all record fields (lists default empty), then extend
`ProjectState` with `investigations`.

Add:

```python
investigations(
    root=None,
    *,
    phase: int | None = None,
    thread: str | None = None,
    status: str | None = None,
    carry_forward: bool | None = None,
) -> list[InvestigationView]
```

Add current/carry-forward helpers in control rather than making CLI/renderers
reimplement supersession semantics.

### 7.2 CLI

Add a read-only command:

```text
i2c investigations [--phase N] [--thread THREAD]
                   [--status STATUS] [--carry-forward]
                   [--json]
```

Text rendering shows ID, phase/thread, status/confidence, question, and compact
conclusion/result. `--json` returns the complete structured records.

Writes remain explicit through:

```text
i2c state append-record investigations.json --from-file <record.json>
i2c state update-record investigations.json --match id=I-N --from-file <updates.json>
```

A dedicated ergonomic `i2c investigation add/check/conclude` command may follow
only if repeated use demonstrates that schema-valid JSON payloads are too
awkward. It is not required for v1 correctness.

### 7.3 Existing views

- `i2c status`: add active current-phase investigation count and compact IDs.
- `i2c phase-summary`: include phase investigations and unresolved items.
- `i2c escalation`: include open/checking investigations for the phase.
- `i2c dashboard`: optional count/status summary only; defer graph rendering.

---

## 8. Assembler Context

Worker context must stay compact. Investigation artifacts and raw results are
never loaded automatically.

### 8.1 PLAN

Include:

- current-phase open/checking/inconclusive investigations, if any;
- confirmed carry-forward conclusions whose thread is active in the current
  phase;
- unresolved follow-up items needed to scope the phase.

PLAN may create the first open investigation for an Explore phase after the
phase record exists.

### 8.2 TESTS

Do not inject investigation history by default. Include only current-phase open
investigation questions if the acceptance suite is explicitly testing a research
harness contract.

### 8.3 EXECUTE

Include:

- current-phase records with status open/checking;
- their assumptions, method, scope, parent conclusion, and artifact references;
- current carry-forward conclusions in the same thread;
- no full historical result table.

This is the main cold-start/resume surface for slow one-arm-per-run spikes.

### 8.4 REVIEW

Include all investigations for the phase in compact audit form:

`ID | Thread | Parent | Status | Confidence | Question | Result | Conclusion`

Flag unresolved open/checking investigations and concluded records with
`confidence=unvalidated` as findings.

### 8.5 CLOSE

Include phase investigations, current carry-forward candidates, limitations, and
follow-up items. Explore CLOSE must not claim success while its designated exit
investigation remains open/checking. Inconclusive is a valid terminal research
outcome when its required limitations/follow-up are present.

### 8.6 Prompt-budget policy

- Compact whitespace and single-line table cells.
- Truncate long result/conclusion text in worker context, but preserve full state
  for CLI JSON output.
- Never inline assumptions/sources/artifacts from unrelated phases or threads.
- Never read artifact contents automatically.
- Prefer carry-forward conclusions over historical results.

---

## 9. Relationship to Decisions and Devlog

Investigations complement rather than replace existing records.

| Surface | Answers |
|---|---|
| Investigation | What are we checking, how, what was observed, how confident are we? |
| Decision | What choice did the project make? |
| Devlog | What did this lifecycle action do? |
| Artifact | Where is the detailed evidence or output? |

A concluded investigation may support a decision, but does not automatically
create or close one. A worker that runs an arm still appends the ordinary
`action=probe` or `action=execute` devlog entry. The investigation record is the
current structured knowledge; the devlog is the chronological action trail.

---

## 10. Action Contracts and Invariants

Investigation writes are `.state/` writes and fit the existing action-contract
path policy. Add postconditions where they create real safety:

- PLAN for an Explore phase may add an open investigation.
- EXECUTE may move an investigation open -> checking -> concluded/inconclusive,
  or add child checks.
- REVIEW should not create new evidence; it may correct status/confidence only
  when the review itself establishes the correction and logs it.
- CLOSE verifies no required exit investigation remains open/checking.

`i2c check` and runner baselines need investigation count/digest snapshots if
contracts assert append/update behavior. Do not require every EXECUTE iteration
to mutate investigations; Build phases may not use them at all.

Drift/recovery should classify an investigation file changed without the
corresponding action devlog as state drift, but deterministic reconciliation
must never invent result/conclusion text.

---

## 11. Implementation Increments

### Increment 1: State and validation

Files:

- `i2c/data/schemas/investigations.schema.json`
- `i2c/validate.py`
- `i2c/state.py`
- `i2c/scaffold.py`
- `i2c/migrate.py`
- `i2c/doctor.py`
- `examples/initial_state/.state/investigations.json`
- state/validation/scaffold/migration/doctor tests

Deliver:

- schema and registry;
- v5 -> v6 migration;
- new-project scaffold;
- optional read compatibility;
- lifecycle, reference, cycle, and failed-write atomicity tests.

### Increment 2: Structured reads and context

Files:

- `i2c/control.py`
- `i2c/render.py`
- `i2c/cli.py`
- `i2c/assemble_context.py`
- control/CLI/assembler tests and prompt goldens

Deliver:

- structured view and filters;
- carry-forward/supersession selection;
- CLI read command;
- PLAN/EXECUTE/REVIEW/CLOSE context recipes;
- phase summary and escalation integration.

### Increment 3: Contracts, dashboard, and docs

Files:

- `i2c/contracts.py` and/or `i2c/invariants.py` as required
- `i2c/dashboard.py`
- `README.md`, `WORKFLOW.md`, `CHANGELOG.md`
- action-contract, dashboard, render, and end-to-end tests

Deliver:

- Explore exit-investigation checks;
- dashboard status summary;
- operator runbook and examples;
- end-to-end slow-spike fixture modeled on masorah clustering coherence.

Keep each increment committed and green. `P:\shared\i2c` is editable and
fleet-wide; an uncommitted broken intermediate state affects every i2c project
and the pirozhok bot.

---

## 12. Acceptance Criteria

### Schema and mutation

- Valid open/checking/concluded/inconclusive/superseded records pass.
- Concluded without result/conclusion fails before write.
- Inconclusive without limitations/follow-up fails before write.
- Non-concluded carry-forward fails.
- Missing phase, parent, or supersedes reference fails.
- Self-reference, cross-thread parent/supersedes, and cycles fail.
- Duplicate IDs fail.
- Rejected append/update leaves file bytes unchanged.
- Missing file reads as empty; writes direct the operator to migrate.

### Carry-forward

- Open successor does not hide concluded predecessor.
- Concluded successor hides predecessor.
- Superseded intermediate records do not resurrect older conclusions.
- Phase/thread filters exclude unrelated findings.
- Inconclusive records never carry forward as conclusions.

### Assembler and surfaces

- EXECUTE gets active current-phase investigation context, not the full log.
- REVIEW/CLOSE get the phase audit view.
- Artifacts are referenced but never loaded.
- CLI text is compact; CLI JSON preserves all fields.
- Missing `investigations.json` does not alter existing prompt goldens except
  where the new empty section is intentionally omitted.
- Status, phase-summary, escalation, and dashboard counts agree.

### Migration and compatibility

- `i2c init` creates an empty valid file.
- v5 -> v6 migration creates the file and stamps v6.
- Migration dry-run reports without writing.
- Existing projects without the file remain readable before migration.
- Older/newer schema-version guards remain clear.
- All existing i2c tests and golden prompts pass.

### End to end

A fixture Explore phase can:

1. open parent investigation;
2. add two child checking arms;
3. conclude one and mark the other inconclusive;
4. carry the supported conclusion into the next EXECUTE prompt;
5. create a decision referencing the conclusion in prose;
6. pass REVIEW/CLOSE with no open required exit investigation.

---

## 13. Risks

### Prompt bloat

Investigation records can become verbose. Mitigation: action-specific filters,
compact conclusions, phase/thread scoping, and artifact references.

### Duplicate truth

Conclusions may be repeated in decisions or design docs. Mitigation: maintain the
semantic boundary in section 9 and never auto-promote.

### Framework blast radius

A schema/assembler bug affects every consumer because installs are editable.
Mitigation: dedicated i2c phase, version bump, incremental commits, fixtures, and
full golden coverage.

### Over-formalizing trivial questions

Not every uncertainty deserves an investigation. Use investigations for bounded
checks whose assumptions, evidence, confidence, or branching matter across
iterations. Ordinary implementation questions remain step notes or devlog text.

### False confidence

A model can write `confirmed` without sufficient evidence. Schema cannot judge
evidence quality. Mitigation: explicit method/sources/scope/limitations,
REVIEW scrutiny, and human gates for consequential carry-forward conclusions.

---

## 14. Decisions Proposed

**D-inv-1: Investigations are an orthogonal collection, not a lifecycle state.**
The state machine remains unchanged.

**D-inv-2: i2c investigations are phase-associated and thread-grouped.**
Add required `phase`; retain free-form `thread` rather than importing app IDs.

**D-inv-3: Missing files read empty; writes require scaffold/migration.**
This preserves read compatibility while keeping versioned state explicit.

**D-inv-4: Lifecycle rules live primarily in JSON Schema; references live in
Python validation.**
Use the strongest declarative validation available without forcing graph logic
into schema tricks.

**D-inv-5: Carry-forward context is selective and action-specific.**
Workers receive conclusions and active checks, never an automatic dump of full
research history or artifact contents.

**D-inv-6: No record deletion in v1.**
Use concluded, inconclusive, and superseded statuses to preserve provenance.

**D-inv-7: The masorah coherence spike is the end-to-end acceptance fixture.**
It provides real parent/child arms, private artifacts, preliminary evidence,
human gates, an inconclusive possibility, and a final configuration decision.

---

## 15. Open Questions

1. Should `as_of` accept date only, date-time only, or either?
2. Should concluded records require confidence at least `preliminary`, as
   proposed, or allow `unvalidated` conclusions that cannot carry forward?
3. Should `thread` be globally free-form, or should projects optionally register
   thread metadata later?
4. Should REVIEW be allowed to update investigation confidence/status, or only
   report findings for EXECUTE/human correction?
5. Should Explore CLOSE require every current-phase investigation to be terminal,
   or only the investigation designated as the phase exit criterion?
6. Should artifact paths be checked for existence when local, or remain opaque
   references so private/remote artifacts are valid?
7. Does v1 need a dedicated ergonomic write command, or are `i2c state`
   append/update payloads sufficient after real use?
8. Should dashboard support remain increment 3 or be deferred entirely until two
   projects adopt investigations?
