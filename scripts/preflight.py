#!/usr/bin/env python3
"""派单前预检：产出「任务 × 模型」分配表，检查模型互斥与文件冲突。

用法：
    python scripts/preflight.py --dag profiles/example-minimal/orchestration.toml \
        --agents profiles/example-minimal/agents.toml
    python scripts/preflight.py ... --wave 1
    python scripts/preflight.py ... --auth-cmd "pi auth check" --json

检查项（对应 SPEC.md M-1、M-2、M-5、P-4）：
    - 每个任务能解析出 agent / model / model_family
    - 同一时刻（同一波次）内 model_family 不重复
    - 同一波次内 files 不重叠
    - 可选：执行鉴权命令并报告结果（鉴权通过 != 配额充足）

只读，不修改任何文件。需要 Python 3.11+。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import compute_waves, load_toml  # noqa: E402


def die(msg: str) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(2)


def build_agent_map(agents_data: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for a in agents_data.get("agents", []):
        name = a.get("name")
        if name:
            result[name] = a
    return result


def resolve(task: dict, agents: dict[str, dict]) -> dict:
    # agents.toml 以“角色名”为键；任务可写 role，也可直接写 agent 工具名。
    lookup_key = task.get("role") or task.get("agent") or ""
    agent_cfg = agents.get(lookup_key) or agents.get(task.get("agent", ""), {})
    agent_tool = task.get("agent") or agent_cfg.get("agent", "")
    model = task.get("model") or agent_cfg.get("model") or ""
    family = task.get("model_family") or agent_cfg.get("model_family") or model
    return {
        "id": task["id"],
        "role": task.get("role", ""),
        "agent": agent_tool,
        "agent_lookup": lookup_key,
        "model": model,
        "model_family": family,
        "worktree": task.get("worktree", ""),
        "files": sorted(set(task.get("files", []) or [])),
        "depends_on": sorted(set(task.get("depends_on", []) or [])),
        "base_branch": task.get("base_branch", "origin/main"),
    }


def run_auth(cmd: str, timeout: int = 60) -> dict:
    try:
        p = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        out = (p.stdout or "") + (p.stderr or "")
        tail = "\n".join(out.strip().splitlines()[-5:])
        return {"command": cmd, "exit_code": p.returncode, "tail": tail}
    except Exception as e:  # noqa: BLE001
        return {"command": cmd, "exit_code": None, "tail": f"{type(e).__name__}: {e}"}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        # die() 的中文错误走 stderr；不重配会在管道里按 GBK 编码（坑 2）
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="派单前模型/文件互斥预检（只读）")
    ap.add_argument("--dag", required=True)
    ap.add_argument("--agents", required=True)
    ap.add_argument("--wave", type=int, default=None, help="只检查第 N 波（1 基）；缺省检查全部")
    ap.add_argument("--auth-cmd", action="append", default=[], help="鉴权检查命令，可重复")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    dag = load_toml(Path(args.dag))
    agents_data = load_toml(Path(args.agents))
    agent_map = build_agent_map(agents_data)

    planned = compute_waves(dag)
    if args.wave is not None:
        if args.wave < 1 or args.wave > len(planned["waves"]):
            die(f"--wave 越界：共 {len(planned['waves'])} 波")
        selected_waves = [planned["waves"][args.wave - 1]]
    else:
        selected_waves = planned["waves"]

    errors: list[str] = []
    warnings: list[str] = []
    rows: list[dict] = []

    for wi, wave in enumerate(selected_waves, 1):
        seen_family: dict[str, str] = {}
        seen_files: dict[str, str] = {}
        for t in wave:
            row = resolve(t, agent_map)
            row["wave"] = wi if args.wave is None else args.wave
            rows.append(row)

            if not row["agent"]:
                errors.append(f"{row['id']}：没有 agent/role")
            if not row["model"]:
                errors.append(f"{row['id']}：没有解析出 model")
            if row["agent_lookup"] and row["agent_lookup"] not in agent_map:
                warnings.append(
                    f"{row['id']}：角色 '{row['agent_lookup']}' 不在 agents.toml"
                )

            fam = row["model_family"]
            if fam:
                if fam in seen_family:
                    errors.append(
                        f"模型家族冲突（第 {row['wave']} 波）：{row['id']} 与 "
                        f"{seen_family[fam]} 都使用 {fam}"
                    )
                else:
                    seen_family[fam] = row["id"]

            for f in row["files"]:
                if f in seen_files:
                    errors.append(
                        f"文件冲突（第 {row['wave']} 波）：{f} 被 {row['id']} 与 "
                        f"{seen_files[f]} 同时占用"
                    )
                else:
                    seen_files[f] = row["id"]

    auth_results = [run_auth(c) for c in args.auth_cmd]
    for r in auth_results:
        if r["exit_code"] not in (0,):
            errors.append(f"鉴权命令失败：{r['command']}（exit={r['exit_code']}）")

    result = {
        "waves_checked": [wi for wi in range(1, len(selected_waves) + 1)],
        "rows": rows,
        "errors": errors,
        "warnings": warnings,
        "auth": auth_results,
        "reminder": "鉴权通过不等于配额充足：派单后 1 分钟内确认 worker 真正开工；出现 429 立即降并行或换模型。",
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("任务 × 模型分配表")
        print("=" * 100)
        for r in rows:
            print(
                f"波{r['wave']}  {r['id']:<20} role={r['role']:<12} agent={r['agent']:<10} "
                f"model={r['model'] or '-':<28} family={r['model_family'] or '-':<10} "
                f"base={r['base_branch']}"
            )
        if auth_results:
            print("\n鉴权检查：")
            for r in auth_results:
                print(f"  [{r['exit_code']}] {r['command']}")
                if r["tail"]:
                    print("    " + r["tail"].replace("\n", "\n    "))
        if warnings:
            print("\n提示：")
            for w in warnings:
                print(f"  - {w}")
        if errors:
            print("\n错误：")
            for e in errors:
                print(f"  - {e}")
        else:
            print("\n预检通过。")
        print(f"\n{result['reminder']}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
