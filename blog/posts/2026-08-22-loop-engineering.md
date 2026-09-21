---
title: "Loop engineering: making long-running agent work durable"
date: 2026-08-22
tags: [agent, loop, multi-agent, architecture]
summary: A chat loses context. future-loop turns "keep an eye on this for a week" into a durable goal — a todo graph, human gates, per-step evidence, and a verifiable definition of done that survives sessions, restarts, and parallel workers. This is how the control plane works, and how it carried the Matilda tiling run.
author: FutureOS
image: assets/covers/loop-engineering.png
---

An AI agent is good at one bounded turn. It is bad at "keep an eye on this for a
week" — because that request lives in chat history, and chat history is exactly
the thing that gets compacted, restarted, or lost.

future-loop is the FutureOS answer. It takes a long-running objective out of the
conversation and turns it into a **durable goal**: a graph of todos, human
gates, per-step evidence, and a verifiable definition of done, all persisted to
disk outside any session. The agent still executes one bounded turn at a time —
but now a deterministic kernel, not the conversation, decides what happens next.

This is the mechanism we used to run the Matilda tiling solve in [the previous
post](./solving-combinatorics-with-four-llms.html). This post is about how the
machinery itself works.

## The shape of a run

```
objective
   │
   ├─ todo graph (advancement / user-gate / monitor, --blocks dependencies)
   │
   ├─ human judgment needed? ──▶ ask one concrete question and wait (user gate)
   │
   ├─ safe to proceed? ──▶ kernel decision: run this todo / wait / replan / stop
   │
   ▼
agent executes one bounded turn → writes evidence → kernel decides the next turn
```

The mental model that matters: **the orchestrator is an AI agent; the loop is
its kanban and its control levers.** The loop is not a rule engine that drives
the agent. It's the durable board the agent works against, plus the levers
(steer, gates, verify, leases) that let a human — or the orchestrator — keep a
long run on the rails.

State lives at `<cwd>/.future/loop/`, event-sourced and replayable. Because the
state is in the project and not in any client, a goal started in the TUI can be
driven from a Feishu chat, picked up after a restart, or watched in the local
web dashboard — all against the same ledger.

## The pieces that carry the weight

**Goals and todos.** An objective decomposes into todos with priorities,
dependencies (`--blocks`), and a class — advancement (real work), user-gate (a
human decision), monitor, blocker, coordination. Dependencies form a DAG, so
independent work runs in parallel while dependents wait.

**Verify gates and evidence — "done" is checked, not claimed.** This is the
load-bearing idea. Two mechanisms:

- `todo complete --evidence "..."` — closing a todo must state what actually
  landed (paths, attempt ids, measurements). Empty evidence is **refused**;
  `--force` is the explicit, recorded override.
- `todo add --verify "cmd"` and `--acceptance "tok1,tok2"` — the kernel runs the
  command after a turn and only exit 0 lets that todo complete, or requires the
  evidence to contain every token. A machine-checkable gate for deterministic
  deliverables (compilation, a file existing, a verifier passing).

The effect: a model saying "I'm done" is not a completion. The gate runs, the
evidence is non-empty, and only then does the kernel accept the handoff.

**Leases.** Who holds a todo, and until when. The holder's pid is recorded, so a
dead process's leases are reclaimed automatically — killing a worker needs no
manual cleanup before relaunching it.

**Gates.** An open gate blocks its dependents; independent work keeps running.
A gate is a decision point, not a work item — you don't "complete" it, you
`gate resolve` it, and the decision is recorded. A scoped gate freezes only its
dependents; a global gate freezes everything.

**Delivery closure.** Completion lands in a pending `delivered` state; an
operator resolves it as `verified` / `failed` / `rework`. Unverified deliveries
auto-derive a follow-up after a few turns, so nothing silently slips.

