# 08 Orca CLI 速查与实跑坑位

> 本文是**参考实现**，不是规范正文。Orca CLI 会演进；执行前用 `--help` 核对当前版本参数。
> 规范要求见 `SPEC.md`；模型与接管的完整决策树见 `05`、`06`。

## 1. 先选通道

| 需求 | 通道 | 入口 |
|---|---|---|
| 要监督、问答、结算、DAG、决策门 | **受监督 worker** | `worker-start --spec/--spec-file/--task` |
| 只移交，不跟踪 | **移交（handoff）** | `worktree create --agent --prompt` |
| 已有终端，纳入监督 | **终端接管** | `worker-start --terminal <handle>` |

**两条通道不混用**：handoff 终端的生命周期权威在启动时写入，事后不能收编；
混用了必须先关闭无监督那只，再按受监督通道重起。

## 2. 基础命令

```bash
# 仓库与工作区
orca repo add --path /abs/repo --json
orca repo list --json
orca worktree list --json
orca worktree current --json
orca worktree show --worktree <selector> --json
orca worktree set --worktree active --comment "一句话状态" --json

# Run / Task
orca orchestration run-create --objective "目标" --json
orca orchestration run-current --json
orca orchestration run-list --json
orca orchestration run-use --id <run_id> --takeover-legacy
orca orchestration task-create --spec "<工单正文>" --task-title <title> --deps '["dep"]' --json
orca orchestration task-list --ready --brief

# 受监督 worker
orca orchestration worker-start --task <task_id> --worktree current --agent codex --json
orca orchestration worker-start --task <task_id> \
  --worktree new-child --name <ws> --repo id:<repo_id> \
  --base-branch origin/main --agent claude --model <model> --effort high --json
orca orchestration worker-start --task <task_id> \
  --worktree path:/abs/worktree --agent pi --json
orca orchestration worker-start --task <task_id> \
  --terminal <handle> --worktree path:/abs/worktree --json

# 观察
orca orchestration worker-show --dispatch <dispatch_id> --json
orca orchestration worker-read --dispatch <dispatch_id> --source auto --json
orca orchestration worker-list --json
orca orchestration check --terminal <handle> --wait \
  --types "worker_done,escalation,question" --timeout-ms 900000 --json
orca orchestration inbox --terminal run:<run_id> --limit 50 --json

# 消息
orca orchestration send --to dispatch:<dispatch_id> --subject "主题" --body "内容" --json
orca orchestration reply --id <message_id> --body "回答" --json

# 停止与释放
orca orchestration worker-stop --dispatch <dispatch_id> --json
orca orchestration worker-abandon --dispatch <dispatch_id> --json
orca orchestration worker-release --dispatch <dispatch_id> --json
orca orchestration worker-keep-open --dispatch <dispatch_id> --json

# 决策门
orca orchestration gate-create --task <task_id> \
  --question "[决策门] 请拍板" --options '["通过","打回重做","暂缓"]' --json
orca orchestration gate-list --json
orca orchestration gate-resolve --id <gate_id> --option "通过" --json
```

## 3. 终端与移交

```bash
# 建终端并指定模型（C 类 agent 的必需路径）
orca terminal create --worktree path:/abs/worktree \
  --command "pi --model provider/model --approve" --json

orca terminal wait --terminal <handle> --for tui-idle --timeout-ms 120000 --json
orca terminal send --terminal <handle> --text "$(cat ticket.md)" --enter --wait-submit 10 --json
orca terminal read --terminal <handle> --screen --json
orca terminal close --terminal <handle> --json

# 移交（不建 Run/Task，不跟踪）
orca worktree create --name <task> --no-parent --agent codex \
  --model <model> --effort high --prompt "<简报>" --json

# 既有工作区起新 agent
orca agent launch --agent codex --worktree id:<repo_id>::/abs/path --prompt "<简报>" --json
```

## 4. 模型参数支持（以当前 `worker-start --help` 为准）

| agent | 支持 | 注意 |
|---|---|---|
| Claude / Codex / Cursor / Antigravity / Grok / Muse | `--model` | `--effort` 需同时有 `--model` |
| OMP | `--model` | 不支持 `--effort` |
| OpenCode | `--model` | 必须既有工作区、CLI 已验证、模型可用；不支持 `--effort` |
| pi / zcode / 其他 | 不透传 | 用自身配置启动；需自定义 argv + 终端接管 |
| 任意 agent + `--terminal` | 不可与 `--model` 同用 | 模型由终端启动命令决定 |

## 5. 消费侧坑位（真实踩过）

### 坑 1：`check --wait` 每 15s 吐 `_keepalive`

