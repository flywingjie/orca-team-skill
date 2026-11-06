# AGENTS.md — 多 Agent 协作总入口（OTS 1.3）

本文件供 Codex、pi、Cursor、Claude Code 及其他读取 `AGENTS.md` 的 agent 使用。
规范正文是 [`SPEC.md`](SPEC.md)；本文件是入口与执行摘要。

> 如果你的工具支持 skill（Claude Code），优先加载 [`SKILL.md`](SKILL.md)。
> 如果你是 worker，收到“角色卡 + 工单 + 纪律”后，先读本文件与 `SPEC.md` §10，再开工。

## 0. 一句话

**隔离靠 worktree，派单靠工单，验收靠证据，收敛靠人拍板；模型按能力矩阵指定，接管按证据链放行。**

## 1. 你的位置

| 如果你是被派单的 worker | 如果你是协调者 |
|---|---|
| 只改工单「归属」段点名的文件 | 拆单、派单、监控、验收、集成、逐文件提交 |
| 零 commit；交付补丁包 | 收补丁包，做三关验收后 commit |
| 业务歧义停下上报 | 不替拍板人定业务政策 |
| 收尾回报三句 | 维护任务 ↔ 分支 ↔ 工作区 ↔ 回报通道 |

## 2. 开工必读

0. **先判档位**：L0 轻 / L1 标 / L2 重（`references/02-workflow.md` §1.1）。
   档位决定必做产物；**工单五段式与实跑证据任何档都不裁剪**。
1. `SPEC.md` §3 角色、§4 三层模型、§6 工单、§7 并行、§10 证据。
2. 你的工单全文（五段式）。
3. 项目自身的规则文件（若有 `AGENTS.md` / `CLAUDE.md` / `docs/`），项目规则优先。

## 3. 工单五段式（缺一段即不合格）

```markdown
# 工单 <单号>：<标题>
## 目标
<精确到目录/文件；列出不碰的相邻文件>
## 改动
<逐条可核对的结果>
## 约束
<口径来源 / 禁改清单 / 契约归属 / 迁移号段 / 越界与歧义处置>
## 归属
<本单独占文件 / 相邻单与交接点 / 分支与基线>
## 验收
<命令 + 期望结果 + 负路径覆盖>
```

模板：`templates/task-ticket.md`。

## 4. worker 纪律（硬性）

- 只改所有权文件；越界即停上报。
- **不 commit / push / 合并**；不执行 `checkout` / `restore` / `clean` / `reset` / `stash`。
- 交付补丁包：`manifest.json` + `change.patch` + `evidence/`；新增文件先登记 intent-to-add。
- 收尾回报三句：做了什么 / 凭什么算做完 / 还有什么没做。
- **失败必须显式写为失败**，不得只在散文里暗示。
- 不削弱测试、不关闭 lint/安全检查、不修改断言来让检查通过。

## 5. 协调者纪律（硬性）

- 同一时刻只允许一只协调者；接管失联 Run 必须满足 `SPEC.md` §9 的证据条件。
- 派单前先判档位（L0/L1/L2）；**难度不决定拆几张单**，只决定角色、模型、验收强度、并行度四个旋钮（`SPEC.md` `S-6`）。
- **角色 ≠ agent 实例**：验收者必须是独立实例；协调者亲自实现的单不得自己验收（`SPEC.md` `R-11`）。
- 派单前出「任务 × 模型」分配表；同一时刻不重复使用同一模型家族（同家族并行需附配额依据，`M-7`）。
- 鉴权预检 ≠ 配额充足；派单后 1 分钟内确认 worker 真正开工。
- 补丁三关（范围 / 正确 / 集成）全过才逐文件 commit；禁止 `git add -A` / `.` / `commit -a`。
- 沉默、超时、不可验证都不等于失败，也不构成接管依据。

## 6. 工具适配

| 工具 | 接入说明 |
|---|---|
| Claude Code | `adapters/claude-code.md`（skill + `CLAUDE.md` 指针） |
| pi | `adapters/pi.md`（自定义模型 argv + 终端接管） |
| Codex | `adapters/codex.md`（`AGENTS.md` + `--model`） |
| Cursor | `adapters/cursor.md`（rules + 串行降级） |
| OpenCode | `adapters/opencode.md`（既有工作区限制） |
| 纯人工 | `adapters/manual.md` |

**规范正文（`SPEC.md`）不写工具命令**；命令在各 `references/` 细则（05/06/07/08）与上表 `adapters/` 文件中。

## 7. 安装到目标仓库

```text
# 方式一：整目录作为参考
把本目录放到团队可访问的位置，仓库 AGENTS.md 写一行指针。

# 方式二：复制入口文件
把 AGENTS.md 复制到目标仓库根目录；
把 references/、templates/ 放到目标仓库 docs/agent-team/ 下；
按需替换其中的示例路径。

# 方式三（Claude Code）
把**整个目录**放到 ~/.claude/skills/orca-team/（SKILL.md 必须与 SPEC.md、references/ 同级，
否则入口内的相对链接会断）；也可在项目内放 .claude/skills/orca-team/ 随仓库分发。
```

安装后自检：让一个 agent 只读工单就能说出“目标 / 禁改 / 归属 / 验收命令”，说明入口有效。

## 8. 索引

- 规范正文：`SPEC.md`
- 角色：`references/01-roles.md`
- 流程与门：`references/02-workflow.md`
- 工单：`references/03-ticket.md`
- 并行：`references/04-parallelism.md`
- 模型：`references/05-model-assignment.md`
- 接管：`references/06-takeover-recovery.md`
- 验收：`references/07-verification.md`
- Orca 命令：`references/08-orca-cli.md`
- 辅助脚本：`scripts/README.md`
- 示例：`profiles/example-minimal/`
