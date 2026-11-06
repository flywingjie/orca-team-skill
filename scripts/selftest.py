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

断言范围：
- plan/preflight/check_consistency 对示例的结构与一致性断言；
- 七个脚本 --help 均可运行（缺省文件跳过并标注）；
- L-1 负例：两个任务共用 worktree 的临时 DAG 必须被 plan.py 判退出码 2；
- 正确关/范围关夹具端到端（scripts/fixtures/：正例、指纹不一致、
  证据越出包、危险文件）；
- 集成关端到端（系统临时目录建 git 临时仓：正例、未跟踪新文件
  scope-violation、破坏性命令 cmd-unsafe 拒执行）。

不修改仓库文件；端到端夹具只写系统临时目录（结束即清理）；
`scripts/fixtures/` 是随仓分发的静态夹具。仅使用标准库（Python 3.11+）。

退出码：0 = 全部断言通过；1 = 有断言失败；2 = 参数错误或前置文件缺失。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DAG = REPO_ROOT / "profiles" / "example-minimal" / "orchestration.toml"
DEFAULT_AGENTS = REPO_ROOT / "profiles" / "example-minimal" / "agents.toml"
DEFAULT_TICKETS = REPO_ROOT / "profiles" / "example-minimal" / "tickets"
FIXTURES = REPO_ROOT / "scripts" / "fixtures"

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
PARALLEL_SCRIPTS = ["check_consistency.py", "check_evidence.py", "check_integration.py"]


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


# ---------------------------------------------------------------- 端到端断言


def plan_worktree_assertion() -> list[Assertion]:
    """⑤ L-1 负例：两个任务共用 worktree 的临时 DAG 必须被判结构错误（退出码 2）。"""
    with tempfile.TemporaryDirectory(prefix="ots-selftest-") as td:
        dag = Path(td) / "dup-worktree.toml"
        dag.write_text(
            '[[tasks]]\n'
            'id = "T-1"\n'
            'worktree = "wt-same"\n'
            'files = ["a.txt"]\n'
            'depends_on = []\n'
            '\n'
            '[[tasks]]\n'
            'id = "T-2"\n'
            'worktree = "wt-same"\n'
            'files = ["b.txt"]\n'
            'depends_on = ["T-1"]\n',
            encoding="utf-8",
            newline="\n",
        )
        r = run_script("plan.py", "--dag", str(dag))
    msg_ok = "worktree 跨任务复用" in r["err"]
    ok = r["exit"] == 2 and msg_ok
    return [
        Assertion(
            "plan.worktree-reuse",
            ok,
            f"exit={r['exit']}（期望 2）stderr 含'worktree 跨任务复用'={msg_ok}",
        )
    ]


def evidence_fixture_assertions() -> list[Assertion]:
    """⑥ 正确关夹具端到端：正例 / 指纹不一致 / 证据越出包。"""
    cases = [
        ("check_evidence.ok", "pkg-ok", 0, "正确关：通过"),
        ("check_evidence.fingerprint-mismatch", "pkg-mismatch", 1, "evidence-fingerprint-mismatch"),
        ("check_evidence.outside-package", "pkg-escape", 1, "evidence-outside-package"),
    ]
    out: list[Assertion] = []
    ticket = FIXTURES / "ticket-ok.md"
    for name, pkg, want_exit, needle in cases:
        if not ticket.exists() or not (FIXTURES / pkg / "manifest.json").exists():
            out.append(Assertion(name, None, f"夹具缺失（{pkg}），跳过"))
            continue
        r = run_script("check_evidence.py", "--ticket", str(ticket), "--patch-dir", str(FIXTURES / pkg))
        hit = needle in r["out"]
        out.append(
            Assertion(
                name,
                r["exit"] == want_exit and hit,
                f"exit={r['exit']}（期望 {want_exit}）含'{needle}'={hit}",
            )
        )
    return out


