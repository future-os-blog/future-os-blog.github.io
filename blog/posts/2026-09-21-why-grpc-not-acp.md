---
title: "Why we chose gRPC, and not ACP"
date: 2026-09-21
tags: [protocol, grpc, acp, desktop, architecture]
description: "FutureOS runs one agent behind a terminal UI, a desktop app, IM bots, and a CLI — all talking to it over a small two-method gRPC service. ACP is the editor↔agent protocol from Zed. Two things decided it for us: one agent has to serve many clients at once, and gRPC's streaming is mature where ACP has no pub/sub."
image: assets/covers/why-grpc-not-acp.jpg
---

The question came up while we were wiring the desktop to the agent: why are we hand-rolling a gRPC protocol when the Agent Client Protocol (ACP) already exists for exactly the "UI talks to an agent" problem? It was a fair challenge, and the answer isn't that ACP is bad — it's that two requirements of ours land squarely outside what ACP does. One agent has to serve several clients at the same time, and the streaming we need is something gRPC does natively and ACP doesn't really have at all.

## What the wire actually looks like

The whole contract is a 1272-line protobuf file ([`future.proto`](https://github.com/futuregene/future-os/blob/main/packages/rpc/proto/future.proto)) defining one service with two methods:

```proto
service FutureAgent {
  // Unary: send a command, get a response. Everything non-streaming
  // (prompt, get_state, new_session, abort, set_model...).
  rpc ExecuteCommand(RpcCommand) returns (RpcResponse);

  // Server-streaming: subscribe to agent events. The TUI and desktop
  // use this for real-time text / tool / thinking updates.
  rpc StreamEvents(StreamRequest) returns (stream StreamEvent);
}
```

That's the entire surface — two methods. The width lives inside the messages: `RpcCommand` is a flat envelope keyed by a `type` string (`"prompt"`, `"get_state"`, `"abort"`, …), and `StreamEvent` is the outbound side — a `type` from a canonical vocabulary (`text_chunk`, `thinking_delta`, `tool_start`, `tool_end`, `approval_request`, `agent_end`, …) plus the ordering envelope (`run_id`, monotonic `idx`, `epoch`, `session_id`).

Two design details carry most of the weight.

**Typed payloads dual-written with a JSON string.** Every response and event carries both a JSON `data` string *and* a typed protobuf `payload` (a `oneof kind` with ~22 event variants). This is deliberate: `data` is byte-stable, which the journal and the NATS consumers downstream rely on, while the typed payload gives the desktop and TUI a compiler-checked contract. Rust clients decode typed-first with a JSON fallback. It's the migration story you write when the event stream is already a durable record that other consumers read.

**Explicit ordering and cursor-based reconnect.** Run events carry `run_id`, a per-run `idx`, an `epoch` (bumped when a run restarts after an abort), and `session_id`. A client reconnects by realigning to a cursor; if that cursor has aged out of the server's bounded replay ring, the server sends a `projection_snapshot` instead of a delta stream. A desktop app that sleeps and wakes mid-run needs exactly this, not "re-render whatever arrives."

## What ACP is for

ACP (spearheaded by Zed) is a JSON-RPC protocol, typically over stdio, between an *editor* and an *agent*. The model: the editor spawns the agent as a subprocess, owns its lifecycle, and drives it; the agent is a pluggable backend the editor fronts. It's a good design for its niche — make any coding agent a drop-in backend for any editor that speaks the protocol.

## Reason one: one agent, many clients

This is the decisive difference, and it's structural.

ACP is point-to-point. One editor, one agent subprocess. There's no notion in the protocol of several clients attached to the same agent, and no pub/sub — the editor is the single consumer of the agent's updates.

FutureOS is the opposite topology. The agent is a long-lived, per-user daemon that owns all the state — sessions on disk, the JSONL journals, model config, the cost ledger. The desktop is one *client* of that daemon; the TUI, the Feishu/DingTalk bridge, and the CLI are its peers, all connected at once. A session you started in the terminal can be watched from the desktop and steered from a chat message.

That needs fan-out: the agent emits a `text_chunk` once and every attached client sees it. A gRPC service gives us this for free — any number of clients open `StreamEvents` and the server multiplexes the same event stream to all of them. ACP has nothing here; you'd have to build a brokered pub/sub layer on top of it yourself, at which point you've reinvented the part gRPC already solved and bolted it onto a protocol that assumed a single consumer.

## Reason two: gRPC's streaming is mature, ACP's isn't

The streaming we need isn't an afterthought. Token deltas, thinking deltas, tool-arg fragments, approval prompts, compaction signals — the desktop wants all of it live, and it wants to drop and resume a subscription mid-run without losing its place.

gRPC server-streaming is the battle-tested answer: HTTP/2 flow control, backpressure, cancellation, and (over tonic) a native client on every platform we ship. ACP is JSON-RPC, which has no native streaming primitive — updates are notifications, but there's no standardized, resumable, backpressured stream to lean on, and no multi-subscriber semantics at all. We'd be hand-rolling the hard parts (reconnect, resume-from-cursor, fan-out) that gRPC and our envelope already handle.

## The cost, honestly

Choosing not to use ACP wasn't free, and it's worth saying what we gave up.

- **Interop.** Speak ACP and Zed (and any other ACP editor) can front your agent today. We built a bespoke protocol, so a third-party editor can't drive FutureOS without writing a client. That's the standard own-protocol-vs-ecosystem trade, and it's real.
- **A ready-made schema.** ACP hands you session/update/permission types out of the box. We designed our own event vocabulary and committed to keeping it stable (field numbers never reused, dual-write during migrations).
- **Simplicity for the single-editor case.** If all we ever wanted was "an editor drives an agent," ACP is less code.

But the fit problem dominates. ACP assumes the editor is in charge and there's exactly one of it. We assume the agent is in charge and there are many windows into it. Making ACP serve multiple concurrent clients over local IPC, with resumable streaming, would mean re-implementing multi-client fan-out and pub/sub on top of a protocol that doesn't have them — which is precisely the work gRPC already does.

## The short version

ACP has no pub/sub and assumes a single editor owning a single agent subprocess. We needed one agent serving a terminal, a desktop, IM bots, and a CLI at the same time, with live streaming that clients can drop and resume. gRPC gives us mature server-streaming and natural multi-client fan-out over local IPC — so that's what we built. The two-method surface stays small on purpose; everything interesting is in the event vocabulary and the ordering envelope. If you're curious how the durable-work layer sits on top of this same agent, that's the [loop engineering post](./loop-engineering.html) — the wire protocol here is what its workers and observers ultimately speak.
