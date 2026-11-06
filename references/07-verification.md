# 07 证据、验收与回报

> 对应 `SPEC.md` §10–§11。核心原则：**“应该能跑”不算完成**。

## 1. 补丁包

worker 的交付物固定为补丁包：

```text
patches/<任务号>/<worker>/
├── manifest.json
├── change.patch
├── change-01.patch      # 可选：里程碑增量
├── change-02.patch
└── evidence/
    ├── test-output.txt
    ├── lint-output.txt
    └── ...
```

### 1.1 manifest.json

```json
{
  "ticket": "API-014",
  "worker": "backend-a",
  "model": "provider/model-id",
  "base_commit": "<sha>",
  "created_at": "2026-11-05T10:00:00Z",
  "files": [
    "server/api/orders.py",
    "server/tests/test_orders.py"
  ],
  "checks": [
    {
      "command": "python -m pytest server/tests/test_orders.py",
      "exit_code": 0,
      "evidence": "evidence/test-output.txt",
      "evidence_sha256": "<64位小写hex>"
    },
    {
      "command": "python scripts/check_evidence.py --ticket <坏 manifest> --patch-dir <dir>",
      "exit_code": 1,
      "expect_exit_code": 1,
      "evidence": "evidence/negative-probe.txt",
      "evidence_sha256": "<64位小写hex>"
    }
  ],
  "notes": "分页参数默认 20，最大 100"
}
```

要求：

- `files` 必须与补丁实际路径一致；
- 每条 `checks` 必须能在 `evidence/` 找到对应输出；
- `model` 只作参考，不可信（以派单分配表为准）；
- `base_commit` 用于判断补丁能否应用；
- **`evidence_sha256`**（`SPEC.md` `V-10`）：该 evidence 文件**字节**的 sha256，小写十六进制 64 位。
  缺失不判失败（记提示、要求补齐）；**不一致即判失败**——说明证据在交付后被改动过。
  生成方式：`python scripts/check_evidence.py --ticket <工单> --patch-dir <补丁包> --print-fingerprint`
  （只读打印，不回填 manifest）。
- **`expect_exit_code`**（`SPEC.md` `V-12`）：该命令的**期望**退出码，缺省 0。
  正确关比对「实际 `exit_code` == `expect_exit_code`」，不等即 `command-failed`（判失败）。
  **只用于负路径探针**（故意破坏输入 → 期望校验器以非零退出）——
  这类行证明的是「工具真的能检出问题」，其 `exit_code` 记真实值（如 1），同时声明 `expect_exit_code: 1`。
  正向验收命令**不得**声明非零期望。缺省为 0，既有 manifest 行为不变。

### 1.2 生成补丁

```bash
# 新增文件先登记，否则 diff 不含它
git add -N <new-files>

# 只导出所有权路径
git diff --binary -- <path-1> <path-2> > change.patch
```

要求：

- 只包含工单「归属」段点名的路径；
- 二进制安全（`--binary`）；
- 不包含 commit、不含 `.env`、不含凭据。

## 2. 补丁三关

### 2.1 范围关

```bash
git apply --check change.patch
git apply --stat change.patch
# 提取路径并比对所有权清单
```

检查：

- [ ] 补丁可应用（无冲突、无缺失上下文）
- [ ] 路径集合 ⊆ 工单「归属」清单
- [ ] 没有夹带无关文件、生成产物、凭据
- [ ] 新增文件已包含在补丁中
- [ ] manifest 的 `files` 与实际路径一致
- [ ] 每条 check 的证据在补丁包目录内且非空（防 `../` 与绝对路径逃出）

不通过：退回越界部分；无法拆分则整单退回。

### 2.2 正确关

工具：`scripts/check_evidence.py --ticket <工单> --patch-dir <补丁包>`

检查：

- [ ] 工单「验收」段每条命令都真实执行过
- [ ] 期望结果逐条核对
- [ ] 负路径覆盖符合工单要求
- [ ] 门禁命令（lint / typecheck / test / build）通过
- [ ] evidence 中有对应输出，且**位于补丁包目录内**（`evidence-outside-package`）
- [ ] **证据内容指纹与 manifest 记录一致**（`SPEC.md` `V-10`）
- [ ] 没有削弱测试、关闭检查、修改断言

不通过：出回修单，写明失败命令与期望差距；重试 ≤ 2。

**指纹与回显的判定口径**（工具已实现，人工复核时按同一口径看）：

| 项 | 判据 | 性质 |
|---|---|---|
| `evidence-fingerprint-ok` | 重算 sha256 == manifest 的 `evidence_sha256` | 通过 |
| `evidence-fingerprint-mismatch` | 两者不等 | **失败**：证据在交付后被改动 |
| `evidence-fingerprint-invalid` | 字段格式非法（非 64 位 hex / 非字符串） | **失败** |
| `evidence-no-fingerprint` | 字段缺失 | 提示（要求补齐，**不判失败**，兼容旧 manifest） |
| `evidence-command-echo` | evidence 文本含该命令 | 提示（正面信号） |
| `evidence-no-command-echo` | evidence 文本不含该命令 | 提示（**不判失败**：pytest 等工具输出本就不含命令行原文） |
| `command-failed` | 实际 `exit_code` != 期望退出码（`expect_exit_code`，缺省 0） | **失败** |

> 指纹只证明**完整性**，不证明**真实性**。真实性靠门禁实跑 + 人工复核。

