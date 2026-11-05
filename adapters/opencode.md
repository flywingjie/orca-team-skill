# 适配：OpenCode

## 1. 接入方式

OpenCode 读取仓库根目录的 `AGENTS.md`（以当前版本为准）。把本目录的 `AGENTS.md` 放到目标仓库根目录。

## 2. 模型指定（受限）

OpenCode 属于**受限透传**：

- 可以通过 `--model` 指定模型；
- 但必须满足：**工作区已存在**、执行主机上的 CLI 已验证、目标模型可用；
- 不支持 `--effort`；
- 不能与 `--terminal` 同用。

```bash
# 必须挂在既有工作区上
orca orchestration worker-start --task <task_id> \
  --worktree path:/abs/worktree --agent opencode --model <model> --json
```

不满足条件时：

1. 先确认 CLI 已安装并在目标主机可用；
2. 先建/选一个工作区；
3. 仍不可用则按“自带配置”处理：用自定义命令启动终端，再接管（见 `pi.md` 的四步）。

## 3. 隔离

- 一任务一工作区；既有工作区必须已经检出正确基线。
- 前后端共用工作区时按「归属」段文件分区。

## 4. 纪律

- worker 只改「归属」段文件；零 commit；交补丁包。
- 不执行破坏性 git 命令。
- 失败显式写失败；回报三句。
- 需要越界/改契约/合并时停下上报。

## 5. 已知限制

| 限制 | 影响 | 处置 |
|---|---|---|
| 不能新建工作区时指定模型 | 新建树下无法选模型 | 先建工作区，再派单 |
| 不支持 `--effort` | 无法调推理档位 | 用模型默认档 |
| 与 `--terminal` 冲突 | 接管终端时不能选模型 | 模型在终端启动命令里给 |
| CLI 未验证 | 派单失败 | 先在目标主机验证 CLI |

## 6. 回报

- 通过 `worker_done` 结算；正文用 `templates/worker-report.md`。
- 状态行写入工作区 comment。
- 协调者按 `references/07-verification.md` 三关验收。
