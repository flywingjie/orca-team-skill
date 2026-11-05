#!/usr/bin/env python3
"""本仓自身自检：把示例期望值变成可执行断言。

用法：
    python scripts/selftest.py
    python scripts/selftest.py --dag <path> --agents <path>
    python scripts/selftest.py --json

断言依据：`tickets/OTC-003-selftest.md` 的「实测基线」与协调者 2026-11-05 回修——
plan.py 对 example-minimal 输出 6 个波次（SPEC-001 -> CTR-001 ->
API-014+WEB-021 -> INT-001 -> VER-001 -> ACC-001），决策门 3 个；
preflight.py 输出「任务 × 模型分配表」并以「预检通过」结尾；
check_consistency.py 对 tickets/ 与 DAG 输出「结论：一致」。

只读：本脚本不创建、修改、删除任何仓库文件，只以子进程调用既有脚本。
仅使用标准库（Python 3.11+，tomllib 不涉及）。

退出码：0 = 全部断言通过；1 = 有断言失败；2 = 参数错误或前置文件缺失。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DAG = REPO_ROOT / "profiles" / "example-minimal" / "orchestration.toml"
DEFAULT_AGENTS = REPO_ROOT / "profiles" / "example-minimal" / "agents.toml"
DEFAULT_TICKETS = REPO_ROOT / "profiles" / "example-minimal" / "tickets"

SUBPROCESS_TIMEOUT = 120  # 秒；超时判失败

# 期望值（实测基线，见工单 OTC-003；协调者 2026-11-05 回修：task id 改为工单单号）
EXPECTED_TASKS = [
    "SPEC-001", "CTR-001", "API-014", "WEB-021",
    "INT-001", "VER-001", "ACC-001",
]
EXPECTED_GATES = ["spec-gate", "design-gate", "delivery-gate"]
EXPECTED_WAVE_COUNT = 6
WAVE3_TASKS = ["API-014", "WEB-021"]
VERIFY_TASK = "VER-001"
ACCEPT_TASK = "ACC-001"
VERIFY_WAVE = 5
ACCEPT_WAVE = 6

# 波头：=== 第 N 波（K 个任务）===
WAVE_RE = re.compile(r"^=== 第 (\d+) 波")
# 任务行：  <id>              role=...（行首 2 空格 + id + 空格 + role=）
TASK_LINE_RE = re.compile(r"^\s{2}(\S+)\s+role=")
# 门行：  <gate-id>            after=<task> ...
GATE_RE = re.compile(r"^  (\S+)\s+after=")

# 并行单可能尚未产出，文件不存在则跳过（不判失败）
PARALLEL_SCRIPTS = ["check_consistency.py", "check_evidence.py"]


@dataclass
class Assertion:
    name: str
    ok: bool | None  # True=通过 False=失败 None=跳过
    detail: str = ""


def die(msg: str) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(2)


def run_script(script: str, *args: str) -> dict:
    """以子进程运行仓库 scripts/<script>，UTF-8 解码，带超时。"""
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / script), *args]
    try:
        p = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SUBPROCESS_TIMEOUT,
        )
        return {
            "exit": p.returncode,
            "out": p.stdout or "",
            "err": p.stderr or "",
            "timeout": False,
            "exc": None,
        }
    except subprocess.TimeoutExpired:
        return {"exit": None, "out": "", "err": "", "timeout": True, "exc": "timeout"}
    except Exception as e:  # noqa: BLE001
        return {"exit": None, "out": "", "err": "", "timeout": False, "exc": f"{type(e).__name__}: {e}"}


def parse_plan(stdout: str) -> tuple[list[tuple[int, list[str]]], list[str]]:
    """从 plan.py 的 stdout 解析出 (波号, 任务 id 列表) 序列与决策门 id 列表。"""
    waves: list[tuple[int, list[str]]] = []
    current_no: int | None = None
    current_ids: list[str] = []
    for line in stdout.splitlines():
        m = WAVE_RE.match(line)
        if m:
            if current_no is not None:
                waves.append((current_no, current_ids))
            current_no = int(m.group(1))
            current_ids = []
            continue
        if current_no is not None:
            tm = TASK_LINE_RE.match(line)
            if tm:
                current_ids.append(tm.group(1))
    if current_no is not None:
        waves.append((current_no, current_ids))

    gates: list[str] = []
    for line in stdout.splitlines():
        gm = GATE_RE.match(line)
        if gm:
            gates.append(gm.group(1))
    return waves, gates


def plan_assertions(dag: Path) -> list[Assertion]:
    """① plan.py 冒烟与结构断言。"""
    out: list[Assertion] = []
    r = run_script("plan.py", "--dag", str(dag))
    out.append(Assertion("plan.exit_code", r["exit"] == 0, f"exit={r['exit']}"))
    if r["timeout"]:
        out.append(Assertion("plan.timeout", False, f"超时 > {SUBPROCESS_TIMEOUT}s"))
        return out
    if r["exc"]:
        out.append(Assertion("plan.run", False, r["exc"]))
        return out

    waves, gates = parse_plan(r["out"])
    wave_nos = [n for n, _ in waves]
    task_ids: set[str] = set()
    for _, ids in waves:
        task_ids.update(ids)
    wave3 = [ids for n, ids in waves if n == 3]
    wave3_ids: set[str] = set()
    if wave3:
        wave3_ids = set(wave3[0])
    verify_wave = next((n for n, ids in waves if VERIFY_TASK in ids), None)
    accept_wave = next((n for n, ids in waves if ACCEPT_TASK in ids), None)

    out.append(
        Assertion(
            "plan.wave_count",
            len(waves) == EXPECTED_WAVE_COUNT and wave_nos == list(range(1, EXPECTED_WAVE_COUNT + 1)),
            f"波次数={len(waves)} 序号={wave_nos}（期望 {EXPECTED_WAVE_COUNT} 且 1..N 连续）",
        )
    )
    missing_tasks = [t for t in EXPECTED_TASKS if t not in task_ids]
    out.append(
        Assertion(
            "plan.task_ids",
            not missing_tasks,
            f"解析任务={sorted(task_ids)} 缺失={missing_tasks or '无'}",
        )
    )
    missing_gates = [g for g in EXPECTED_GATES if g not in gates]
    out.append(
        Assertion(
            "plan.gate_ids",
            not missing_gates,
            f"解析门={gates} 缺失={missing_gates or '无'}",
        )
    )
    out.append(
        Assertion(
            "plan.wave3_parallel",
            all(t in wave3_ids for t in WAVE3_TASKS),
            f"第 3 波任务={sorted(wave3_ids)}（期望同时含 {WAVE3_TASKS}）",
        )
    )
    out.append(
        Assertion(
            "plan.verify_accept_waves",
            verify_wave == VERIFY_WAVE and accept_wave == ACCEPT_WAVE and verify_wave != accept_wave,
            f"{VERIFY_TASK} 波={verify_wave} {ACCEPT_TASK} 波={accept_wave}"
            f"（期望 {VERIFY_WAVE} / {ACCEPT_WAVE}，不同波）",
        )
    )
    return out


def preflight_assertions(dag: Path, agents: Path) -> list[Assertion]:
    """② preflight.py 冒烟断言。"""
    out: list[Assertion] = []
    r = run_script("preflight.py", "--dag", str(dag), "--agents", str(agents))
    out.append(Assertion("preflight.exit_code", r["exit"] == 0, f"exit={r['exit']}"))
    if r["timeout"]:
        out.append(Assertion("preflight.timeout", False, f"超时 > {SUBPROCESS_TIMEOUT}s"))
        return out
    if r["exc"]:
        out.append(Assertion("preflight.run", False, r["exc"]))
        return out
    has_title = "任务 × 模型分配表" in r["out"]
    has_pass = "预检通过" in r["out"]
    out.append(
        Assertion(
            "preflight.output",
            has_title and has_pass,
            f"含'任务 × 模型分配表'={has_title} 含'预检通过'={has_pass}",
        )
    )
    return out


def consistency_assertion(dag: Path) -> list[Assertion]:
    """⑤ 工单 ↔ DAG 一致性：check_consistency.py 必须退出码 0（一致）。

    失败时把 stdout 尾部若干行放进 detail，便于定位被检出项。
    """
    r = run_script(
        "check_consistency.py",
        "--tickets", str(DEFAULT_TICKETS),
        "--dag", str(dag),
    )
    if r["timeout"]:
        return [
            Assertion(
                "consistency.exit_code",
                False,
                f"超时 > {SUBPROCESS_TIMEOUT}s",
            )
        ]
    if r["exc"]:
        return [Assertion("consistency.exit_code", False, r["exc"])]
    tail = "\n".join((r["out"] or "").strip().splitlines()[-5:]) or "(stdout 为空)"
    return [
        Assertion(
            "consistency.exit_code",
            r["exit"] == 0,
            f"exit={r['exit']}；{tail}",
        )
    ]


def help_assertions() -> list[Assertion]:
    """③ 三个既有脚本 --help 可运行。"""
    out: list[Assertion] = []
    for script in ["plan.py", "preflight.py", "accept_patch.py"]:
        r = run_script(script, "--help")
        ok = r["exit"] == 0 and not r["timeout"] and not r["exc"]
        detail = f"exit={r['exit']}" if not r["exc"] else r["exc"]
        out.append(Assertion(f"{script}.help", ok, detail))
    return out


def parallel_help_assertions() -> list[Assertion]:
    """④ 并行单新增脚本 --help 可运行；文件不存在则跳过并标注。"""
    out: list[Assertion] = []
    for script in PARALLEL_SCRIPTS:
        path = REPO_ROOT / "scripts" / script
        if not path.exists():
            out.append(
                Assertion(
                    f"{script}.help",
                    None,
                    "文件不存在（OTC-001/002 并行单未产出），跳过",
                )
            )
            continue
        r = run_script(script, "--help")
        ok = r["exit"] == 0 and not r["timeout"] and not r["exc"]
        detail = f"exit={r['exit']}" if not r["exc"] else r["exc"]
        out.append(Assertion(f"{script}.help", ok, detail))
    return out


def run_all(dag: Path, agents: Path) -> list[Assertion]:
    assertions: list[Assertion] = []
    assertions.extend(plan_assertions(dag))
    assertions.extend(preflight_assertions(dag, agents))
    assertions.extend(consistency_assertion(dag))
    assertions.extend(help_assertions())
    assertions.extend(parallel_help_assertions())
    return assertions


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="本仓自检：把示例期望值变成可执行断言（只读）"
    )
    ap.add_argument("--dag", default=str(DEFAULT_DAG), help="orchestration.toml 路径")
    ap.add_argument("--agents", default=str(DEFAULT_AGENTS), help="agents.toml 路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    dag = Path(args.dag)
    agents = Path(args.agents)
    missing = [str(p) for p in (dag, agents) if not p.exists()]
    if missing:
        die(f"前置文件缺失：{', '.join(missing)}")

    assertions = run_all(dag, agents)
    passed = sum(1 for a in assertions if a.ok is True)
    failed = sum(1 for a in assertions if a.ok is False)
    skipped = sum(1 for a in assertions if a.ok is None)
    ok = failed == 0

    if args.json:
        payload = {
            "ok": ok,
            "assertions": [
                {"name": a.name, "ok": a.ok, "detail": a.detail} for a in assertions
            ],
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for a in assertions:
            if a.ok is True:
                mark = "通过"
            elif a.ok is False:
                mark = "失败"
            else:
                mark = "跳过"
            print(f"[{mark}] {a.name}: {a.detail}")
        print(
            f"\n共 {len(assertions)} 项：通过 {passed}，失败 {failed}，跳过 {skipped}"
        )
        print("全部通过。" if ok else "存在失败项。")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
