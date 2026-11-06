---
name: orca-team
description: >-
  Standard for multi-role, multi-agent software delivery. Use when you need to
  split work into independent tickets, dispatch several AI agents (Claude Code,
  pi, Codex, Cursor, OpenCode) in isolated worktrees, assign specific models,
  supervise or take over a worker/run, run decision gates, or accept worker
  patches with evidence. Triggers: "多 agent", "多智能体", "Orca 派单",
  "worker 接管", "补丁验收", "并行开发", "task DAG", "指定模型跑 agent".
---

# Orca Team Standard（OTS 1.3）

多角色、多 Agent 并行协作的执行口径。**规范正文在 `SPEC.md`**；本文件是入口与快速操作卡。

## 何时使用

- 一项工作要拆给 2 个及以上 agent，或 agent + 人并行推进同一仓库。
- 需要指定某个 agent 用哪个模型、在哪个工作区、由谁监督。
- 需要接管失联 worker / Run，或判断“它到底挂了没有”。
- 需要验收 worker 的补丁包，决定能否合入。

单 agent、单会话的小改动不必套全套；但**工单五段式**与**实跑证据**不裁剪。

## 四条铁律

1. **隔离靠 worktree**：一个任务一个工作区 + 一个 `feature/*` 分支；不共享工作目录并行。
2. **派单靠工单**：工单必须五段齐全（目标 / 改动 / 约束 / 归属 / 验收），且不含工具命令。
3. **验收靠证据**：补丁包 = `manifest.json` + `change.patch` + `evidence/`；三关（范围 / 正确 / 集成）全过才 commit。
4. **接管靠证据链**：沉默、超时、不可验证都不等于失败；原协调者活跃时禁止接管。

## 角色一句话

| 角色 | 一句话 |
|---|---|
| 拍板人 | 业务政策、高风险判定、合并与发布 |
| 协调者 | 拆单、派单、监控、验收、集成、逐文件提交（同一时刻只一只） |
| 顾问 | 只出意见，不改代码 |
| 设计 | 规格、契约定义、待拍板清单 |
| 实现者 | 所有权文件内改码，零 commit，交补丁包 |
| 验收者 | 条款级核验，不代写实现 |

## 派单前检查（缺一不可）

- [ ] 工单五段齐全；「归属」列出独占文件；「验收」有命令 + 期望结果。
- [ ] 依赖已结算；并行度 ≤ 8；链深 ≤ 4。
- [ ] 文件所有权互不相交；迁移号段已分配；契约产物有唯一 owner。
- [ ] 产出「任务 × 模型」分配表；同一时刻不重复使用同一模型家族。
- [ ] 模型可达预检已跑；鉴权通过 ≠ 配额充足（派单后 1 分钟看是否限流）。
- [ ] 基线是 `origin/main`（或工单指定的集成分支），不是本地 `main`。

## 模型指定（速查）

| agent 类型 | 做法 |
|---|---|
| Claude / Codex / Cursor / Antigravity / Grok / Muse | 派单命令直接带 `--model`（reasoning effort 需同时带 `--model`） |
| OMP | 只带 `--model`，不带 effort |
| OpenCode | 必须挂既有工作区，且 CLI 已验证、模型可用 |
| pi / zcode / 其他自带配置 | 先用 `pi --model ...` 启动终端，再接管为受监督 worker，最后补发工单正文 |

完整决策树：[`references/05-model-assignment.md`](references/05-model-assignment.md)。

## 接管（速查）

| 类型 | 允许条件 | 禁止 |
|---|---|---|
| 终端接管 | 未受监督、已空闲、无其他调度在管 | 属于另一个活跃调度 |
| Run 接管 | 原协调者失联且无健康 worker，已记录证据 | 原协调者仍活跃；等太久就接管 |
| 续派 | 已结算 worker，用消息通道唤醒 | 绕过调度直接向终端注入指令 |

完整决策树与命令：[`references/06-takeover-recovery.md`](references/06-takeover-recovery.md)。

## 执行顺序

0. **先判档位**（`references/02-workflow.md` §1.1）：L0 轻 / L1 标 / L2 重。
   档位决定必做产物，**工单五段式与实跑证据任何档都不裁剪**。
1. 读 `SPEC.md` §3–§10（角色、三层模型、七阶段、工单、并行、模型、接管、验收）。
2. 按工具读一个 `adapters/<tool>.md`。
3. 复制 `templates/task-ticket.md` 写工单。
4. 跑 `scripts/preflight.py` 出分配表；`scripts/plan.py` 出波次；
   `scripts/check_consistency.py` 校验工单↔编排配置一致。
5. 派单 → 监控 → 收补丁 → 三关（范围 `accept_patch.py`、正确 `check_evidence.py`、集成 `check_integration.py`）→ 逐文件 commit。
6. 更新状态行与进度文件。

> **Windows / PowerShell**：跑任何 Orca 命令前先读 `references/08-orca-cli.md` §5 的实跑坑位
> （管道按 GBK 解码、`check --wait` 输出 JSON 多对象序列、命名管道 EPERM 等）。

## 参考索引

| 主题 | 文件 |
|---|---|
| 角色与边界 | `references/01-roles.md` |
| 七阶段与门 | `references/02-workflow.md` |
| 工单五段式 | `references/03-ticket.md` |
| 并行五条硬规则 | `references/04-parallelism.md` |
| 模型分配 | `references/05-model-assignment.md` |
| 接管与恢复 | `references/06-takeover-recovery.md` |
| 验收与回报 | `references/07-verification.md` |
| Orca CLI 速查 | `references/08-orca-cli.md` |

## 禁止

- 替拍板人决定业务政策、资金与数据完整性、合并与发布。
- 让 worker commit / push / 合并。
- 用 `git add -A` / `.` / `commit -a` 提交。
- 把“终端空闲”“等太久”“没回复”当作完成或失败证据。
- 为了过检查而削弱测试、关闭 lint、修改断言。
- 在规范正文或工单里写工具专属命令。
