# 辅助脚本

七个**只读**脚本，标准库实现，Python 3.11+（`tomllib`）。均支持 `--json`，
退出码统一为：`0` 通过 / `1` 有失败项 / `2` 参数或读取错误。

它们只做机械检查，不替代人工验收，也不修改仓库、不调用 Orca。

| 脚本 | 对应规范条目 | 管什么 |
|---|---|---|
| `plan.py` | `P-4`、`P-5` | 波次计算：依赖、文件冲突、模型家族冲突 |
| `preflight.py` | `M-1`、`M-2`、`M-5`、`P-4` | 派单前预检：产出「任务 × 模型」分配表 |
| `accept_patch.py` | `V-1` ~ `V-4` | 补丁**范围关** |
| `check_evidence.py` | `V-4`、`V-10` | 补丁**正确关**：验收项 × manifest × evidence × 内容指纹 |
| `check_integration.py` | `V-3`、`V-11`、`P-4` | 补丁**集成关**：按依赖合入、范围与所有权、门禁与消费方 |
| `check_consistency.py` | `K-7`、`L-4`、`P-4` | 工单 ↔ 编排 DAG 的四类一致性 |
| `selftest.py` | —— | 本仓自身冒烟：把示例期望值变成断言 |

## plan.py — 波次计算

读取 DAG，按依赖与约束切波，提前暴露环、缺失依赖、文件冲突、模型家族冲突。

```bash
python scripts/plan.py --dag profiles/example-minimal/orchestration.toml
python scripts/plan.py --dag path/to/orchestration.toml --max-parallel 4 --json
```

约束（对应 `SPEC.md` `P-4`、`P-5`）：

- 依赖必须存在，不得有环；
- 同一波次内 `files` 不得有交集；
- 同一波次内 `model_family` 不得重复；
- 并行度不超过 `max_parallel`（默认取 DAG 的 `orchestration.max_parallel`，再缺省为 8）。

输出每波任务、依赖、门禁与延后原因。脚本只给出可行排布建议，实际派单仍由协调者决定。

## preflight.py — 派单前预检

产出「任务 × 模型」分配表，检查模型互斥、文件重叠与可选鉴权命令。

```bash
python scripts/preflight.py \
  --dag profiles/example-minimal/orchestration.toml \
  --agents profiles/example-minimal/agents.toml

# 只看第 2 波
python scripts/preflight.py --dag ... --agents ... --wave 2

# 追加鉴权检查（命令由使用者提供）
python scripts/preflight.py --dag ... --agents ... \
  --auth-cmd "pi auth check" --auth-cmd "claude --version"
```

解析顺序：任务显式 `model` / `model_family` → `agents.toml` 中角色配置 → 报错。
`role` 优先于 `agent` 作为 `agents.toml` 的查找键。

**注意**：鉴权通过 ≠ 配额充足。派单后 1 分钟内必须确认 worker 真正开工；出现 429 立即降并行或换模型（见 `references/05-model-assignment.md`）。

## accept_patch.py — 补丁范围关

检查补丁包的 manifest、路径白名单、凭据文件、证据完整性与 `git apply --check`。

```bash
python scripts/accept_patch.py \
  --manifest patches/API-014/backend/manifest.json \
  --repo /abs/repo \
  --allowed server/api/orders.py \
  --allowed "server/tests/**"
```

检查项（对应 `SPEC.md` `V-1` ~ `V-4`）：

- manifest 必需字段：`ticket` / `worker` / `base_commit` / `files` / `checks`；
- 补丁路径可解析（`diff --git`、`+++ b/`）；
- 补丁路径 ⊆ `--allowed`（支持精确路径、目录前缀、glob）；
- `manifest.files` 与补丁实际路径一致；
- 每条 `checks[].evidence` 存在且非空；
- 拦截 `.env*`、`id_rsa`、`.gh_token`、`credentials.json`、`*.pem/.key/.p12/.pfx`；
- `git apply --check --binary`（可用 `--no-apply-check` 跳过）。

**`--repo` 必须指向基线提交上的干净 worktree**。若工作区已包含同一改动，`git apply --check` 会报“patch does not apply”，这是环境问题而非补丁缺陷。推荐流程：

```bash
git worktree add /tmp/accept-<单号> <base_commit>
python scripts/accept_patch.py --manifest ... --repo /tmp/accept-<单号> --allowed ...
```

范围关通过**不代表**正确关与集成关通过；后者必须实跑门禁并核对 `evidence/`（见 `references/07-verification.md`）。

## check_evidence.py — 正确关

把「工单「验收」段的每条命令是否真跑过、有没有证据」变成机器可读结论。

```bash
python scripts/check_evidence.py \
  --ticket tickets/API-014.md \
  --patch-dir patches/API-014/backend

python scripts/check_evidence.py --ticket ... --patch-dir ... --json
```

检查项：

