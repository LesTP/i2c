# Examples & walkthrough

This directory is a hands-on tour of i2c using a small, in-progress
project. Nothing here needs API keys or a backend CLI — every command
below is deterministic and runs locally.

- **`initial_state/`** — a fixture project caught mid-phase (Phase 2, the
  `event_store` module: step 1 done, steps 2–4 pending). It holds a real
  `.state/` directory.
- **`smoke_test.py`** — an end-to-end script that copies the fixture to a
  temp dir and exercises the whole `state.py` write surface.

All paths below are written from the repository root. The `i2c` commands
assume the package is installed (`pip install -e .` from the repo root); if the
console isn't on your PATH, substitute `python -m i2c.cli …`.

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
i2c next-action
```
```
ACTION: EXECUTE
NEXT: execute
```

```bash
# A human-readable orientation snapshot (control-backed; --json for structured).
i2c status
```
```
Phase:        2
State:        execute
Module:       event_store
Regime:       build
Dependencies: (none)
Budget:       steps_remaining=3

Steps (phase 2):
  2.1  [complete]  Append-only writer  (1234567)
  2.2  [pending]  Reader API
  2.3  [pending]  Concurrency tests
  2.4  [pending]  Schema migration
...
```

```bash
# The phase-boundary audit view ("what happened in phase 2?").
i2c phase-summary --phase 2
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
i2c state complete steps.json --phase 2 --step 2 --commit abc1234
i2c state append devlog.jsonl '{"phase":2,"step":2,"action":"execute","outcome":"complete","summary":"Reader API implemented.","contracts":[],"commit":"abc1234","timestamp":"2026-01-01T00:00:00Z"}'

# Re-check: step 2.2 now shows complete, steps_remaining drops.
i2c status
```

`smoke_test.py` automates exactly this kind of sequence (an execute step
plus a phase close) end-to-end — read it for the full `i2c state` command
surface, including `set`, `complete`, `append-record`, `update-record`, and
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
(`i2c assemble --action plan --phase N --mode supervised`)
additionally needs the project's adapter (`CLAUDE.md` / `CODEX.md`) and
project docs (`PROJECT.md`, `ARCHITECTURE.md`, `ARCH_*.md`) in the project
root; `WORKER_SPEC.md` and `instructions/` resolve from the installed package
(or a project-local override). `i2c init` scaffolds these.