`--wait` 会周期性输出 `{"_keepalive":...}`，与 stdout 合流后 JSON 变成多对象序列。
**处置**：逐对象解析，过滤 `_keepalive`，取最后一个。

### 坑 2：Windows 管道按 GBK 解码

中文工单经管道时会被错解码，JSON 中途损坏。
**处置**：按字节读 → `utf-8` 解码；或设置 `PYTHONIOENCODING=utf-8`。

### 坑 3：`--ack` 要批 `deliveryId`

`check --ack <message_id>` 不报错但不生效。
**处置**：`check` 取批 → 处理 → `check --ack <deliveryId>`。

### 坑 4：`--spec` 不等于已投递

`worker-start --spec/--spec-file --terminal <handle>` 只登记 Task 并接管终端，
**不会**把正文打进 agent。
**处置**：补 `terminal send`，并用 `worker-read --source terminal` 确认它开始读文件/执行命令。

### 坑 5：既有工作区不能带建树参数

`--worktree path:<x>` 与 `--name` / `--repo` / `--base-branch` / `--setup` 同用会被拒。
**处置**：既有工作区只给 `--worktree`。

### 坑 6：handoff 与 supervised 混用

先用 `worktree create --agent --prompt` 派单，再想收编为受监督 worker：会被拒或两只 agent 互踩。
**处置**：先判角色再选通道；混用了先 `terminal close` 无监督那只。

### 坑 7：基线用本地 `main`

本地 `main` 可能落后远端很多，worker 会基于旧代码开工。
**处置**：`--base-branch origin/main`（或项目等价物）。

### 坑 8：Claude 权限确认框导致秒退

首次以 bypass 模式启动会弹确认框，默认选中“退出”。
**处置**：预置 `skipDangerousModePermissionPrompt: true`（写入用户设置）。

### 坑 9：Windows 命名管道被沙箱挡（EPERM）

桌面版运行时的命名管道可能拒绝 CLI 连接。
**处置**：起 headless `orca serve`，并固定 `ORCA_USER_DATA_PATH`；所有命令带同一值。

### 坑 10：429 伪装成停滞

限流后 TUI spinner 空转、计数器冻结，看起来像卡死。
**处置**：先读屏幕找 `429` / `rate_limit_error`；限流恢复后 `send` 续跑，不要重启。

### 坑 11：manifest 自报模型不可信

worker 可能写一个不存在的模型名。
**处置**：以派单分配表 + 终端状态为准；范围关顺手核对。

### 坑 12：重试不继承 placement

`--retry-of <dispatch_id>` 不会继承原工作区/模型/服务器。
**处置**：重试时重新指定 `--on` / `--worktree` / `--agent`。

### 坑 13：Windows 写出的 JSON / TOML 带 BOM

PowerShell 5.1 的 `Set-Content` / `Out-File -Encoding UTF8` **默认写 BOM**。
本机产出的 `manifest.json`、TOML 配置带 BOM 是常态，解析侧必须容错。

**症状**：`json.loads` 报 `Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)`，
或文件被误判成「不是合法 JSON / 非法 TOML」。

**处置**：
- 解析 JSON / Markdown：读文件用 `encoding="utf-8-sig"`（同时兼容有 BOM 与无 BOM）。
- 解析 TOML：`tomllib.load(open(path, "rb"))`（二进制模式），或 `tomllib.loads(text)` 且 text 用 `utf-8-sig` 读出。
- 写入侧要无 BOM：`[System.IO.File]::WriteAllText($p, $t, (New-Object System.Text.UTF8Encoding($false)))`。
- **注意**：读取侧容错 ≠ 放宽判定。BOM 修好后，正确的 JSON 仍必须通过同样的检查项。

## 6. 稳健的 JSON 解析片段

```python
import json
import sys

raw = sys.stdin.buffer.read().decode("utf-8")  # Windows 管道按字节读
decoder, objs, i = json.JSONDecoder(), [], 0
while i < len(raw):
    while i < len(raw) and raw[i] in " \r\n\t":
        i += 1
    if i >= len(raw):
        break
    obj, i = decoder.raw_decode(raw, i)
    objs.append(obj)

objs = [o for o in objs if not (isinstance(o, dict) and o.get("_keepalive"))]
result = objs[-1] if objs else {}
```

## 7. 冒烟检查顺序

```text
① orca status --json                      → ok: true
② orca repo list --json                   → 目标仓库存在
③ orca worktree current --json            → 当前工作区正确
④ orca orchestration run-create ...       → 拿到 run_id
⑤ task-create + worker-start（1 个任务）  → 拿到 dispatch_id
⑥ worker-read                             → 确认真的在读文件/执行命令
⑦ worker_done                             → 结算成功
⑧ gate-create                             → 门可创建
```

冒烟通过再上并行；不要第一次就跑满并行度。