- 工单验收表逐行解析（表头 `| # | 命令 | 期望结果 |`），命令/期望为空 → `empty-command` / `empty-expectation`（判失败）；
- 工单验收命令必须出现在 `manifest.checks[].command` 中 → 否则 `command-not-executed`（判失败）；
- 每条 check 的 `exit_code` 必须为 0 → 否则 `command-failed`（判失败）；
- 每条 check 的 `evidence` 文件必须存在且非空 → 否则 `evidence-missing` / `evidence-empty`（判失败）；
- manifest 必填字段齐全、`files` 非空 → 否则 `manifest-missing-field` / `manifest-empty-files`（判失败）；
- 负路径覆盖的未勾选项**只报告、不判失败**（`negative-path-unchecked`）。

命令比对前会做规范化：去首尾空白 → 折叠连续空白 → 去行尾续行反斜杠。

### 证据内容指纹（`SPEC.md` `V-10`）

manifest 的每条 `checks[]` **可**记录 `evidence_sha256`：该 evidence 文件**字节**的 sha256
（小写十六进制 64 位）。验收时重算比对：

| 情形 | 报告的 kind | 判失败？ |
|---|---|---|
| 一致 | `evidence-fingerprint-ok` | 否 |
| 不一致 | `evidence-fingerprint-mismatch` | **是**（证据在交付后被改动） |
| 字段格式非法（非 64 位 hex / 非字符串） | `evidence-fingerprint-invalid` | **是** |
| 字段缺失 | `evidence-no-fingerprint` | 否（提示，兼容旧 manifest） |

另有一条**提示级**的弱 provenance 信号：evidence 文本中是否出现该命令
（`evidence-command-echo` / `evidence-no-command-echo`）。
**不判失败**——pytest / npm 等工具的输出通常不含命令行原文。

### 期望退出码（`SPEC.md` `V-12`）

manifest 的 `checks[]` 可声明 `expect_exit_code`（整数，**缺省 0**）。
正确关比对的是「实际 `exit_code` == `expect_exit_code`」，不等即 `command-failed`（判失败）。

用途：**负路径探针**——故意破坏输入、期望校验器检出并以非零退出。
这类行证明「工具真的能检出问题」，其 `exit_code` 记真实值（如 1），
同时声明 `expect_exit_code: 1`；否则「证明工具有效」的证据永远交付不了。

正向验收命令**不得**声明非零期望；缺省为 0，既有 manifest 行为完全不变。

给 worker 用的便利命令（只读打印，不回填 manifest）：

```bash
python scripts/check_evidence.py --ticket <工单.md> --patch-dir <补丁包> --print-fingerprint
# 每行输出：<command>\t<64位hex>
```

> 指纹只证明**完整性**，不证明**真实性**。真实性靠门禁实跑 + 人工复核。

## check_integration.py — 补丁集成关

把集成关（合入后回归、契约产物统一生成、消费方验证）做成机器可执行。

```bash
python scripts/check_integration.py \
  --repo <集成分支的干净 worktree> \
  --patches <补丁包根目录> \
  --dag profiles/example-minimal/orchestration.toml \
  --gate-cmd "python -m pytest -q" \
  --consumer-cmd "npm run build" \
  --evidence-out patches/_integration \
  [--base origin/main] [--allow <共享文件>] [--contract-product <产物路径>] \
  [--timeout 120] [--dry-run] [--json]
```

流程与判据：

1. 读 DAG，按 `depends_on` **拓扑排序**得应用顺序；有环 → 退出码 2；
2. 定位补丁包 `<patches>/<task_id>/[worker/]`；缺失 → `patch-missing`（失败）；
3. 逐个补丁 `git apply --check --binary` → 失败 `apply-check-failed`（失败）并**跳过其下游任务**；
   再 `git apply --binary` → 失败 `apply-failed`（失败）；
4. **跨补丁文件所有权**：同一路径出现在 >1 个补丁的归属文件里 → `file-multi-owner`（失败）
   ——这就是 `P-4` 的机械检查（合并不报错，但语义已经坏了）；
5. **契约产物唯一 owner**：`--contract-product` 指定的路径若出现在 >1 个补丁里
   → `contract-product-multi-owner`（失败）；
6. **变更范围**：合入后 `git diff --name-only <base>` 的实际变更集
   ⊆ 各补丁归属文件 ∪ `--allow` → 越界 `scope-violation`（失败）；
7. 跑 `--gate-cmd`：退出码 ≠ 0 → `gate-failed`（失败），输出存 `<evidence-out>/gate-NN.txt`；
8. 跑 `--consumer-cmd`：退出码 ≠ 0 → `consumer-failed`（失败），输出存 `consumer-NN.txt`；
9. 产出 `<evidence-out>/integration-manifest.json`：每条命令记 `command` / `exit_code` /
   `evidence` / **`evidence_sha256`**，以及 `applied_patches` / `changed_paths` / `scope_violations`。

