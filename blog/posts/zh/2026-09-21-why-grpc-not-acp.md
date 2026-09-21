---
title: "为什么我们选了 gRPC，而不是 ACP"
date: 2026-09-21
tags: [protocol, grpc, acp, desktop, architecture]
description: "FutureOS 让一个 agent 同时支撑终端 UI、桌面应用、IM 机器人和 CLI——它们都通过一个小巧的两方法 gRPC 服务与之通信。ACP 是 Zed 推出的「编辑器↔agent」协议。真正拍板的是两点：一个 agent 要同时服务多个客户端，以及 gRPC 的流式成熟而 ACP 没有 pub/sub。"
image: assets/covers/why-grpc-not-acp.jpg
---

在把桌面端接到 agent 的过程中，有人提出了一个问题：既然 Agent Client Protocol（ACP）就是为「UI 与 agent 通信」这件事而生的，我们为什么还要手写一套 gRPC 协议？这个质疑很合理。答案并不是 ACP 不好——而是我们的两个硬性要求恰好落在 ACP 的能力边界之外：一个 agent 要同时服务多个客户端，而我们需要的流式能力，gRPC 原生具备、ACP 却几乎没有。

## 线路上到底长什么样

整份契约就是一个 1272 行的 protobuf 文件（[`future.proto`](https://github.com/futuregene/future-os/blob/main/packages/rpc/proto/future.proto)），定义了一个只有两个方法的服务：

```proto
service FutureAgent {
  // 一元调用：发一条命令，拿回一个响应。所有非流式操作
  // （prompt、get_state、new_session、abort、set_model……）。
  rpc ExecuteCommand(RpcCommand) returns (RpcResponse);

  // 服务端流式：订阅 agent 事件。TUI 和桌面端用它
  // 获取实时的文本 / 工具 / 思考更新。
  rpc StreamEvents(StreamRequest) returns (stream StreamEvent);
}
```

这就是全部的对外表面——两个方法。宽度藏在消息内部：`RpcCommand` 是一个以 `type` 字符串（`"prompt"`、`"get_state"`、`"abort"`……）为键的扁平信封；`StreamEvent` 是出站一侧——一个取自规范词表的 `type`（`text_chunk`、`thinking_delta`、`tool_start`、`tool_end`、`approval_request`、`agent_end`……），外加排序信封（`run_id`、单调递增的 `idx`、`epoch`、`session_id`）。

有两个设计细节承担了大部分重量。

**类型化 payload 与 JSON 字符串双写。** 每个响应和事件都同时携带一个 JSON `data` 字符串*和*一个类型化的 protobuf `payload`（一个约 22 种事件变体的 `oneof kind`）。这是刻意的：`data` 字节稳定，下游的 journal 和 NATS 消费方依赖它；而类型化 payload 给桌面端和 TUI 一份编译器可校验的契约。Rust 客户端按「类型优先、JSON 兜底」解码。当事件流本身已经是其他消费方在读取的持久记录时，这就是你要写的那种迁移路径。

**显式排序与基于游标的重连。** run 事件携带 `run_id`、每个 run 内的 `idx`、一个 `epoch`（run 在中止后重启时递增）和 `session_id`。客户端重连时对齐到某个游标；如果该游标已经老到超出服务端有界的重放环，服务端就改发一个 `projection_snapshot`，而不是增量流。一个睡眠后在 run 中途醒来的桌面应用，需要的正是这个，而不是「收到什么就渲染什么」。

## ACP 是为谁设计的

ACP（由 Zed 主导）是一个 JSON-RPC 协议，通常跑在 stdio 上，连接的是*编辑器*和 *agent*。它的模型是：编辑器把 agent 作为子进程拉起、拥有其生命周期并驱动它；agent 则是编辑器前置的一个可插拔后端。在它的细分领域里，这是个好设计——让任何编码 agent 都能成为任何会说这个协议的编辑器的即插即用后端。

## 原因一：一个 agent，多个客户端

这是决定性的差异，而且是结构性的。

ACP 是点对点的：一个编辑器，一个 agent 子进程。协议里根本没有「多个客户端挂在同一个 agent 上」的概念，也没有 pub/sub——编辑器是 agent 更新的唯一消费者。

FutureOS 是相反的拓扑。agent 是一个长驻的、按用户划分的守护进程，拥有全部状态——磁盘上的会话、JSONL journal、模型配置、成本账本。桌面端只是这个守护进程的一个*客户端*；TUI、飞书/钉钉桥、CLI 都是它的平级，同时连接。你在终端里开启的会话，可以从桌面端观察、从一条聊天消息里干预。

这需要扇出（fan-out）：agent 发出一次 `text_chunk`，每个挂着的客户端都要看到。gRPC 服务天然满足这一点——任意数量的客户端打开 `StreamEvents`，服务端把同一条事件流多路复用给它们。ACP 在这里什么都没有；你得自己在它之上搭一层带代理的 pub/sub，而那就等于重新发明了 gRPC 早已解决的那部分，再把它嫁接到一个假定单一消费者的协议上。

## 原因二：gRPC 的流式成熟，ACP 的不成熟

我们要的流式不是事后补的。token 增量、思考增量、工具参数片段、审批提示、压缩信号——桌面端全都要实时的，而且还要能在 run 中途断开再恢复订阅，且不丢位置。

gRPC 的服务端流式是经过实战检验的答案：HTTP/2 流控、背压、取消，以及（经由 tonic）在我们发布的每个平台上的原生客户端。ACP 是 JSON-RPC，没有原生的流式原语——更新只是通知，但没有标准化、可恢复、带背压的流可以依靠，也根本没有多订阅者语义。那些难的部分（重连、从游标恢复、扇出）我们得手写，而 gRPC 和我们的信封已经处理了。

## 代价，如实说

不选 ACP 不是免费的，值得说清我们放弃了什么。

- **互操作性。** 说 ACP，Zed（以及任何其他 ACP 编辑器）今天就能前置你的 agent。我们自建了协议，所以第三方编辑器不写客户端就驱动不了 FutureOS。这是标准的「自有协议 vs 生态」取舍，是真实存在的。
- **现成的 schema。** ACP 开箱即用地给你 session/update/permission 类型。我们得自己设计事件词表，并承诺保持其稳定（字段号绝不复用，迁移期双写）。
- **单一编辑器场景下的简单。** 如果我们想要的只是「一个编辑器驱动一个 agent」，ACP 的代码更少。

但适配性问题压倒一切。ACP 假定编辑器主导，而且恰好只有一个；我们假定 agent 主导，而且有多个窗口。要让 ACP 在本地 IPC 上服务多个并发客户端、还带可恢复的流式，就等于在一个没有这些能力的协议之上重新实现多客户端扇出和 pub/sub——而那正是 gRPC 已经做好的工作。

## 一句话版本

ACP 没有 pub/sub，且假定单一编辑器拥有单一 agent 子进程。我们需要一个 agent 同时服务终端、桌面、IM 机器人和 CLI，并且客户端能断开再恢复的实时流式。gRPC 在本地 IPC 上给我们成熟的服务端流式和天然的多客户端扇出——所以我们就建了它。两方法的表面是刻意保持小；真正有意思的都在事件词表和排序信封里。如果你好奇那层「持久化工作」是如何架在同一个 agent 之上的，去看 [loop 工程那篇](./loop-engineering.html)——这里的线路协议，正是它的 worker 和 observer 最终要说的语言。