def accept_patch_fixture_assertions() -> list[Assertion]:
    """⑦ 范围关夹具端到端：危险文件 / 证据越出包（--no-apply-check，不依赖 git）。"""
    cases = [
        ("accept_patch.secret-file", "pkg-secret", "危险文件"),
        ("accept_patch.outside-package", "pkg-escape", "不在补丁包目录内"),
    ]
    out: list[Assertion] = []
    for name, pkg, needle in cases:
        manifest = FIXTURES / pkg / "manifest.json"
        if not manifest.exists():
            out.append(Assertion(name, None, f"夹具缺失（{pkg}），跳过"))
            continue
        r = run_script("accept_patch.py", "--manifest", str(manifest), "--no-apply-check")
        hit = needle in r["out"]
        out.append(
            Assertion(
                name,
                r["exit"] == 1 and hit,
                f"exit={r['exit']}（期望 1）含'{needle}'={hit}",
            )
        )
    return out


# 集成关端到端夹具：补丁正文与 DAG（手工小 diff，只在临时仓使用）
PATCH_MODIFY = (
    "diff --git a/base.txt b/base.txt\n"
    "--- a/base.txt\n"
    "+++ b/base.txt\n"
    "@@ -1 +1 @@\n"
    "-base\n"
    "+base changed\n"
)
PATCH_ADD = (
    "diff --git a/new.txt b/new.txt\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/new.txt\n"
    "@@ -0,0 +1 @@\n"
    "+hello\n"
)
PATCH_STRAY = (
    "diff --git a/base.txt b/base.txt\n"
    "--- a/base.txt\n"
    "+++ b/base.txt\n"
    "@@ -1 +1 @@\n"
    "-base\n"
    "+base stray\n"
    "diff --git a/stray.txt b/stray.txt\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/stray.txt\n"
    "@@ -0,0 +1 @@\n"
    "+stray\n"
)
DAG_OK = (
    "[orchestration]\n"
    'name = "selftest-integration"\n'
    "max_parallel = 4\n"
    "\n"
    "[[tasks]]\n"
    'id = "T-1"\n'
    'files = ["base.txt"]\n'
    "depends_on = []\n"
    "\n"
    "[[tasks]]\n"
    'id = "T-2"\n'
    'files = ["new.txt"]\n'
    'depends_on = ["T-1"]\n'
)
DAG_SINGLE = (
    "[orchestration]\n"
    'name = "selftest-integration-single"\n'
    "max_parallel = 4\n"
    "\n"
    "[[tasks]]\n"
    'id = "T-1"\n'
    'files = ["base.txt"]\n'
    "depends_on = []\n"
)


def _git(repo: Path, *args: str) -> None:
    """在临时仓执行 git；失败抛 RuntimeError（由上层转成断言失败）。"""
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=SUBPROCESS_TIMEOUT,
    )
    if p.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 失败：{(p.stdout or '') + (p.stderr or '')}".strip()
        )


def _make_integration_repo(td: Path) -> Path:
    """建临时 git 仓：单提交 base.txt（LF、不转换），供 apply 与 diff 基线。"""
    repo = td / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "selftest@example.com")
    _git(repo, "config", "user.name", "selftest")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "base.txt").write_bytes(b"base\n")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-qm", "base")
    return repo


