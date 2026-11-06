# Orca Team Standard（OTS）—— 多 Agent 并行协作标准

[![CI](https://github.com/flywingjie/orca-team-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/flywingjie/orca-team-skill/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![OTS](https://img.shields.io/badge/OTS-1.3.0-blue.svg)](SPEC.md)

> A standard for parallel AI-agent delivery: worktree isolation, five-part tickets,
> evidence-based acceptance, chain-of-evidence takeover. Works with Claude Code, Codex,
> Cursor, pi, OpenCode — or plain humans.

一句话：

> **隔离靠 worktree，派单靠工单，验收靠证据，收敛靠人拍板；模型按能力矩阵指定，接管按证据链放行。**

---

## 为什么需要它

给 2 个以上 AI agent 同时改一个真实仓库，几乎必然撞上这几类事故：

| 事故 | 后果 | OTS 的答案 |
|---|---|---|
| 两个 agent 都认为自己该改同一个文件 | 合并时互相覆盖，且**合并不报错、语义已坏** | 文件所有权分区（`P-4`）+ 工单↔DAG 一致性前置校验 |
| agent 报告「测试全过」，其实根本没跑 | 假完成流进主干 | 证据必须可复核，内容指纹防篡改（`V-4`/`V-10`） |
| worker 顺手改了范围外文件 | 改动失控、评审漏检 | 「越界即停」纪律 + 范围关路径白名单（`K-3`） |
| 多路任务撞同一模型家族 | 集体 429 限流停摆 | 模型家族互斥 + 派单前分配表（`M-1`/`M-7`） |
| worker 沉默 10 分钟就判失败、盲目重跑 | 丢上下文、重复劳动、双写 | 沉默≠失败、接管按证据链（`T-4`/`T-8`） |
| 为让门禁变绿而改断言、关 lint | 门禁形同虚设 | 验收者独立实例 + 禁止削弱检查（`V-6`/`R-11`） |

**OTS 是一套写在仓库文件里的规则 + 七个可执行的校验脚本**：规则喂给 agent，脚本喂给 CI，
人只保留拍板权。规范正文 [`SPEC.md`](SPEC.md) 不绑定任何工具；Orca 是首选执行载体，但不是硬依赖。

---

## 五分钟上手

### 第一步：选一条接入路径

| 你用的工具 | 怎么接入 | 说明 |
|---|---|---|
| **Claude Code** | 整目录复制到 `~/.claude/skills/orca-team/` | 之后说「多 agent 派单 / 补丁验收」自动加载，见 [`adapters/claude-code.md`](adapters/claude-code.md) |
| **Codex / Cursor / OpenCode** | 仓库根放一行 `AGENTS.md` 指针 | 见 [`AGENTS.md`](AGENTS.md) §6 工具适配表 |
| **pi / zcode（自带配置类）** | 读 [`adapters/pi.md`](adapters/pi.md)：自定义 argv 起终端 → 接管为受监督 worker | 模型只能在启动命令里给 |
| **纯人工 / 其他工具** | 拷 [`templates/`](templates/) + [`scripts/`](scripts/README.md)，见 [`adapters/manual.md`](adapters/manual.md) | 降级清单齐全 |

### 第二步：克隆下来跑一遍

```bash
git clone https://github.com/flywingjie/orca-team-skill.git
cd orca-team-skill

# 24 项自检：结构一致性 + 三关夹具端到端 + 集成关临时仓端到端（Python ≥ 3.11，零第三方依赖）
python scripts/selftest.py

# 看一个从规格到验收的完整 DAG 怎么排波次
python scripts/plan.py --dag profiles/example-minimal/orchestration.toml

# 工单 ↔ 编排配置一致性（防「同一文件不并行」被两边写歪）
python scripts/check_consistency.py \
  --tickets profiles/example-minimal/tickets \
  --dag profiles/example-minimal/orchestration.toml
```

期望：`selftest` 全绿（24 项）；`plan` 输出 6 个波次；`check_consistency` 退出码 0。

### 第三步：写第一张工单

复制 [`templates/task-ticket.md`](templates/task-ticket.md)，填满**五段**（缺一段即不合格）：

```markdown
# 工单 <单号>：<一句话标题>
## 目标    ← 精确到目录/文件；列出「不碰」的相邻文件
## 改动    ← 逐条可核对的结果
## 约束    ← 口径 / 禁改 / 契约归属 / 迁移号 / 基线 / 越界与歧义处置
## 归属    ← 本单独占文件 / 依赖 / 与他单相邻 / 分支 / 补丁包路径
## 验收    ← 命令 + 期望结果 + 负路径覆盖（可执行、可判定）
```

之后就是循环：**拆单 → 派单 → 收补丁 → 三关验收 → 逐文件提交 → 回写**。

---

## 怎么用在真实项目里（按规模取用三档）

规范不搞一刀切：先判**档位**（`S-4`），档位决定你要做多少功课。
**任何档位都不裁剪五段式工单与实跑证据。**

| 档 | 什么时候用 | 必做产物 | 你要跑的命令 / 用到的文件 |
|---|---|---|---|
| **L0 轻** | 单会话能完成；无契约变更；只动一个归属域 | 一张五段式工单 + 定向验收证据 | `templates/task-ticket.md`（DAG、分配表、决策门全省） |
| **L1 标** | 2–4 张工单；有依赖；契约已存在 | 工单 + 文件所有权表 + 任务×模型分配表 + 定向验证 | `plan.py`（波次）、`preflight.py`（分配表）、`check_consistency.py`（对齐） |
| **L2 重** | 要定义/改动契约；多实例并行；跨工具或需要接管 | 全七阶段 + 补丁三关 + 决策门 + Runbook | 上面全部 + `accept_patch.py` / `check_evidence.py` / `check_integration.py` + `templates/runbook.md` |

升档看**触发条件**不看难度（`S-5`）：难度高 ≠ 档位高，难度高只调四个旋钮——
角色（顾问/设计提前介入）、模型（换更强推理模型）、验收强度（定向 → 全量门禁 → 回归）、拆单粒度（拆得更细）。

### 场景 A：单兵 AI 编码（L0）

你 + 一个 Claude Code / Codex 会话改自己的仓库：

1. 写一张五段式工单，把「越界即停、歧义处置、不 commit」写进约束段；
2. agent 改完，**让它贴出命令与输出**，而不是贴「已完成」；
3. 你自己复跑验收命令——「应该能跑」不算完成。

> 单 agent 也别跳过工单：写下来的过程就是你防止范围漂移的锚。

### 场景 B：2–4 张单并行（L1）

前后端拆开、模块间有依赖：

1. 每张单一份五段式工单，「归属」段列出**互不相交**的独占文件；
2. `plan.py` 切波次（同波不得撞文件、不得撞模型家族），`preflight.py` 出分配表；
3. `check_consistency.py` 保证工单与编排配置两边写的是同一批事实；
4. worker 各自在独立 worktree 里零 commit 交付补丁包，你按三关验收。

### 场景 C：契约级多 agent 交付（L2）

涉及接口契约、多实例、跨工具、需要决策门与接管预案：

1. 按七阶段走：立项 → 方案（规格门）→ 拆单 → 并行实施 → 收口（设计门）→ 验收（交付门）→ 回写；
2. DAG 节点与基线规则见 [`references/02-workflow.md`](references/02-workflow.md)（契约定义/产物各有唯一 owner）；
3. 补丁三关全自动：范围关 → 正确关 → 集成关，全过才逐文件 commit；
4. 用 [`templates/runbook.md`](templates/runbook.md) 记录编排现场，让接管者只看本文件就能接上。

---

## 一次完整交付长什么样

```text
① 拆单    templates/task-ticket.md → 工单 API-014（五段齐全，依赖写明 CTR-001）
② 预检    plan.py 出波次 → preflight.py 出「任务 × 模型分配表」→ check_consistency.py 对齐两边
③ 派单    正文 = 角色卡 + 工单 + 纪律尾巴（见下）→ 独立 worktree，worker 零 commit
④ 交付    补丁包 patches/API-014/backend/
             manifest.json   任务号 / 基线 commit / 文件清单 / 自测命令与结果
             change.patch    只含所有权路径的二进制安全 diff
             evidence/       每条命令的真实输出
⑤ 三关    范围关 accept_patch.py  → 路径 ⊆ 归属、无凭据、可应用
          正确关 check_evidence.py → 验收命令逐条跑过、内容指纹一致、负路径覆盖
          集成关 check_integration.py → 拓扑序合入、跨补丁无同文件多 owner、门禁实跑
⑥ 验收    独立实例条款级核验 → 协调者逐文件 commit（禁 git add -A / commit -a）
⑦ 回写    进度、决策、遗留风险、工作区处置
```

manifest 里的关键字段（[`references/07-verification.md`](references/07-verification.md) §1.1 有完整样例）：

```json
{
  "ticket": "API-014",
  "base_commit": "<基线 sha>",
  "files": ["server/api/orders.py", "server/tests/test_orders.py"],
  "checks": [
    { "command": "python -m pytest server/tests/test_orders.py -q",
      "exit_code": 0,
      "evidence": "evidence/test-output.txt",
      "evidence_sha256": "<证据文件字节的 64 位小写 hex>" },
    { "command": "python scripts/check_evidence.py --ticket <坏单> --patch-dir <目录>",
      "exit_code": 1, "expect_exit_code": 1,
      "evidence": "evidence/negative-probe.txt" }
  ]
}
```

- `evidence_sha256`：证据在交付后被改动 → 指纹不一致 → **判失败**（只证明完整性，不证明真实性）；
- `expect_exit_code`：负路径探针的合法出口——「证明工具真的能检出问题」也是一等验收项（`V-12`）；
- worker 结算必须回**三句**：做了什么 / 凭什么算做完（命令与结果）/ 还有什么没做；失败必须显式写失败。

---

## 怎么把规则喂给 AI

派单正文 = **角色卡 + 工单 + 纪律尾巴**，三段缺一不可（[`references/03-ticket.md`](references/03-ticket.md) §4）：

```markdown
# 角色卡        ← templates/role-card.md：身份 / 职责 / 边界 / 交付物 / 禁止
# 本次任务      ← 工单五段式全文（自足，不依赖对话上下文）
# 纪律
- 只改「归属」段点名的文件；需要越界时停下上报。
- 不 commit / push / 合并；破坏性 git 命令全禁。
- 收尾交付补丁包（manifest + change.patch + evidence）。
- 回报三句：做了什么 / 凭什么算做完 / 还有什么没做。
- 失败必须显式写为失败。
```

AI 最容易犯的错，规范里每一条都有对应的**闸门**（条目号可直接引用为 `ots:<条目>`）：

| AI 的坏习惯 | 规范闸门 | 机械校验 |
|---|---|---|
| 改了别人的文件 | `P-4` / `R-5` | `check_consistency.py`、集成关 `file-multi-owner` |
| 没跑测试却说跑过 | `V-4` / `V-10` | `check_evidence.py`（指纹不一致即失败） |
| 范围外「顺手改一下」 | `K-3` / `V-2` | 范围关 `accept_patch.py` 路径白名单 |
| 撞模型家族集体限流 | `M-1` / `M-7` | `preflight.py` 分配表 + 派单后 1 分钟开工确认 |
| 沉默就判失败、盲目重派 | `T-4` / `T-8` | [`references/06-takeover-recovery.md`](references/06-takeover-recovery.md) 接管证据链 |
| 为过检查改断言、关 lint | `V-6` / `X-6` | 验收者必须独立实例（`R-11`） |
| worker 自己 commit 改历史 | `V-1` / `X-2` | 零 commit 交付 + 协调者逐文件提交 |

口径引用带命名空间（`ots:R-7` / `spec:<路径>#<锚点>` / `dec:<编号>`），裸编号不合规（`K-6`）。

---

## 工具适配矩阵（谁能指定模型）

| agent | 类别 | 模型怎么指定 | 接入文档 |
|---|---|---|---|
| Claude Code | A 原生透传 | 派单命令带 `--model`（配 effort 需同时带） | [`adapters/claude-code.md`](adapters/claude-code.md) |
| Codex / Cursor / Antigravity / Grok / Muse | A 原生透传 | `--model [--effort]` | [`adapters/codex.md`](adapters/codex.md)、[`adapters/cursor.md`](adapters/cursor.md) |
| OMP | B 受限透传 | 只带 `--model`，不带 effort | [`references/05-model-assignment.md`](references/05-model-assignment.md) |
| OpenCode | B 受限透传 | 必须挂既有工作区 | [`adapters/opencode.md`](adapters/opencode.md) |
| pi / zcode / 其他自带配置 | C 自带配置 | 自定义 argv 起终端 → 接管 → 补发工单 | [`adapters/pi.md`](adapters/pi.md) |
| 纯人工 | — | — | [`adapters/manual.md`](adapters/manual.md) |

决策树与家族互斥口径见 [`references/05-model-assignment.md`](references/05-model-assignment.md)。

---

## 脚本速查（Python ≥ 3.11，零依赖）

| 脚本 | 干什么 | 典型时机 |
|---|---|---|
| `scripts/plan.py` | 读 DAG 切波次；查文件冲突、模型冲突、worktree 复用（`L-1`） | 派单前 |
| `scripts/preflight.py` | 产出「任务 × 模型分配表」，查模型互斥 | 派单前 |
| `scripts/check_consistency.py` | 工单 ↔ 编排配置四类一致性（单号/归属/依赖/基线） | 派单前 |
| `scripts/accept_patch.py` | **范围关**：路径白名单、凭据拦截、证据在包内、可应用 | 收补丁时 |
| `scripts/check_evidence.py` | **正确关**：验收命令 × manifest × 证据 × 内容指纹 × 期望退出码 | 收补丁时 |
| `scripts/check_integration.py` | **集成关**：拓扑序合入、跨补丁所有权、范围、门禁与消费方实跑 | 收口时 |
| `scripts/selftest.py` | 本仓自检 24 项（含三关端到端夹具） | 改本仓脚本后 |

统一退出码：`0` 通过 / `1` 有失败项 / `2` 参数或结构错误；全部支持 `--json`。
`scripts/fixtures/` 是随仓分发的三关静态夹具，用法见 [`scripts/README.md`](scripts/README.md)。

---

## 在现有仓库落地（checklist）

1. **入口进仓库**：仓库根放 [`AGENTS.md`](AGENTS.md)（Codex/pi/Cursor 等自动读取），
   或按 [`adapters/claude-code.md`](adapters/claude-code.md) 装成 Claude Code skill；
   项目自身规则优先于本规范（`SPEC.md` §1）。
2. **判档位**（L0/L1/L2），确定必做产物。
3. **写第一张工单**（五段式；基线写 `origin/main`，不写可能落后的本地 `main`）。
4. **派单前**：`plan.py` + `preflight.py` + `check_consistency.py`。
5. **收单三关**：`accept_patch.py` → `check_evidence.py` → `check_integration.py`，全过才提交。
6. **逐文件 commit**（禁 `git add -A` / `commit -a`），回写进度与遗留风险。

安装自检方法：让一个 agent 只读工单就能说出「目标 / 禁改 / 归属 / 验收命令」，说明入口有效。

---

## 目录结构

```text
orca-team-skill/
├── SPEC.md                  # 规范正文（唯一权威）
├── SKILL.md                 # Claude Code skill 入口
├── AGENTS.md                # 跨工具入口
├── references/              # 分主题细则（角色/流程/工单/并行/模型/接管/验收/Orca CLI）
├── templates/               # 工单、角色卡、回报、状态行、Runbook
├── adapters/                # 各 agent 工具的接入与限制
├── profiles/                # 最小可跑示例（DAG + 7 张示例工单 + 角色卡）
├── scripts/                 # 七个校验脚本 + fixtures/ 三关夹具
└── .github/workflows/       # CI：selftest（ubuntu + windows）
```

示例怎么跑、期望输出是什么，见 [`profiles/example-minimal/README.md`](profiles/example-minimal/README.md)。

---

## 开源协作

- **`main` 分支受保护（严格模式）**：所有改动必须经 Pull Request，CI（`selftest`，ubuntu + windows）
  必须通过；**只有维护者 [@flywingjie](https://github.com/flywingjie) 有权合并**；禁止 force push 与删分支。
- **贡献流程**：fork → `feature/*` 分支 → PR → 等 CI 绿 → 维护者合并。
  提 PR 前本地跑 `python scripts/selftest.py`（24 项全绿）。
- **报 issue**：按本仓自己的证据文化来——附命令与输出，别只描述现象。
- 规范条目改动请同步 `SPEC.md` 附录 B 变更记录；新增工具 = 新增一个 `adapters/<tool>.md`，不动规范正文（`A-2`）。

---

## 设计边界

- **不绑定项目**：规范正文不出现任何项目的域顺序、业务红线、内部路径或决策编号。
- **不绑定工具**：规范正文（`SPEC.md`）不写 Orca 命令；命令出现在 `references/`（05/06/07/08 细则）与 `adapters/`。
- **不替人拍板**：业务政策、资金与数据完整性、合并与发布永远由人决定（`W-4`）。
- **不削弱门禁**：验收以实跑证据为准，「应该能跑」不算完成（`V-7`）。

---

## 版本与许可

当前 **1.3.0**。规范条目编号（`R/L/W/K/P/M/T/V/X/A/S-*`）在 [`SPEC.md`](SPEC.md) 附录 A 汇总，
变更记录在附录 B。

[MIT](LICENSE) © 2026 flywingjie
