# 05 模型指定与并行分配

> 对应 `SPEC.md` §8。本文回答两个问题：**能不能指定模型**、**多个 worker 怎么分模型**。

## 1. 先判断 agent 属于哪一类

不同 agent 对“启动时指定模型”的支持不同。按能力分三类：

| 类别 | 判定 | 典型 agent | 派单方式 |
|---|---|---|---|
| **A. 原生透传** | 派单命令接受 provider model id | Claude、Codex、Cursor、Antigravity、Grok、Muse | 直接在派单命令带 `--model` |
| **B. 受限透传** | 只接受部分参数，或要求既有工作区 | OMP（只接受模型）、OpenCode（要求既有工作区 + CLI 已验证） | 按限制裁剪参数 |
| **C. 自带配置** | 启动参数不透传模型，模型由 agent 自身配置决定 | pi、zcode 及其他“用自己的配置起”的 agent | 自定义命令启动终端 → 接管为受监督 worker |

**关键提醒**：

- 多数编排 CLI 的 `--model` 只对 A 类透传；C 类传了会被拒或静默忽略。
- `--model` 通常**不能与** `--terminal` 同用：终端接管时模型已由终端启动命令决定。
- `--effort`（reasoning effort）通常要求同时有 `--model`；不支持 effort 的 agent 不要传。

## 2. 决策树

```text
需要指定模型？
├─ 否
│   └─ 用 agent 默认配置派单
│
└─ 是
    ├─ 目标 agent 属于 A 类（原生透传）
    │   └─ 派单命令直接指定：
    │      worker-start --task <id> --worktree new-child --name <ws> \
    │        --repo <repo-selector> --base-branch origin/main \
    │        --agent <id> --model <provider/model> [--effort <level>]
    │
    ├─ 目标 agent 是 OMP
    │   └─ 只指定模型，不指定 effort
    │
    ├─ 目标 agent 是 OpenCode
    │   └─ 必须：
    │      ① 工作区已存在（不能新建）
    │      ② 执行主机上的 CLI 已验证
    │      ③ 目标模型可用
    │      派单：worker-start --task <id> --worktree <existing> \
    │              --agent opencode --model <id>
    │
    ├─ 目标 agent 属于 C 类（pi / zcode / 自带配置）
    │   └─ 自定义 argv + 终端接管（四步，缺一不可）：
    │      ① 建终端，用自定义命令起 agent：
    │         terminal create --worktree <existing-worktree> \
    │           --command "<agent> --model <provider/model> <其他启动参数>"
    │         → 拿 terminal handle
    │      ② 等终端进入空闲：
    │         terminal wait --terminal <handle> --for tui-idle --timeout-ms 120000
    │      ③ 登记 Task 并接管终端：
    │         worker-start --spec-file <工单文件> --terminal <handle> \
    │           --worktree <existing-worktree> --agent <id>
    │      ④ 补发工单正文（关键）：
    │         terminal send --terminal <handle> \
    │           --text "$(cat <工单文件>)" --enter --wait-submit 10
    │
    └─ 目标终端已经在跑
        └─ 用 --terminal 接管；不要同时传 --model
```

### 2.1 为什么 C 类要“四步”

- `--model` 不透传 C 类 agent，所以模型只能在启动命令里给。
- `worker-start --terminal` 只**登记 Task/Dispatch 并接管终端**，不会把工单正文打进终端。
- 因此必须再 `terminal send` 一次；并以“agent 开始读文件/执行命令”作为真正派到的证据。
- 不要只发邮件/消息通道：终端模式的 agent 可能不消费邮箱。

## 3. 派单前预检

### 3.1 产出「任务 × 模型」分配表

格式：

| 任务 | 角色 | agent | 模型 | 模型家族 | 工作区 | 状态 |
|---|---|---|---|---|---|---|
| API-014 | 后端 | pi | `provider/model-a` | family-a | ws-api | 待派 |
| WEB-021 | 前端 | pi | `provider/model-b` | family-b | ws-web | 待派 |
| INT-001 | 集成 | claude | `model-c` | family-c | ws-int | 依赖未就绪 |

