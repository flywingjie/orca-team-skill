# 工单 FIX-001：三关脚本静态夹具（正例）

> 仅供 `scripts/` 的机器自检使用，不是真实业务工单。

## 目标

为 `check_evidence.py` / `accept_patch.py` 提供可复核的最小输入。

## 验收

| # | 命令 | 期望结果 |
|---|---|---|
| 1 | `python -m pytest tests/test_fix.py -q` | 退出码 0，全部通过 |
| 2 | `python scripts/probe.py --bad` | 负路径探针，退出码 1 |

负路径覆盖：
- [x] 无效输入被拒绝
- [x] 拒绝信息可读
