# Examples & walkthrough

This directory is a hands-on tour of i2c using a small, in-progress
project. Nothing here needs API keys or a backend CLI — every command
below is deterministic and runs locally.

- **`initial_state/`** — a fixture project caught mid-phase (Phase 2, the
  `event_store` module: step 1 done, steps 2–4 pending). It holds a real
  `.state/` directory.
- **`smoke_test.py`** — an end-to-end script that copies the fixture to a
  temp dir and exercises the whole `state.py` write surface.

All paths below are written from the repository root.

---

## 1. Confirm the toolchain works

```bash
python examples/smoke_test.py
```

This prints a transcript of every `state.py` call and the resulting state
transitions, then validates every `.state/` file against its schema. If it
ends with `=== SMOKE TEST PASSED ===`, your environment is good to go.
(Only dependency: `jsonschema`; Python 3.10+.)

---

## 2. Read a project's state (the deterministic tools)

The read-side tools walk up from the current directory to find `.state/`,
so `cd` into the project first. These never modify anything.

```bash
cd examples/initial_state

# What action would the loop dispatch next, and what state follows it?
python ../../tools/state_machine.py
```
```
ACTION: EXECUTE
NEXT: execute
```

```bash
# A human-readable orientation snapshot.
python ../../tools/assemble_context.py --section status
```
```
## Project Status
**Phase:** 2 (event_store) — Core storage (Build)
**State:** execute
**Budget:** steps_remaining=3
**Module:** event_store
## Current Phase Steps
| Step | Title              | Status   | Commit  |
|------|--------------------|----------|---------|
| 2.1  | Append-only writer | complete | 1234567 |
| 2.2  | Reader API         | pending  | —       |
| 2.3  | Concurrency tests  | pending  | —       |
| 2.4  | Schema migration   | pending  | —       |
...
```

```bash
# The phase-boundary audit view ("what happened in phase 2?").
python ../../tools/assemble_context.py --section phase-summary --phase 2
```

---

## 3. Make a state change (write surface)

State is only ever written through `state.py`, which validates against the
schema before saving. Work on a throwaway copy so the committed fixture
stays put:

```bash
# from the repo root
cp -r examples/initial_state /tmp/i2c-demo      # Windows: xcopy /E /I examples\initial_state %TEMP%\i2c-demo
cd /tmp/i2c-demo

# Mark step 2.2 done and log it.
python <repo>/tools/state.py complete steps.json --phase 2 --step 2 --commit abc1234
python <repo>/tools/state.py append devlog.jsonl '{"phase":2,"step":2,"action":"execute","outcome":"complete","summary":"Reader API implemented.","contracts":[],"commit":"abc1234","timestamp":"2026-01-01T00:00:00Z"}'

# Re-check: step 2.2 now shows complete, steps_remaining drops.
python <repo>/tools/assemble_context.py --section status
```

`smoke_test.py` automates exactly this kind of sequence (an execute step
plus a phase close) end-to-end — read it for the full command surface,
including `set`, `complete`, `append-record`, `update-record`, and
`append-gotcha`.

---

## 4. Start your own project

The fixture shows a project *mid-flight*. To begin one from scratch —
copy the framework files in, write `PROJECT.md` + `ARCHITECTURE.md`,
initialize `.state/`, and run the first phase — follow
**[Bootstrap a new i2c project](../README.md#bootstrap-a-new-i2c-project)**
in the top-level README.

Note: the read commands in §2 work against any `.state/` directory. The
*full* per-action prompt assembly
(`assemble_context.py --action plan --phase N --mode supervised`)
additionally needs the framework docs (`WORKER_SPEC.md`, `instructions/`,
the adapter) present in the project root — which the bootstrap step puts
there.
