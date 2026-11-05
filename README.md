# Orca Team Standard（OTS）

多角色、多 Agent 并行协作的标准 Skill 与规范。跨项目、跨工具可用；Orca 是首选执行载体与参考实现，但不是硬依赖。

## 一句话

> 隔离靠 worktree，派单靠工单，验收靠证据，收敛靠人拍板；模型按能力矩阵指定，接管按证据链放行。

## 三分钟上手

1. 读 [`SPEC.md`](SPEC.md) 的前 6 节，理解角色、三层模型、七阶段与工单五段式。
2. 按 [`adapters/`](adapters/) 里对应工具的一页说明接入：
   - Claude Code → [`adapters/claude-code.md`](adapters/claude-code.md)
   - pi → [`adapters/pi.md`](adapters/pi.md)
   - Codex → [`adapters/codex.md`](adapters/codex.md)
   - Cursor → [`adapters/cursor.md`](adapters/cursor.md)
   - OpenCode → [`adapters/opencode.md`](adapters/opencode.md)
   - 纯人工 → [`adapters/manual.md`](adapters/manual.md)
3. 复制 [`templates/task-ticket.md`](templates/task-ticket.md) 写第一张工单。
4. 派单前跑 [`references/05-model-assignment.md`](references/05-model-assignment.md) 的预检；
   用 `scripts/check_consistency.py` 校验工单 ↔ 编排配置一致（防「同一文件不并行」被两边写歪）。
5. 收工按 [`references/07-verification.md`](references/07-verification.md) 做补丁三关。
6. 先判**档位**（L0 轻 / L1 标 / L2 重，见 [`references/02-workflow.md`](references/02-workflow.md) §1.1）：
   档位决定必做产物，**工单五段式与实跑证据任何档都不裁剪**。

## 文件索引

| 文件 | 作用 |
|---|---|
| [`SPEC.md`](SPEC.md) | **规范正文（唯一权威）**：角色、流程、并行、模型、接管、验收 |
| [`SKILL.md`](SKILL.md) | Claude Code skill 入口（含 frontmatter 与触发词） |
| [`AGENTS.md`](AGENTS.md) | 跨工具总入口（Codex / pi / Cursor 等自动读取） |
| [`references/01-roles.md`](references/01-roles.md) | 角色职责与禁止事项 |
| [`references/02-workflow.md`](references/02-workflow.md) | 七阶段、DAG 节点、决策门 |
| [`references/03-ticket.md`](references/03-ticket.md) | 工单五段式与反例 |
| [`references/04-parallelism.md`](references/04-parallelism.md) | 并行五条硬规则与文件所有权 |
| [`references/05-model-assignment.md`](references/05-model-assignment.md) | 模型指定决策树、互斥、预检 |
| [`references/06-takeover-recovery.md`](references/06-takeover-recovery.md) | 接管、失联、429、停止与释放 |
| [`references/07-verification.md`](references/07-verification.md) | 补丁三关、证据、回报格式 |
| [`references/08-orca-cli.md`](references/08-orca-cli.md) | Orca CLI 速查与实跑坑位 |
| [`templates/`](templates/) | 工单、角色卡、回报、状态行、Runbook |
| [`adapters/`](adapters/) | 各 agent 工具的接入与限制 |
| [`profiles/example-minimal/`](profiles/example-minimal/) | 最小可跑示例（无业务口径） |
| [`scripts/`](scripts/README.md) | 七个只读辅助脚本：波次计算、模型预检、补丁三关（范围/正确/集成）、工单↔DAG 一致性、本仓自检（用法见其 README） |
| [`profiles/example-minimal/tickets/`](profiles/example-minimal/tickets/) | 7 张示例工单（`SPEC-001`/`CTR-001`/`API-014`/`WEB-021`/`INT-001`/`VER-001`/`ACC-001`），照抄格式用 |

## 目录约定

```text
orca_team_skill/
├── SPEC.md                  # 规范正文
├── SKILL.md                 # Claude Code skill 入口
├── AGENTS.md                # 跨工具入口
├── references/              # 分主题细则
├── templates/               # 可复制模板
├── adapters/                # 工具适配
├── profiles/                # 项目档案/示例
└── scripts/                 # 只读计算与验收辅助
```

根级 `tickets/` 是本仓自身的工单与证据留痕，**本地保留、不入库**（`.gitignore` 用根锚定 `/tickets/`）；
随仓分发的示例工单在 `profiles/example-minimal/tickets/`。

## 设计边界

- **不绑定项目**：规范正文不出现任何项目的域顺序、业务红线、内部路径或决策编号。
- **不绑定工具**：规范正文不写 Orca 命令；命令只出现在 `references/08-orca-cli.md` 与 `adapters/`。
- **不替人拍板**：业务政策、资金与数据完整性、合并与发布永远由人决定。
- **不削弱门禁**：验收以实跑证据为准，“应该能跑”不算完成。

## 版本

当前 `1.2.0`。规范条目编号（`R-*`、`L-*`、`W-*`、`K-*`、`P-*`、`M-*`、`T-*`、`V-*`、`X-*`、`A-*`、`S-*`）在 `SPEC.md` 附录 A 汇总，供工单引用。
