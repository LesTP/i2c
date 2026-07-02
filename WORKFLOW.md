# i2c Workflow Map

## Actor Topology

```mermaid
graph TB
    subgraph "Human Layer"
        H["👤 Human"]
    end

    subgraph "Dispatch Layer"
        CB["🤖 Chat-ops Bot<br/>(e.g. Telegram / Discord)<br/>deterministic driver"]
        CO["🎯 Orchestrator<br/>(Claude or Codex session)<br/>Reads: ORCH adapter"]
    end

    subgraph "Execution Layer"
        LR["🔄 Loop Runner<br/>(run_iteration.py)"]
        SM["⚙️ State Machine<br/>(state_machine.py)<br/>called by runner"]
        ASM["📦 Context Assembler<br/>(assemble_context.py)<br/>called by runner"]
        W["🔨 Worker<br/>(Claude or Codex session)<br/>receives assembled prompt"]
    end

    subgraph "State Layer"
        ST[".state/<br/>project.json<br/>steps.json<br/>phases.json<br/>devlog.jsonl<br/>decisions.json"]
        PD["Project Docs<br/>PROJECT.md<br/>ARCHITECTURE.md<br/>ARCH_*.md"]
        GOV["Governance Docs<br/>WORKER_SPEC.md + instructions/*.md<br/>(packaged; project may override)<br/>adapter file (project-root)"]
        LOG["logs/loop/<br/>summary.log<br/>iteration_NNN.*"]
    end

    H -- "chat commands<br/>/run /batch /audit /endphase" --> CB
    H -- "Direct session<br/>(supervised mode)" --> CO
    CB -- "Deterministic dispatch<br/>/run → subprocess" --> LR
    CB -- "Reads .state/ for<br/>/audit /portfolio /diagnose" --> ST
    CO -- "i2c run" --> LR
    CO -- "Reads logs for<br/>post-run analysis" --> LOG
    LR -- "1. determine action" --> SM
    SM -- "reads" --> ST
    LR -- "2. build prompt" --> ASM
    ASM -- "reads" --> ST
    ASM -- "reads" --> PD
    ASM -- "reads" --> GOV
    LR -- "3. invoke with<br/>assembled prompt" --> W
    W -- "writes outcomes<br/>via i2c state" --> ST
    LR -- "4. parse EXIT signal<br/>write summary.log" --> LOG
    CO -- "Reports results" --> H
    CB -- "Reports results" --> H

    style ST fill:#e1f5fe,stroke:#0288d1
    style GOV fill:#f3e5f5,stroke:#7b1fa2
    style PD fill:#e8f5e9,stroke:#388e3c
    style LOG fill:#fff3e0,stroke:#f57c00
    style ASM fill:#fce4ec,stroke:#c62828
```

## Two Dispatch Paths

There are **two independent ways** to dispatch worker iterations:

```mermaid
graph LR
    subgraph "Path A: Deterministic driver (chat-ops)"
        H1["Human"] -->|"/run 3"| CB1["Chat-ops bot"]
        CB1 -->|"subprocess"| LR1["run_iteration.py"]
        LR1 --> W1["Worker"]
    end

    subgraph "Path B: Orchestrator (interactive)"
        H2["Human"] -->|"run 3 loops"| CO2["Orchestrator"]
        CO2 -->|"i2c run"| LR2["run_iteration.py"]
        LR2 --> W2["Worker"]
    end
```

**Path A (deterministic driver):** A chat-ops bot (e.g. a Telegram or Discord bot) receives a command, directly shells out to `run_iteration.py`, reads results from files, and formats a report. No LLM judgment in the dispatch path — the bot is a plain program, not an AI session. It also handles the read/gate commands (`/audit`, `/portfolio`, `/diagnose`, `/endphase`) by reading `.state/` directly.

**Path B (Orchestrator):** AI-mediated. An orchestrator session (Claude or Codex) receives freeform instructions, decides what to do, shells out to `run_iteration.py`, then reads logs and reports back. The orchestrator makes judgment calls: "should I dispatch another iter?", "is this error worth escalating?", "what do I tell the human?"

**Key insight:** Both paths use the same runner, same worker, same state files. The difference is who sits between the human and the runner: a deterministic program or an AI session.

> **Current model:** [`DESIGN_packaging_v1.md`](DESIGN_packaging_v1.md) §7 is
> authoritative for the driver/surface architecture and generalizes these two
> paths. Both a chat-ops bot and an orchestrator are **drivers over the
> deterministic `i2c.control` command API**; they differ only in who pulls the
> levers — a deterministic Policy, a Human, or an LLM Agent. §7 factors the
> design into three independent axes (transport/surface, worker backend,
> orchestrator), so "Path A vs Path B" is really one axis — the orchestrator —
> varying while the others stay fixed. Read the diagram above as the two common
> configurations, not the full design.

---

## Invocation Flow

