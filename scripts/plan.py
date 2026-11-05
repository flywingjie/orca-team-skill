#!/usr/bin/env python3
"""读取 DAG TOML，计算波次并检查文件/模型冲突。

用法：
    python scripts/plan.py --dag profiles/example-minimal/orchestration.toml
    python scripts/plan.py --dag path/to/orchestration.toml --json
    python scripts/plan.py --dag path/to/orchestration.toml --max-parallel 4

规则（对应 SPEC.md P-4、P-5）：
    - 并行度 <= max_parallel
    - 同一波次内 files 不得有交集
    - 同一波次内 model_family 不得重复
    - 依赖必须存在；不得有环

只读，不修改任何文件。需要 Python 3.11+（tomllib）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


def die(msg: str) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(2)


def load_toml(path: Path) -> dict:
    if tomllib is None:
        die("需要 Python 3.11+（tomllib）。")
    if not path.exists():
        die(f"文件不存在：{path}")
    with path.open("rb") as f:
        return tomllib.load(f)


def build_tasks(data: dict) -> tuple[list[dict], dict[str, dict], dict[str, set[str]]]:
    tasks = data.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        die("DAG 中没有 [[tasks]]。")
    by_id: dict[str, dict] = {}
    deps: dict[str, set[str]] = {}
    for t in tasks:
        tid = t.get("id")
        if not tid:
            die("存在没有 id 的任务。")
        if tid in by_id:
            die(f"任务 id 重复：{tid}")
        by_id[tid] = t
        deps[tid] = set(t.get("depends_on", []))
    for tid, ds in deps.items():
        missing = ds - set(by_id)
        if missing:
            die(f"任务 {tid} 依赖不存在：{', '.join(sorted(missing))}")
        if tid in ds:
            die(f"任务 {tid} 依赖自身。")
    return tasks, by_id, deps


def topo_order(by_id: dict[str, dict], deps: dict[str, set[str]]) -> list[str]:
    indeg = {tid: len(ds) for tid, ds in deps.items()}
    children: dict[str, list[str]] = {tid: [] for tid in by_id}
    for tid, ds in deps.items():
        for d in ds:
            children[d].append(tid)
    queue = sorted([tid for tid, n in indeg.items() if n == 0])
    order: list[str] = []
    while queue:
        tid = queue.pop(0)
        order.append(tid)
        for c in sorted(children[tid]):
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if len(order) != len(by_id):
        cycle = sorted(set(by_id) - set(order))
        die(f"依赖存在环，涉及：{', '.join(cycle)}")
    return order


def family_of(task: dict) -> str:
    return str(task.get("model_family") or task.get("model") or "")


def files_of(task: dict) -> set[str]:
    raw = task.get("files", [])
    if isinstance(raw, str):
        raw = [raw]
    return {str(x) for x in raw}


def compute_waves(data: dict, max_parallel: int | None = None) -> dict:
    tasks, by_id, deps = build_tasks(data)
    order = topo_order(by_id, deps)
    rank = {tid: i for i, tid in enumerate(order)}

    limit = max_parallel
    if limit is None:
        limit = int(data.get("orchestration", {}).get("max_parallel", 8))
    limit = max(1, limit)

    remaining = set(by_id)
    done: set[str] = set()
    waves: list[list[dict]] = []
    warnings: list[str] = []

    while remaining:
        ready = [
            by_id[tid] for tid in sorted(remaining, key=lambda x: rank[x])
            if deps[tid] <= done
        ]
        if not ready:
            die("无法推进：剩余任务的依赖未结算（可能存在环或缺失依赖）。")

        wave: list[dict] = []
        used_files: set[str] = set()
        used_families: set[str] = set()
        deferred: list[str] = []

        for t in ready:
            tid = t["id"]
            f = files_of(t)
            fam = family_of(t)
            if len(wave) >= limit:
                deferred.append(tid)
                continue
            if f & used_files:
                deferred.append(tid)
                continue
            if fam and fam in used_families:
                deferred.append(tid)
                continue
            wave.append(t)
            used_files |= f
            if fam:
                used_families.add(fam)

        if not wave:
            # 理论上不会发生；兜底取第一个，避免死循环
            wave = [ready[0]]
            warnings.append(f"波次无法满足约束，强制放入 {ready[0]['id']}")

        for t in wave:
            remaining.discard(t["id"])
            done.add(t["id"])
        waves.append(wave)

        if deferred:
            warnings.append(
                "本波延后：" + ", ".join(deferred) + "（文件/模型/并行度约束）"
            )

    gates = data.get("gates", [])
    return {
        "name": data.get("orchestration", {}).get("name", ""),
        "max_parallel": limit,
        "waves": [
            [
                {
                    "id": t["id"],
                    "role": t.get("role", ""),
                    "agent": t.get("agent", ""),
                    "model": t.get("model", ""),
                    "model_family": family_of(t),
                    "worktree": t.get("worktree", ""),
                    "files": sorted(files_of(t)),
                    "depends_on": sorted(deps[t["id"]]),
                    "base_branch": t.get("base_branch", "origin/main"),
                    "run_after_gate": t.get("run_after_gate", ""),
                }
                for t in wave
            ]
            for wave in waves
        ],
        "gates": gates,
        "warnings": warnings,
    }


def print_plan(result: dict) -> None:
    print(f"DAG：{result['name'] or '(未命名)'}    并行度上限：{result['max_parallel']}")
    for i, wave in enumerate(result["waves"], 1):
        print(f"\n=== 第 {i} 波（{len(wave)} 个任务）===")
        for t in wave:
            deps = ", ".join(t["depends_on"]) or "-"
            gate = f"  [门:{t['run_after_gate']}]" if t["run_after_gate"] else ""
            print(
                f"  {t['id']:<20} role={t['role']:<12} agent={t['agent']:<10} "
                f"model={t['model'] or '-':<28} family={t['model_family'] or '-':<10} "
                f"deps=[{deps}]{gate}"
            )
            if t["files"]:
                print(f"    files: {', '.join(t['files'])}")
    if result["gates"]:
        print("\n决策门：")
        for g in result["gates"]:
            print(
                f"  {g.get('id', '?'):<20} after={g.get('after', '?'):<20} "
                f"escalate_to={g.get('escalate_to', '-')}  {g.get('note', '')}"
            )
    if result["warnings"]:
        print("\n提示：")
        for w in result["warnings"]:
            print(f"  - {w}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="DAG 波次计算（只读）")
    ap.add_argument("--dag", required=True, help="orchestration.toml 路径")
    ap.add_argument("--max-parallel", type=int, default=None, help="覆盖 DAG 中的并行度上限")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    data = load_toml(Path(args.dag))
    result = compute_waves(data, args.max_parallel)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_plan(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
