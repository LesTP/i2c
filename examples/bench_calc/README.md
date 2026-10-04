# bench_calc - a fixed task for comparing models and backends

A small, exact task that drives the whole i2c loop (`plan -> tests -> execute
-> review -> close`) on one build phase, plus a **hidden reference suite** that
grades the result independently of the tests the worker writes for itself.
Use it to check whether a model or backend can complete a governed phase, and
to compare models on the same starting point.

Repo-only: `examples/` is not part of the installed i2c package.

## Layout

| Path | What |
|---|---|
| `spec/PROJECT.md`, `spec/ARCHITECTURE.md` | The task (Pattern B, one phase: `calc-ops`). Copied into each project. |
| `seed/calc.py`, `seed/tests/test_calc.py` | Starting code: `add` implemented and tested. Copied into each project. |
| `reference/test_reference.py` | Hidden grader, 65 tests. **Never copied into a project.** |
| `reference/calc_reference.py` | A reference implementation; i2c's tests check the grader against it. |
| `new_project.py` | Creates a fresh project at phase 1 PLAN. |
| `grade.py` | Runs the hidden grader against a project's `calc.py` and summarises its telemetry. |
| `sweep.py` | k fresh runs per model through the full loop, graded, in one table. |

## Use

```bash
# 1. A fresh project per run (on the host that runs the loop, e.g. pirozhok).
python examples/bench_calc/new_project.py ~/workspace/bench/calc-deepseek \
    --backend pidev --model deepseek/deepseek-v4.1-flash

# 2. Drive the loop until the phase gate (or the first non-zero exit).
cd ~/workspace/bench/calc-deepseek
i2c run          # repeat; or /setdir + /batch from the bot

# 3. Grade.
python ~/workspace/i2c/examples/bench_calc/grade.py ~/workspace/bench/calc-deepseek
```

`new_project.py` copies the spec and seed, scaffolds with `i2c init`
(`--run-split off`, Pattern B, the chosen backend's adapter), records the phase
and moves to `phase=1 state=plan` through `i2c state` (no hand edits), and makes
the initial git commit the runner needs. `--model` sets `[run].model`; per-step
routing can be added to the project's `i2c.toml` as `[run.models]`.
`[telemetry].test_cmd` is scoped to the worker's own acceptance suite.

`grade.py` reports the reference result (passed / failed / errors), whether the
phase reached `audit_boundary`, iterations, exit codes, contract violations,
tokens, cost (from the backend where it reports one) and time, plus one line
per iteration. `--json` prints the same as JSON. Full per-step detail (every
message and tool call) is in the project's `logs/loop/iteration_NNN.jsonl`.

## Sweep: k runs per model, one table

`sweep.py` does the three steps above for a list of models, `--runs` times
each, every run from a fresh project:

```bash
# On the run host, with OPENROUTER_API_KEY in the environment for pidev.
python ~/workspace/i2c/examples/bench_calc/sweep.py --out ~/workspace/bench/sweep-1 \
    --models deepseek/deepseek-v4.1-flash,qwen/qwen3-coder,z-ai/glm-4.7-flash \
    --runs 2 --parallel 3 --budget-usd 3
```

- Each run calls `i2c run` until a halt state (normally `audit_boundary`), the
  first non-zero exit, `--max-iterations` (default 12; a clean phase takes
  about 8), or the sweep's recorded spend reaching `--budget-usd`. The budget
  is checked between iterations, so one in-flight iteration can overshoot it.
- Each model is used for every action (`[run].model`). For per-action routing,
  edit a run's `i2c.toml` and drive it by hand.
- Output: `<out>/results.md` (a per-model table: runs reaching the gate, mean
  and full reference scores, iterations, cost, time, contract violations, the
  action each failed run stopped at; then one row per run) and
  `<out>/results.json` (everything, including each run's per-iteration rows).
- Each run's project stays in `<out>/<model>-r<N>/` (its event logs are in
  `logs/loop/`), and its `i2c run` console output in `<out>/<model>-r<N>.log`.
- `--report-only` rebuilds the tables from the run folders already in `--out`
  (after an interrupted sweep, or after driving a run further by hand).
- Runs that fail setup, or are skipped by the budget, appear in the per-run
  table with the reason.

## Provenance of the reference suite

Written by `deepseek/deepseek-v4.1-flash` as the phase 2 TESTS action of the
FU-70 pidev end-to-end run (pidev-spike commit `d25e366`, archived in
`p:\shared\pidev-samples\pidev-spike`). Adopted after review: it covers every
guarantee in the spec. It passes against `reference/calc_reference.py`, an
independent implementation (checked by i2c's tests), and fails against the
seed code. The spec's number grammar was made exact (ASCII digits; `0x10` and
`1_000` are malformed) so that every case the suite asserts is stated in the
spec.

## Caveats

- One run per model is only a screen: cheap models failed intermittently in
  FU-70. Repeat runs before drawing conclusions.
- The grader judges `calc.py` only; process quality (contract violations,
  retries, cost) comes from `grade.py`'s loop summary.
- For benchmark-grade comparisons, pin the i2c version for the whole series
  (see the fleet deployment notes on editable vs pinned installs).