The runner assembles context *before* the worker starts, so the worker reads zero governance files.

```mermaid
sequenceDiagram
    participant R as Runner<br/>(run_iteration.py)
    participant SM as State Machine<br/>(state_machine.py)
    participant ASM as Assembler<br/>(assemble_context.py)
    participant W as Worker<br/>(Claude / Codex)
    participant ST as .state/

    R->>SM: 1. python -m i2c.state_machine
    SM->>ST: read project.json, steps.json
    SM-->>R: ACTION: EXECUTE, NEXT: review

    R->>ASM: 2. python -m i2c.assemble_context --action execute --phase 11
    Note over ASM: Reads: WORKER_SPEC, adapter,<br/>instructions/execute.md,<br/>project.json, ARCH_module.md,<br/>steps.json, devlog.jsonl
    ASM-->>R: Structured prompt (stdout)

    R->>W: 3. invoke with assembled prompt
    Note over W: All governance context<br/>is already in-context.<br/>Worker reads only source<br/>and test files.
    W->>ST: 4. i2c state set/complete/append
    W-->>R: 5. EXIT signal
    R->>R: 6. parse signal, write summary.log
```

### Single-step vs multi-step

**Single-step (STEP_BUDGET = 1, common case):** Runner handles everything before invocation. Worker does one action and exits.

**Multi-step (STEP_BUDGET > 1):** First step's context arrives pre-assembled. Between steps, the worker calls the assembler for updated context:

```bash
# Mid-step context request:
i2c assemble --action execute --phase 11

# Or request a single section:
i2c assemble --section architecture
i2c assemble --section module --module event_store
```

---

## Phase Lifecycle — Step by Step

```mermaid
stateDiagram-v2
    [*] --> plan: phase starts
    plan --> execute: steps defined
    execute --> execute: steps remaining > 1
    execute --> review: all steps complete
    review --> close: fixes applied
    close --> audit_boundary: phase complete
    audit_boundary --> [*]: awaiting human audit
```

| State | Assembled into prompt | Worker reads directly | Worker writes |
|-------|----------------------|----------------------|---------------|
| **plan** | instructions/plan.md, PROJECT.md, ARCHITECTURE.md, ARCH_module.md | — | steps.json, project.json, devlog.jsonl |
| **execute** | instructions/execute.md, ARCH_module.md, steps.json, recent devlog | source files, test files | steps.json, devlog.jsonl, project.json |
| **review** | instructions/review.md, ARCH_module.md, ARCHITECTURE.md, phase devlog, decisions | source files | devlog.jsonl, project.json |
| **close** | instructions/close.md, ARCH_module.md, phase devlog, decisions | source/test files | phases.json, project.json (gotchas; state→audit_boundary) |

**Always assembled** (every action): WORKER_SPEC (identity, loop, escalation, output contract, prohibitions), adapter (tool rules, project notes), project.json (state, gotchas), module list.

After CLOSE the worker leaves the project in `audit_boundary`; the human (or an autonomous wrapper) clears the gate — see *Detailed Action Map → Boundary clearing* below.

---

## Detailed Action Map

The per-action procedure — what each of PLAN / EXECUTE / REVIEW / CLOSE reads,
does, and writes, step by step — is the **canonical** content of
`instructions/{plan,execute,review,close}.md` (the same text the assembler puts
in the worker's prompt). See those files for the authoritative steps; the
README's "four worker actions" table is the one-line summary. The diagrams
above show how the runner wraps each action and what the assembler reads.

**Boundary clearing (after CLOSE):** the worker leaves the project in
`audit_boundary`; the human (or an autonomous wrapper) clears the gate with
`i2c state set project.json phase=N+1 state=plan` to advance, or
`i2c state set project.json state=done` to terminate. See the README
("Lifecycle states") and `DECISIONS.md` (D-state-*) for the full rules.

---

## Supervised Mode

In supervised mode, a human works directly with an AI assistant. The same tools serve both modes:

```mermaid
graph LR
    subgraph "Autonomous"
        R["Runner"] -->|"calls"| SM2["state_machine.py"]
        R -->|"calls"| ASM2["assemble_context.py"]
        R -->|"invokes with prompt"| W2["Worker"]
        W2 -->|"calls"| SP2["state.py"]
    end

    subgraph "Supervised"
        HA["Human + Assistant"] -->|"calls on demand"| ASM3["assemble_context.py<br/>--mode supervised"]
        HA -->|"calls"| SP3["state.py"]
        HA -->|"optionally calls"| SM3["state_machine.py"]
    end
```

Common supervised commands:

| Task | Command |
|------|---------|
| Cold-start orientation | `i2c status` |
| Plan a phase | `i2c assemble --action plan --mode supervised` |
| Mark a step done | `i2c state complete` + `i2c state append devlog.jsonl` |
| Review a phase | `i2c assemble --action review --mode supervised` |
| Close a phase | `i2c assemble --action close --mode supervised` |