`--dry-run` 只做 `--check` 与排布推演，**不 apply、不执行任何命令**。

**安全边界**（`SPEC.md` `V-11`）：

- 只允许在被检工作区内执行 `git apply` 与 `git diff`；
  **不得** commit / push / merge / `reset --hard` / clean / checkout；
- **不得**执行破坏性命令（`rm -rf`、`drop table`、`truncate`、`git push` 等）——
  命中即**拒绝执行**并判失败（`cmd-unsafe`）；
  按小写子串匹配，**宁可误伤不可漏执行**：`format`、`truncate` 这类词出现在正常命令里也会被拦下，
  改写命令即可，**不要放宽黑名单**；
- 命令在 `--repo` 下执行，超时判失败（`cmd-timeout`）；
- **证据文件恒为非空**：命令无任何输出时写入最小可复核内容（命令原文 + 退出码 + cwd），
  不得产出 0 字节文件；
- 产物只写 `--evidence-out`，不得写到别处。

## check_consistency.py — 工单 ↔ DAG 一致性

工单是 Markdown、编排是 TOML，两处手写同一批事实；本脚本双向比对四类：

| # | 比对 | 工单侧 | DAG 侧 |
|---|---|---|---|
| 1 | 单号集合 | 标题 `# 工单 <单号>：` | `[[tasks]].id` |
| 2 | 归属文件集 | 「归属」段「本单独占文件」 | `tasks[].files` |
| 3 | 依赖 | 「归属」段「依赖：」 | `tasks[].depends_on` |
| 4 | 基线 | 「约束」段「基线：」 | `tasks[].base_branch`（缺省 `origin/main`） |

```bash
python scripts/check_consistency.py \
  --tickets profiles/example-minimal/tickets \
  --dag profiles/example-minimal/orchestration.toml

python scripts/check_consistency.py --tickets ... --dag ... --json
```

- 退出码：`0` 完全一致 / `1` 存在不一致 / `2` 参数或读取错误；
- 不一致类型：`ticket-only` / `dag-only` / `mismatch` / `missing-section` / `parse-error`；
- 工单文件损坏、缺小节、单行解析失败**不会中断**，记进结果后继续；
- 两侧缺省值对齐：基线缺省 `origin/main`；依赖缺省空列表。

**用途**：这是 `P-4`（同一文件不并行）事故的**前置闸门**——归属文件集一旦两边不一致，
两个 worker 就可能同时改同一个文件，且合并时未必报冲突。

## selftest.py — 本仓自检

把示例的期望值变成可执行断言，防止文档与实现漂移。

```bash
python scripts/selftest.py
python scripts/selftest.py --json
python scripts/selftest.py --dag <path> --agents <path>
```

断言范围：

- `plan.py`：退出码、波次数与序号连续性、任务 id 齐备、门 id 齐备、第 3 波并行构成、`verify`/`accept` 不同波；
- `preflight.py`：退出码、输出含「任务 × 模型分配表」与「预检通过」；
- 七个脚本的 `--help` 均可运行；
- `check_consistency.py` 对示例工单与 DAG 的比对结论为一致（退出码 0）。

并行产出的脚本若当时尚未交付，`--help` 断言会**跳过并标注**，不判失败。

## 典型串法

```bash
# 派单前
python scripts/plan.py --dag orchestration.toml --json > /tmp/waves.json
python scripts/preflight.py --dag orchestration.toml --agents agents.toml
python scripts/check_consistency.py --tickets tickets/ --dag orchestration.toml

# 收单时
python scripts/accept_patch.py --manifest <补丁包>/manifest.json \
  --repo <基线worktree> --allowed <工单归属路径> --allowed <...>
python scripts/check_evidence.py --ticket <工单.md> --patch-dir <补丁包>

# 集成时（收口）
python scripts/check_integration.py --repo <集成分支worktree> --patches <补丁根> \
  --dag <orchestration.toml> --gate-cmd "<全量门禁>" --consumer-cmd "<消费方验证>" \
  --evidence-out patches/_integration

# 本仓自检
python scripts/selftest.py
```

## 共同约定

- **只读**：不修改、不创建、不删除仓库文件；
- **标准库**：不引入第三方依赖；
- **跨平台**：路径一律用 `pathlib`，不硬编码分隔符；
- **BOM 容错**：读 JSON / Markdown / TOML 一律 `encoding="utf-8-sig"`（兼容有/无 BOM）。
  Windows PowerShell 5.1 的 `Set-Content -Encoding UTF8` **默认写 BOM**，
  不容错会把合法文件误判为「不是合法 JSON / 非法 TOML」（见 `references/08-orca-cli.md` §5 坑 13）；
- **中文输出**：显式 UTF-8，避免 PowerShell 管道按 GBK 解码导致乱码。