要求：

- 同一时刻“状态 = 在跑”的行，模型家族不得重复。
- 分配表是验收时核对模型归属的唯一依据。
- worker 自报的模型（如 manifest 里的 model 字段）不可信。

### 3.2 鉴权预检

- 用 agent 工具自带的鉴权检查（如 pi 的 `auth check`）确认可达。
- **鉴权通过 ≠ 配额充足**：很多限流只在真正跑起来后才暴露。

### 3.3 派单后 1 分钟内的确认

- 读终端/worker 输出，确认它真的开始读文件、执行命令。
- 出现 `429` / `rate_limit_error` / 限流字样：立即降并行或换模型。
- 不要把限流导致的“空转”判成“卡死”并盲目重启；重启会丢上下文。

## 4. 模型互斥

### 4.1 判定口径

- 按**底层模型家族**判重，不按显示名。
- **家族归并口径**（`SPEC.md` `M-7`）：厂商前缀 + 主干版本。
  同厂商不同代 = **不同家族**；同代不同别名/不同尺寸 = **同家族**。
  判定有歧义时按更严格的一侧执行（视作同家族）。
  一句话：**同一主干版本的不同别名/尺寸算同一家族（`gpt-5.1` 与 `gpt-5.1-codex`）；
  跨代算不同家族（`gpt-4` 与 `gpt-5.1`）。**
- 协调者与 worker **同时计入并发**：协调者用 family-a、worker 也用 family-a 时视为冲突，
  **worker 让位**（先派其它家族），并在分配表里标注该冲突与处置。
- **家族互斥 ≠ 禁止同模型并行**：家族互斥的目的是避免限流与质量塌陷叠加。
  若有配额证据（窗口剩余额度可支撑 N 路并发），允许同家族并行 N 路，
  但必须在分配表逐行标注「并发路数」与「配额依据」，
  并按本文件 §3.3 在派单后 1 分钟内逐路确认开工。
- **配额门禁**：派单前查该模型的窗口剩余额度；任一窗口低于 20% 时不得用该模型并行，
  降级到其它家族或降低并行度（`SPEC.md` `M-3`、`M-5`）。
  （20% 是本细则的保守**操作经验值**，`SPEC.md` `M-7` 未规定具体百分比；
  作为派单前的下限执行，不构成规范硬数字。）

### 4.2 不可达时的降级顺序

1. 换同能力的其他家族模型；
2. 降低并行度，串行执行；
3. 延迟非关键任务到下一波；
4. 最后才考虑用同一模型串行。

**禁止**：为了凑并行度，把两个任务都塞给同一模型同时跑。

## 5. 模型变更

- 不得在派单后静默换模型。
- 需要换时：更新分配表 → 在状态行记录原因 → 必要时重派（带原失败标识）。
- 重派不继承原工作区与模型，必须重新指定。

## 6. 常见错误

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 对 pi 传 `--model` | 被拒或忽略 | 自定义 argv + 终端接管 |
| `--model` 与 `--terminal` 同用 | 参数冲突 | 终端接管时不传模型 |
| 用本地 `main` 当基线 | worker 读到旧代码 | 用 `origin/main` |
| 只做鉴权预检就派满 8 个 | 中途限流，集体停摆 | 派单后 1 分钟逐个确认 |
| 相信 manifest 里的 model | 分配表失真 | 以终端状态/分配表为准 |
| 同一模型并行两个任务 | 互相拖慢、限流叠加 | 按家族互斥 |

## 7. 一页检查表

- [ ] 每个 agent 的能力类别已确认（A/B/C）
- [ ] 分配表已出，模型家族无重复
- [ ] C 类 agent 已按“四步”派单，并补发了工单正文
- [ ] `--model` 与 `--terminal` 没有同时使用
- [ ] 派单后 1 分钟内确认真正开工
- [ ] 没有出现限流未处置
- [ ] manifest 记录与分配表不一致时，以分配表为准
