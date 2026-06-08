# Initial state example

Canonical example of an in-progress i2c project. Phase 2 (`event_store` module)
is mid-flight: step 1 complete, step 2 in progress, steps 3-4 pending.

Use as a reference when authoring a new project's `.state/`, or as a fixture
for exercising the toolchain.

## Files

| File | Contents |
|------|----------|
| `project.json` | Current phase, lifecycle state (see DESIGN_state_lifecycle_v1.md), gotchas, budget |
| `phases.json` | All phases (one record per phase) |
| `steps.json` | All steps across all phases |
| `devlog.jsonl` | Append-only history (one JSON object per line) |
| `decisions.json` | Project-level decisions |

## Walk a worker session against this fixture

The smoke-test script in `examples/smoke_test.py` copies this directory to a
temp location and runs the full state.py command surface against it, simulating
what a worker would do across one step of execute + one phase close.

```powershell
python p:\shared\i2c\examples\smoke_test.py
```

It prints a transcript of every `state.py` call and the resulting state
transitions. Useful for confirming the tooling end-to-end without spinning up
a real project.
