---
title: "Why the desktop talks to the agent over gRPC, not ACP"
date: 2026-09-25
tags: [protocol, grpc, acp, desktop, architecture]
description: "FutureOS runs one agent behind a terminal UI, a desktop app, IM bots, and a CLI — all talking to it over a small two-method gRPC service. ACP is the editor↔agent protocol that came out of Zed. Here's what each is actually good at, and why ACP was the wrong shape for us."
---

There are two questions hiding in this post. One is technical: what does the desktop actually say to the agent over the wire? The other is a judgment call: given that the Agent Client Protocol (ACP) exists and is designed for exactly the "UI talks to an agent" problem, why didn't we use it? The answers are related — the wire shape follows from the job, and the job turned out not to be the one ACP was built for.

## What the wire looks like

The whole contract is a 1272-line protobuf file (`packages/rpc/proto/future.proto`) that defines one service with two methods:

```proto
service FutureAgent {
  // Unary: send a command, get a response. Used for everything
  // non-streaming (prompt, get_state, new_session, abort, set_model...).
  rpc ExecuteCommand(RpcCommand) returns (RpcResponse);

  // Server-streaming: subscribe to agent events. The TUI and desktop
  // use this for real-time text / tool / thinking updates.
  rpc StreamEvents(StreamRequest) returns (stream StreamEvent);
}
```

That's the entire surface. Two methods. There's no negotiation handshake of the "initialize → capability exchange" kind; a client connects (over a local Unix socket, or a protected named pipe on Windows — never the network by default) and immediately starts issuing commands and subscribing to events.

The width lives inside the messages, not the method list. `RpcCommand` is a flat envelope keyed by a `type` string (`"prompt"`, `"get_state"`, `"new_session"`, `"abort"`, …) with optional fields for whatever that command needs — session id, model id, thinking level, attachments, a typed `sandbox_policy`, and so on. `StreamEvent` is the outbound side: a `type` string from a canonical vocabulary (`text_chunk`, `thinking_delta`, `tool_start`, `tool_end`, `approval_request`, `agent_end`, …) plus the run/event ordering envelope (run id, monotonic `idx`, `epoch`, `session_id`).

Two details matter more than the shape.

**Typed payloads alongside a JSON string.** Every `RpcResponse` and `StreamEvent` carries both a JSON `data` string *and* a typed protobuf `payload` (a `oneof kind` with ~22 variants for events). This dual-write is deliberate: the `data` string is byte-stable, which is what the journal and the NATS consumers downstream of it rely on, while the typed payload gives the desktop and TUI a compiler-checked contract to key off. Rust clients decode typed-first with a JSON fallback. It's the migration story you write when you can't break the consumers that already treat the event stream as the durable record.

**Ordering is explicit, not implied.** Run events carry `run_id`, a per-run monotonic `idx`, an `epoch` (bumped when a run restarts after an abort), and `session_id`. The desktop reconstructs its local view by replaying from a cursor, and if that cursor has aged out of the server's bounded replay ring, the server sends a `projection_snapshot` instead of a stream of deltas. So a reconnect isn't "the client re-renders whatever it gets" — it's "the client realigns to a cursor, or gets a fresh snapshot." A long-running desktop app that sleeps and wakes up mid-run needs exactly this.

## What ACP is actually for

ACP (the Agent Client Protocol, spearheaded by Zed) is a JSON-RPC protocol, typically over stdio, between an *editor* and an *agent*. Its model is:

- The **editor is the client and the orchestrator.** It spawns the agent as a subprocess, owns the lifecycle, and drives it.
- The agent is, from the protocol's perspective, a pluggable backend the editor fronts. You point Zed at an agent and it renders the conversation, applies edits, surfaces permissions.

That inversion is the whole point of ACP, and it's a good design for its niche: make any coding agent a drop-in backend for any editor that speaks the protocol. The editor holds the UX; the agent holds the loop.

## Where the two genuinely differ

**Process and authority.** In ACP the editor spawns and owns the agent. In FutureOS the agent is a long-lived, per-user daemon that owns all the state — the sessions on disk, the JSONL journals, the model config, the cost ledger. The desktop is a *client* of that daemon, one of several. The TUI, the Feishu/DingTalk bridge, and the CLI are peers of the desktop, all connected to the same agent at once. A session you started in the terminal can be watched from the desktop and steered from a chat message. That only works if the agent is the hub and the UIs are spokes — the opposite of ACP's topology.

**One protocol, many frontends.** ACP is point-to-point: one editor, one agent subprocess. We needed one agent, many concurrent clients, and they don't all look like editors. The IM bridge streams into a chat card, the desktop renders a thread tree, the TUI is a raw terminal. A gRPC service with a canonical event vocabulary serves all of them with the same two methods; ACP would have to be stretched into a multi-client, multi-transport shape it wasn't designed for.

**The transport is the security boundary.** Our default transport is local IPC — a Unix-domain socket, or a Windows named pipe with a current-user DACL. That's not incidental: the local socket *is* the auth model for a per-user agent, and TCP requires an explicit opt-in. gRPC over tonic gives us that for free on every platform, with streaming built in. ACP's stdio assumption is fine when the editor literally spawns the child, but it doesn't map onto a pre-existing daemon listening on a socket.

**Streaming is a first-class need, not a bolt-on.** Token deltas, thinking deltas, tool-arg fragments, approval prompts, compaction signals — the desktop wants all of it live, and it wants to reconnect mid-run and catch up. gRPC server-streaming plus our cursor/snapshot realignment handles that. JSON-RPC has no native streaming; you'd be layering it on top.

## What we'd give up — being honest

ACP has real advantages, and choosing not to use it wasn't free.

- **Interop.** Speak ACP and Zed (and any other ACP editor) can front your agent today. We built a bespoke protocol, so no third-party editor can drive FutureOS without writing a client. That's a real cost — it's the standard "own protocol vs. ecosystem" trade.
- **A ready-made schema.** ACP hands you session/update/permission types out of the box. We had to design our own event vocabulary and keep it stable (field numbers never reused, dual-write during migrations).
- **Simplicity for the single-editor case.** If all we ever wanted was "an editor drives an agent," ACP is less code and less to maintain.

But none of those outweighed the fit problem. Our product isn't "an editor fronting an agent" — it's "one agent, everywhere," with the desktop as just one window into it. ACP assumes the editor is in charge and there's exactly one of it; our architecture assumes the agent is in charge and there are many windows. Bending ACP into that shape would have meant re-implementing multi-client fan-out, daemon lifecycle, cursor-based reconnect, and a durable event journal on top of a protocol that doesn't have them — at which point you're maintaining ACP *and* the missing half.

## The one-line version

ACP is the right protocol when the editor owns the agent and there's one editor. We needed the agent to own the state and serve a terminal, a desktop, IM bots, and a CLI at the same time, over local IPC, with live streaming and cursor-based reconnect. That's a gRPC service, not an editor backend — so that's what we built.

The two-method surface (`ExecuteCommand` + `StreamEvents`) is small on purpose; everything interesting is in the event vocabulary and the ordering envelope. If you're curious how the durable-work layer sits on top of this same agent, that's the [loop engineering post](./loop-engineering.html); the wire protocol here is what the loop's workers and observers ultimately speak.