def _write_patch(root: Path, task: str, patch_text: str, files: list[str]) -> None:
    pdir = root / task
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "change.patch").write_text(patch_text, encoding="utf-8", newline="\n")
    (pdir / "manifest.json").write_text(
        json.dumps(
            {
                "ticket": task,
                "worker": "selftest",
                "base_commit": "0" * 40,
                "files": files,
                "checks": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
        newline="\n",
    )


def _run_integration(
    td: Path,
    repo: Path,
    dag_text: str,
    patch_specs: list[tuple[str, str, list[str]]],
    gate_cmd: str,
    consumer_cmd: str,
    ev_name: str,
) -> tuple[dict, Path]:
    patches = td / f"patches-{ev_name}"
    patches.mkdir(parents=True, exist_ok=True)
    for task, text, files in patch_specs:
        _write_patch(patches, task, text, files)
    dag = td / f"dag-{ev_name}.toml"
    dag.write_text(dag_text, encoding="utf-8", newline="\n")
    ev = td / ev_name
    args = [
        "check_integration.py",
        "--repo", str(repo),
        "--patches", str(patches),
        "--dag", str(dag),
        "--evidence-out", str(ev),
        "--base", "HEAD",
    ]
    if gate_cmd:
        args += ["--gate-cmd", gate_cmd]
    if consumer_cmd:
        args += ["--consumer-cmd", consumer_cmd]
    return run_script(*args), ev


def integration_assertions() -> list[Assertion]:
    """⑧ 集成关端到端（系统临时目录建 git 仓）：正例 / 未跟踪夹带 / 破坏性命令。"""
    if shutil.which("git") is None:
        return [Assertion("check_integration.e2e", None, "git 不可用，跳过")]
    out: list[Assertion] = []

    # 正例：两个补丁按拓扑序合入，门禁与消费方命令实跑
    td = Path(tempfile.mkdtemp(prefix="ots-itg-ok-"))
    try:
        repo = _make_integration_repo(td)
        r, ev = _run_integration(
            td, repo, DAG_OK,
            [("T-1", PATCH_MODIFY, ["base.txt"]), ("T-2", PATCH_ADD, ["new.txt"])],
            "echo gate-ok", "echo consumer-ok", "ev-ok",
        )
        manifest_ok = (ev / "integration-manifest.json").is_file()
        gate_ok = (ev / "gate-01.txt").is_file()
        consumer_ok = (ev / "consumer-01.txt").is_file()
        passed = r["exit"] == 0 and manifest_ok and gate_ok and consumer_ok
        out.append(
            Assertion(
                "check_integration.ok",
                passed,
                f"exit={r['exit']}（期望 0）manifest={manifest_ok} gate证据={gate_ok} consumer证据={consumer_ok}",
            )
        )
    except Exception as e:  # noqa: BLE001
        out.append(Assertion("check_integration.ok", False, f"夹具异常：{type(e).__name__}: {e}"))
    finally:
        shutil.rmtree(td, ignore_errors=True)

    # 未跟踪盲区回归：补丁夹带 manifest 未声明的新文件 → 必须 scope-violation
    td = Path(tempfile.mkdtemp(prefix="ots-itg-stray-"))
    try:
        repo = _make_integration_repo(td)
        r, _ev = _run_integration(
            td, repo, DAG_SINGLE,
            [("T-1", PATCH_STRAY, ["base.txt"])],
            "", "", "ev-stray",
        )
        hit_kind = "scope-violation" in r["out"]
        hit_path = "stray.txt" in r["out"]
        out.append(
            Assertion(
                "check_integration.untracked-scope",
                r["exit"] == 1 and hit_kind and hit_path,
                f"exit={r['exit']}（期望 1）含'scope-violation'={hit_kind} 含'stray.txt'={hit_path}",
            )
        )
    except Exception as e:  # noqa: BLE001
        out.append(Assertion("check_integration.untracked-scope", False, f"夹具异常：{type(e).__name__}: {e}"))
    finally:
        shutil.rmtree(td, ignore_errors=True)

    # 破坏性命令：cmd-unsafe 拒执行（不产出证据文件）并判失败
    td = Path(tempfile.mkdtemp(prefix="ots-itg-unsafe-"))
    try:
        repo = _make_integration_repo(td)
        r, ev = _run_integration(
            td, repo, DAG_SINGLE,
            [("T-1", PATCH_MODIFY, ["base.txt"])],
            "git push origin main", "", "ev-unsafe",
        )
        hit_kind = "cmd-unsafe" in r["out"]
        no_evidence = not (ev / "gate-01.txt").exists()
        out.append(
            Assertion(
                "check_integration.cmd-unsafe",
                r["exit"] == 1 and hit_kind and no_evidence,
                f"exit={r['exit']}（期望 1）含'cmd-unsafe'={hit_kind} 未执行(无gate证据)={no_evidence}",
            )
        )
    except Exception as e:  # noqa: BLE001
        out.append(Assertion("check_integration.cmd-unsafe", False, f"夹具异常：{type(e).__name__}: {e}"))
    finally:
        shutil.rmtree(td, ignore_errors=True)

    return out


def run_all(dag: Path, agents: Path) -> list[Assertion]:
    assertions: list[Assertion] = []
    assertions.extend(plan_assertions(dag))
    assertions.extend(preflight_assertions(dag, agents))
    assertions.extend(consistency_assertion(dag))
    assertions.extend(help_assertions())
    assertions.extend(parallel_help_assertions())
    assertions.extend(plan_worktree_assertion())
    assertions.extend(evidence_fixture_assertions())
    assertions.extend(accept_patch_fixture_assertions())
    assertions.extend(integration_assertions())
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
