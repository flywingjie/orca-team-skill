# 适配：pi

## 1. 接入方式

pi 读取仓库根目录的 `AGENTS.md`。把本目录的 `AGENTS.md` 放到目标仓库根目录，或并入项目已有的 `AGENTS.md`。

```markdown
多 agent 协作按 AGENTS.md（Orca Team Standard）执行；
细则在 docs/agent-team/ 或 ~/.claude/skills/orca-team/references/。
```

## 2. 模型指定（重点）

pi 属于**自带配置**类：编排 CLI 的 `--model` **不透传**给 pi，传了会被拒或忽略。

正确路径是“自定义 argv + 终端接管”四步：

```bash
# ① 建终端，用 pi 自己的 --model 启动
orca terminal create --worktree path:/abs/worktree \
  --command "pi --model provider/model --approve" --json
# → 记下返回的 terminal handle

# ② 等终端空闲
orca terminal wait --terminal <handle> --for tui-idle --timeout-ms 120000 --json

# ③ 登记 Task 并接管终端（不会投递工单正文）
orca orchestration worker-start --spec-file ticket.md \
  --terminal <handle> --worktree path:/abs/worktree --agent pi --json

# ④ 补发工单正文（关键）
orca terminal send --terminal <handle> \
  --text "$(cat ticket.md)" --enter --wait-submit 10 --json
```

**确认派到**：

```bash
orca orchestration worker-read --dispatch <dispatch_id> --source terminal --json
```

看到 pi 开始读文件/执行命令才算真的开工。只发消息通道（邮箱）不够，终端模式 pi 不消费邮箱。

## 3. 工作区

- **先建工作区**：C 类路径要求工作区已存在，`terminal create` 与 `worker-start` 必须指向同一路径。

  ```bash
  orca worktree create --name <ws> --repo <selector> \
    --base-branch origin/main --json
  orca worktree show --worktree name:<ws> --json   # 取绝对路径
  # 后续统一用 --worktree path:/abs/worktree
  ```

- 推荐一任务一工作区；前后端可共用一个工作区，但必须按「归属」段文件分区。
- pi 默认读 `AGENTS.md` / `CLAUDE.md`；工单与纪律照样投喂。
- 禁止破坏性 git 命令（`checkout` / `restore` / `clean` / `reset` / `stash`）。

## 4. 限流与停滞

- pi 被限流时可能 TUI spinner 空转、计数器冻结，看起来像卡死。
- **先读屏幕**找 `429` / `rate_limit_error`；限流恢复后直接 `terminal send` 续跑，不要 stop/重启（会丢上下文）。
- 判停滞三证：屏幕两帧逐字节相同 + 计数器冻结 + `terminal wait --for tui-idle` 超时。
- 只有“没消息”不能判停滞。

## 5. 停止与释放

```bash
orca orchestration worker-stop --dispatch <dispatch_id> --json      # 明确取消
orca orchestration worker-abandon --dispatch <dispatch_id> --json  # 进程状态未知
orca orchestration worker-release --dispatch <dispatch_id> --json  # 结算后回收
orca orchestration worker-keep-open --dispatch <dispatch_id> --json
```

## 6. 回报

- 收到工单正文后，按 `templates/worker-report.md` 结算。
- 状态行写入工作区 comment（一句话）。
- 失败必须显式写“失败”，并附命令与输出。

## 7. 限制

- 模型由启动命令决定；接管后不能再用 `--model` 改。
- 既有工作区不接受 `--name` / `--repo` / `--base-branch` / `--setup`。
- 终端模式的投递结果无法由 pi 自己证明；必须以 `worker-read` 的动作为准。