**The should-run kernel.** Scheduling, refusal reasons, and spend are all
deterministic and auditable (`quota should-run/usage/spend/decisions`). Given
the state, the kernel decides whether to run a todo, wait, replan, or stop —
and it can tell you *why*.

**Observability and steering.** `worker tail` streams a worker's live turn log —
which tools it's calling, its token/cost burn — so the orchestrator can watch
before it interrupts. `supervisor steer` injects durable guidance into a worker;
routine guidance waits for a turn boundary, `--interrupt` aborts the in-flight
session for urgent correction.

**Fault recovery.** A worker that exits before a turn-boundary writeback
(transport loss, retry-budget exhaustion) reports an `infra_stopped` note. A
worker that dies outright is detected by an independent watchdog via its dead
lease — no paid turns needed. These are recoverable: the session is kept,
context replayed, the run resumed, without spending the run's error budget.

## The skill drives, the CLI is the mechanism

You rarely type these commands. You say `/future-loop keep an eye on X`, and the
agent loads the future-loop skill — a maintained driving manual — and
orchestrates `future loop` commands on your behalf: check for an existing goal
first (never duplicate), confirm the plan, decompose with dependencies and hard
checks, dispatch detached turns, steer drifting workers, open a user gate for
irreversible decisions, and close out with validated closure.

The split is deliberate. The **skill** owns "what to do when, how to decompose,
how to drive" — the orchestration layer. The **CLI** is the underlying mechanism
— the state kernel, the hard checks, the decisions. The skill can change how it
drives without touching the kernel's guarantees.

## A concrete run: Matilda

The [tiling case study](./solving-combinatorics-with-four-llms.html) is this
machinery under load. The user's prompt ended with a hard constraint — *do not
solve anything in this session* — so the orchestrator only decomposed,
scheduled, adjudicated, and kept the ledger. All the mathematics happened in
four worker sessions.

How the pieces showed up:

- **One worker per card.** Each task had a single owner, so four parallel models
  never claimed each other's work.
- **Verify gates made "done" verifiable.** 13 of 15 deliverables passed their
  gate; one was blocked by it (the work got filled in the next round); one
  errored into failure and recovered. The final reviewer re-ran the verifiers
  instead of trusting summaries.
- **Steering rescued a stuck worker.** glm's first round spun for ~40 minutes
  with nothing written to disk; a steer brought it back to deliver in 9 minutes.
- **The shared board was the only cross-worker channel.** Workers appended
  short conclusions to one file; each had its own artifact directory, so nobody
  overwrote anyone else.
- **Fault recovery absorbed real failures.** Three infrastructure disconnects
  (glm round one, gpt rounds two and three) all auto-recovered; the retry cost
  was about ¥4.75, not a lost run.
- **The goal closed its verification loop.** The final answer — 21, backed by a
  construction and two independent solver proofs ruling out 20 — only became
  "done" when the reviewer's re-runs passed.

The result is in that post. The point here is that none of the coordination,
verification, or recovery was improvised in a chat — it was the control plane
doing its job, durably, across 4.5 hours and 15 rounds.

## What it is and isn't

future-loop is **kanban plus control levers for an agent-orchestrator**, with
durable state and machine-checked completion. It is not a fully autonomous
planner, and it deliberately isn't a rule engine that replaces the agent's
judgment — the agent still decides *how* to do each turn; the loop decides
*what's next*, *whether it's safe*, and *whether "done" is real*.

It's also honest about its limits: a `--verify` gate checks deterministic
deliverables, not the correctness of research; tokens alone don't establish
facts; and a queued external request is not a verified result. The machinery is
there to make a long agent run auditable and recoverable — not to pretend the
hard parts of judgment away.

The full operational model, every command, and the architecture rationale are in
the [loop control plane
doc](https://github.com/futuregene/future-os/blob/main/docs/architecture/loop-control-plane.md)
in the FutureOS repo. The tiling run that exercised all of it is the [previous
post](./solving-combinatorics-with-four-llms.html).
