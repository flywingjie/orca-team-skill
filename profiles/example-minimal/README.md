# 最小示例（无业务口径）

这是一个纯结构示例，用来验证 DAG、波次、模型分配、工单模板与一致性检查能跑通。**不含任何项目的业务规则。**

## 任务图

```text
SPEC-001 → [spec-gate] → CTR-001
                            ├─ API-014 ┐
                            └─ WEB-021 ┴→ INT-001 → [design-gate]
                                                       → VER-001 → ACC-001 → [delivery-gate]
```

## 单号即 task id

`orchestration.toml` 的 `id` **就是工单单号**，必须与 `tickets/` 下对应工单标题里的单号一致。
该不变量由 `scripts/check_consistency.py` 校验（工单 ↔ DAG 的四类比对：单号 / 归属文件集 / 依赖 / 基线）。

单号格式见 `references/03-ticket.md` §5：`<区域>-<序号>[-<短名>]`。

## 目录

```text
example-minimal/
├── orchestration.toml   # DAG：任务、依赖、文件所有权、决策门
├── agents.toml          # 角色 → agent / 模型映射
├── tickets/             # 与 DAG 一一对应的示例工单（五段式）
│   ├── SPEC-001.md
│   ├── CTR-001.md
│   ├── API-014.md
│   ├── WEB-021.md
│   ├── INT-001.md
│   ├── VER-001.md
│   └── ACC-001.md
└── roles/               # 角色卡
```

## 用脚本验证

```bash
python scripts/plan.py --dag profiles/example-minimal/orchestration.toml
python scripts/plan.py --dag profiles/example-minimal/orchestration.toml --json
python scripts/preflight.py --dag profiles/example-minimal/orchestration.toml \
  --agents profiles/example-minimal/agents.toml
python scripts/check_consistency.py \
  --tickets profiles/example-minimal/tickets \
  --dag profiles/example-minimal/orchestration.toml
```

期望：

- `plan.py` 输出 6 个波次：SPEC-001 → CTR-001 → API-014+WEB-021 → INT-001 → VER-001 → ACC-001。
  （`VER-001` 与 `ACC-001` 的依赖不同，因此不在同一波。）
- `preflight.py` 输出「任务 × 模型分配表」并确认同一波次内模型家族不重复。
- `check_consistency.py` **退出码 0**（示例工单与 DAG 完全一致）；
  故意改一处工单的归属文件后，应报 `mismatch` 并以退出码 1 结束。

## 说明

- `model` / `model_family` 是占位值，替换成你实际可用的模型。
- `files` 用于冲突检测；必须与工单「归属」段的独占文件一致。
- `role_card` 路径相对于本目录。
- 工单里的路径与验收命令指向**示例目标仓库**，不在 OTS 资产仓内实际存在；真实项目按本仓工具链替换。
