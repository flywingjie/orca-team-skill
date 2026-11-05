# 适配：Codex

## 1. 接入方式

Codex 读取仓库根目录的 `AGENTS.md`。把本目录的 `AGENTS.md` 放到目标仓库根目录，或并入项目已有版本。

```markdown
多 agent 协作按 AGENTS.md（Orca Team Standard）执行；
规范正文：SPEC.md；模型与接管：references/05、06。
```

## 2. 隔离与派单

**受监督（推荐）**：

```bash
orca orchestration worker-start --task <task_id> \
  --worktree new-child --name <ws> --repo id:<repo_id> \
  --base-branch origin/main --agent codex --model <model> --effort high --json
```

**移交（不跟踪）**：

```bash
orca worktree create --name <task> --no-parent --agent codex \
  --model <model> --effort high --prompt "<简报>" --json
```

**既有工作区起新 agent**：

```bash
orca agent launch --agent codex --worktree id:<repo_id>::/abs/path \
  --prompt "<简报>" --json
```

## 3. 模型

- Codex 属于**原生透传**：`--model` 可用；reasoning effort 需要同时给 `--model`。
- 不要与 `--terminal` 同用。
- 重试（`--retry-of`）不继承模型与工作区，必须重新指定。

## 4. 纪律

- worker 只改「归属」段文件；零 commit；交补丁包。
- 不执行破坏性 git 命令。
- 失败显式写失败；回报三句。
- 需要越界/改契约/合并时停下上报。

## 5. 已知坑

| 坑 | 处置 |
|---|---|
| 移交通道启动后想收编为受监督 | 不允许；先关掉无监督那只，再按受监督通道重起 |
| 既有工作区带 `--name` | 被拒；只给 `--worktree path:<x>` |
| 用本地 `main` 当基线 | 用 `origin/main` |
| 长输出一次读完 | 用 cursor 分页读，避免截断 |

## 6. 回报

- 通过 `worker_done` 结算；正文用 `templates/worker-report.md`。
- 状态行写入工作区 comment。
- 协调者收报后按 `references/07-verification.md` 三关验收。
