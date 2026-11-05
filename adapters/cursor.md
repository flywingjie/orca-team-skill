# 适配：Cursor

## 1. 接入方式

Cursor 支持仓库级规则。二选一：

- 在仓库根目录放 `AGENTS.md`（本目录版本），Cursor 会作为项目规则读取；
- 或把摘要放到 `.cursor/rules/orca-team.mdc`：

```markdown
---
description: 多 agent 协作规范（Orca Team Standard）
alwaysApply: true
---
按 AGENTS.md（Orca Team Standard）执行。
派单前读 references/05-model-assignment.md；
接管前读 references/06-takeover-recovery.md；
验收按 references/07-verification.md 的补丁三关。
```

## 2. 隔离与派单

- 隔离仍用 git worktree（或 Orca 工作区）。
- 支持通过 Orca 以 `cursor` 作为 agent 启动；模型属于原生透传时可直接 `--model`。
- 如果当前 Cursor 版本无法以无头/终端方式被编排：
  - 降级为“人工在 Cursor 内执行工单”；
  - 协调者用状态行/工单文件跟踪，不强行接入受监督通道。

## 3. 模型

- 优先使用工具自身的模型设置。
- 若通过 Orca 启动且属于原生透传，可用 `--model`；不要与 `--terminal` 同用。
- 无法确认支持时，按“自带配置”处理：先启动，再接管。

## 4. 纪律

- worker 只改「归属」段文件；零 commit；交补丁包。
- 不执行破坏性 git 命令。
- 失败显式写失败；回报三句。
- 业务歧义停下上报。

## 5. 降级清单

| 缺什么 | 降级做法 | 代价 |
|---|---|---|
| 无法被 Orca 以受监督方式启动 | 人工在 Cursor 内执行工单 | 无 `worker_done`，靠状态行 |
| 无法指定模型 | 用工具设置；在分配表标注“工具默认” | 模型互斥可能无法保证 |
| 无独立工作区 | 一分支一任务，串行 | 并行度降为 1 |

## 6. 回报

- 用 `templates/worker-report.md` 的格式，写入工单文件或状态行。
- 协调者按 `references/07-verification.md` 验收。
