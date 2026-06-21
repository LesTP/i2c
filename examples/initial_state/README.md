# Initial state example

A canonical example of an in-progress i2c project. Phase 2 (the
`event_store` module) is mid-flight: step 1 is complete, steps 2–4 are
pending.

Use it as a reference when authoring a new project's `.state/`, or as a
fixture for exercising the toolchain.

## Files

| File | Contents |
|------|----------|
| `project.json` | Current phase, lifecycle state, gotchas, budget |
| `phases.json` | All phases (one record per phase) |
| `steps.json` | All steps across all phases |
| `devlog.jsonl` | Append-only history (one JSON object per line) |
| `decisions.json` | Project-level decisions |

## Walk a worker session against this fixture

The smoke-test script in `examples/smoke_test.py` copies this directory to
a temp location and runs the full `state.py` command surface against it,
simulating what a worker does across one execute step and one phase close.

```bash
python examples/smoke_test.py
```

It prints a transcript of every `state.py` call and the resulting state
transitions — handy for confirming the tooling end-to-end without spinning
up a real project. See [`examples/README.md`](../README.md) for a fuller
walkthrough.