### 2.3 集成关

工具：`scripts/check_integration.py --repo <集成分支 worktree> --patches <补丁根> --dag <orchestration.toml> --gate-cmd "..." --consumer-cmd "..." --evidence-out <证据目录>`

检查：

- [ ] 各补丁按依赖顺序合入集成分支（拓扑序；某个补丁失败则跳过其下游任务）
- [ ] 冲突已解决，且没有借机改契约/业务语义
- [ ] **同一路径不出现在两个及以上补丁的归属文件里**（`P-4` 的机械检查：合并不报错但语义已坏）
- [ ] 合入后的实际变更集（已跟踪变更 + 补丁新增、尚未 add 的未跟踪文件）
      ⊆ 各补丁归属文件 ∪ 集成单独占的共享文件
- [ ] 契约产物由集成单统一生成，且**只出现在一个**补丁里
- [ ] 消费方（前端/调用方）验证通过
- [ ] 合并后全量门禁通过
- [ ] 回归用例通过
- [ ] 产出 `integration-manifest.json`，其每条命令带 `evidence_sha256`

不通过：回退契约单或对应实现单；不得在集成阶段“现场发挥”。

**脚本安全边界**（`SPEC.md` `V-11`）：

- 只允许在被检工作区内执行 `git apply` 与只读查询（`git diff` / `git ls-files`）；
  **不得** commit / push / merge / `reset --hard` / clean / checkout；
- **不得**执行破坏性命令（`rm -rf`、`drop table`、`truncate`、`git push` 等），
  命中即**拒绝执行**并判失败；
  匹配用小写子串，**宁可误伤不可漏执行**——误伤只需换一条命令，漏执行是事故。
  （因此 `format`、`truncate` 这类词若出现在正常命令里会被拦下，改写命令即可，不要放宽黑名单。）
- 命令在 `--repo` 下执行，超时判失败（`cmd-timeout`）；
- **证据文件恒为非空**：命令无任何输出时，写入最小可复核内容
  （命令原文 + 退出码 + cwd），不得产出 0 字节文件——否则集成关的产物会被正确关判 `evidence-empty`；
- 产物只写 `--evidence-out`，不得写到别处。

## 3. 验收记录

```text
任务：<单号>
补丁：<路径>
范围关：通过 / 不通过（原因）
正确关：通过 / 不通过（失败命令 + 期望）
集成关：通过 / 不通过（冲突与处置）
门禁：<命令 + 结果>
结论：接受 / 回修（第 N 次）/ 升级
验收人：<标识>    时间：<ISO8601>
```

## 4. 回报格式

### 4.1 worker 结算三句

```text
做了什么：<一句话 + 补丁包路径>
凭什么算做完：<执行的命令 + 关键结果>
还有什么没做：<未覆盖项 / 已知限制 / 需要谁决定什么>
```

失败时必须显式：

```text
结果：失败
卡点：<具体错误 / 缺少的依赖 / 越界冲突>
已尝试：<命令与结果>
需要：<谁做什么决定 / 提供什么>
```

### 4.2 协调者交付六段

```text
1. 结果：做了什么，是否完成
2. 影响：业务/行为/数据/接口/权限的影响面
3. 变更范围：文件清单、迁移、契约
4. 验证证据：命令与输出、门禁结果
5. 未验证项：没跑的检查、无法验证的部分、剩余风险
6. 待确认事项：需要拍板人决定的问题
```

## 5. 交付后回退

已合入集成分支的改动发现问题（交付门之后）时，按三级处置。**任何一级都要走补丁三关**：

| 级 | 适用 | 动作 | 基线 | 授权 |
|---|---|---|---|---|
| ① **revert 单** | 单个工单的改动有问题 | 出回退单，逐文件提交 | `origin/main` 或当前集成分支 | 协调者决策 + R-1 知会 |
| ② **回退集成分支** | 一批改动整体需退 | 整批回退单，回归后合入 | 集成分支 | R-1 明确授权 |
| ③ **数据回滚** | 涉及资金或数据完整性 | 走补偿/冲销流程，**不改历史** | — | R-1 明确授权并留痕（`X-2`） |

禁止：

- `git reset --hard`、`git revert --no-commit` 后强推改写已推送历史（`X-2`、`X-3`）；
- 直接在集成分支上手改代码绕过工单；
- 以「回退」为名削弱测试或关闭检查（`V-6`）。

回退单同样要有 manifest / patch / evidence 与回报三句；**回退后必须重跑全量门禁**。
回退也属于「变更」，同样受 `SPEC.md` `L-4` 的独立验收判据约束。

## 6. 禁止事项

- 不得用“代码已生成”代替“测试通过”。
- 不得只跑部分命令却报告“全绿”。
- 不得修改测试断言、跳过用例、关闭 lint 来让门禁通过。
- 不得把失败写在散文里、结论却写“完成”。
- 不得在证据缺失的情况下接受补丁。
- 不得把 worker 自报的模型/结果当作已验证事实。

## 7. 最小验收清单

- [ ] 补丁可应用
- [ ] 路径不越界
- [ ] 验收命令逐条跑过
- [ ] 负路径有覆盖
- [ ] 门禁通过
- [ ] 证据可复核
- [ ] 合入后回归通过
- [ ] 契约产物由唯一 owner 生成
- [ ] 逐文件 commit
- [ ] 进度与风险已回写
