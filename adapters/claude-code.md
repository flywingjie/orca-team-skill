# 适配：Claude Code

## 1. 接入方式

**方式 A：作为 skill 安装（推荐）**

把本目录的以下内容放到 `~/.claude/skills/orca-team/`：

```text
~/.claude/skills/orca-team/
├── SKILL.md
├── references/
└── templates/
```

`SKILL.md` 的 frontmatter 已包含触发词；之后提到“多 agent / Orca 派单 / 接管 / 补丁验收”时会加载。

**方式 B：仓库级指针**

在目标仓库的 `CLAUDE.md` 写：

```markdown
多 agent 协作按 ~/.claude/skills/orca-team/SPEC.md 执行；
派单前读 references/05-model-assignment.md；接管前读 06-takeover-recovery.md。
```

## 2. 隔离与派单

| 场景 | 做法 |
|---|---|
| 自己做（单 agent） | 直接在当前会话执行；小改动不必开 worker |
| 需要独立工作区/不同 agent | 用 Orca 受监督 worker（见 `references/08-orca-cli.md`） |
| 只需要并行子任务 | 用 Claude Code 自带子 agent；注意它们共享当前上下文，不适合跨工作区并行 |

**判定**：需要“独立工作区 + 可监督生命周期 + 可结算”时用 Orca worker；只是并行读代码/搜索时用自带子 agent。

## 3. 模型

- Claude Code 会话内可用 `/model` 切换（以当前版本为准）。
- 作为 Orca worker 时属于**原生透传**：派单命令带 `--model`（需要 reasoning effort 时加 `--effort`）。
- 不要同时使用 `--terminal` 与 `--model`。

## 4. 派单形态

```bash
# 新建工作区 + 受监督
orca orchestration worker-start --task <task_id> \
  --worktree new-child --name <ws> --repo id:<repo_id> \
  --base-branch origin/main --agent claude --model <model> --effort high --json

# 既有工作区
orca orchestration worker-start --task <task_id> \
  --worktree path:/abs/worktree --agent claude --json
```

## 5. 已知坑

| 坑 | 处置 |
|---|---|
| 首次 bypass 权限确认框导致秒退 | 用户设置预置 `skipDangerousModePermissionPrompt: true` |
| 本地 `main` 落后 | 基线用 `origin/main` |
| 终端读取是累积输出，不是渲染屏 | 需要看当前画面时用 `--screen` |
| 子 agent 共享上下文 | 需要隔离时用 Orca worker，不要用子 agent 冒充 |

## 6. 回报

- 作为 Orca worker：通过 `worker_done` 结算；正文用 `templates/worker-report.md`。
- 作为协调者：收 `worker_done` / `escalation` / `question`，按 `references/07-verification.md` 验收。
- 状态行写入工作区 comment（`orca worktree set --comment`）。

## 7. 限制

- Claude Code 的 skill 是“加载指令”，不强制行为；纪律仍靠工单与角色卡。
- 不能保证子 agent 的模型与主会话不同；需要模型互斥时用 Orca 分配表。
