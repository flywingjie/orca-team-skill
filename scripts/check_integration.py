#!/usr/bin/env python3
"""补丁三关之集成关：按 DAG 拓扑序合入补丁、校验跨补丁所有权与变更范围、
真实执行门禁与消费方命令、产出 integration-manifest.json。

用法：
    python scripts/check_integration.py \\
        --repo <集成分支的干净 worktree> \\
        --patches <补丁包根目录> \\
        --dag <orchestration.toml> \\
        --gate-cmd "<全量门禁命令>" \\
        --consumer-cmd "<消费方验证命令>" \\
        --evidence-out <证据目录> \\
        [--base <基线分支，默认 origin/main>] \\
        [--allow <集成单独占的共享文件/契约产物，可重复>] \\
        [--contract-product <契约产物路径，可重复>] \\
        [--timeout <秒，默认 120>] \\
        [--dry-run] [--json]

执行流程（严格按 SPEC.md V-11，10 步）：
    1. 读 DAG（utf-8-sig + tomllib），按 depends_on 拓扑排序；存在环 → 退出码 2
    2. 定位补丁包：<patches>/<task_id>/ 或 <patches>/<task_id>/<worker>/；
       缺失 → patch-missing（判失败）
    3. 逐个补丁（拓扑序）：git apply --check / apply；
       失败 → apply-check-failed / apply-failed（判失败），不 apply，
       跳过所有依赖该任务的后续任务（skipped-dependency，提示）
    4. 跨补丁文件所有权：同一路径出现在 >1 个补丁 files → file-multi-owner（判失败）
    5. 契约产物唯一 owner：--contract-product 路径出现在 >1 个补丁 files
       → contract-product-multi-owner（判失败）
    6. 变更范围：git diff --name-only <base> ⊆（补丁 files 并集 ∪ --allow）；
       越界 → scope-violation（判失败）
    7. 门禁命令逐条真实执行（<evidence-out>/gate-NN.txt）；退出码 ≠ 0 → gate-failed（判失败）
    8. 消费方命令逐条真实执行（<evidence-out>/consumer-NN.txt）；退出码 ≠ 0 → consumer-failed（判失败）
    9. 生成 <evidence-out>/integration-manifest.json（含 evidence_sha256，与 V-10 同机制）
    10. 输出（文本 / --json）：ok / repo / patches / dag / applied / changed_paths / issues[] / summary

安全硬约束（违反即失败）：
    - 只允许在 --repo 内执行 git apply 与 git diff；禁止 commit/push/merge/
      reset --hard/clean/checkout/stash/worktree
    - 拒绝执行含破坏性片段的命令（cmd-unsafe，判失败，且不得执行）：
      rm -rf、del /f、drop table、drop database、truncate、mkfs、format、
      git reset --hard、git push、git clean、git checkout（小写子串匹配）
    - 命令在 <repo> 下执行，超时 --timeout（默认 120 秒）→ cmd-timeout（判失败）
    - 不得在 --repo 与 --evidence-out 之外创建或修改任何文件
    - --repo 必须存在且是 git 仓库；否则退出码 2

只用于检查，不负责回滚：apply 改动留在 --repo（集成结果），--json 给出 applied 列表。
仅标准库，Python 3.11+（tomllib）。退出码：0 集成关通过；1 有不一致；2 参数或环境错误。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

DEFAULT_BASE = "origin/main"
DEFAULT_TIMEOUT = 120

# 破坏性命令片段（小写子串匹配，命中即拒绝执行且不得执行）
UNSAFE_FRAGMENTS = (
    "rm -rf",
    "del /f",
    "drop table",
    "drop database",
    "truncate",
    "mkfs",
    "format",
    "git reset --hard",
    "git push",
    "git clean",
    "git checkout",
)


def die(msg: str) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(2)


def finding(kind: str, detail: str, fatal: bool = True, command: str = "", task: str = "") -> dict:
    return {"kind": kind, "detail": detail, "fatal": bool(fatal), "command": command, "task": task}


def norm_path(raw: str) -> str:
    """路径归一：统一分隔符为 /，去掉前导 ./，忽略前后空白。"""
    p = raw.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def is_git_repo(path: Path) -> bool:
    """用文件系统判断是否为 git 工作区（.git 目录或 worktree 指针文件）。

    说明：安全边界限定脚本在 --repo 内只执行 git apply 与 git diff，
    因此不用 git rev-parse 探测仓库，改用 .git 存在性判断（worktree/子模块
    的 .git 是文件，.exists() 同样为真）。
    """
    return (path / ".git").exists()


def sha256_file(path: Path) -> str:
    """证据文件字节的 sha256（小写十六进制 64 位），与 SPEC.md V-10 同机制。"""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()


def is_unsafe(command: str) -> bool:
    """破坏性命令片段匹配（小写子串）。命中即拒绝执行。"""
    low = command.lower()
    return any(frag in low for frag in UNSAFE_FRAGMENTS)


# ---------------------------------------------------------------- DAG


def load_dag(path: Path) -> list[dict]:
    """读取 DAG；文件级错误以退出码 2 终止。utf-8-sig 兼容带 BOM 的 TOML。"""
    if tomllib is None:  # pragma: no cover
        die("需要 Python 3.11+（tomllib）。")
    try:
        # tomllib.loads 能处理 utf-8-sig 读出的无 BOM 文本
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        die(f"DAG 不是合法 TOML：{exc}")
    except UnicodeDecodeError as exc:
        die(f"DAG 不是合法 UTF-8 文本：{exc}")
    except OSError as exc:
        die(f"DAG 读取失败：{exc}")
    raw_tasks = data.get("tasks", [])
    if not isinstance(raw_tasks, list):
        die("DAG 的 tasks 不是数组。")
    tasks: list[dict] = []
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            die("DAG 的 [[tasks]] 含有非表项。")
        tid = raw.get("id")
        if not isinstance(tid, str) or not tid.strip():
            die("DAG 的 [[tasks]] 缺少 id。")
        deps = raw.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        if not isinstance(deps, list):
            die(f"DAG 任务 {tid} 的 depends_on 不是字符串或数组。")
        dep_list = [str(d).strip() for d in deps if str(d).strip()]
        tasks.append({"id": tid.strip(), "deps": dep_list})
    return tasks


def topo_sort(tasks: list[dict]) -> tuple[list[str], list[str], dict[str, list[str]]]:
    """Kahn 拓扑排序。返回 (应用顺序, 环中任务, 直接后继映射)。

    只统计 DAG 内已知依赖；未知依赖视为外部已满足（不阻塞调度）。
    环中的任务即「永远无法入队」的剩余集合。
    """
    ids = [t["id"] for t in tasks]
    id_set = set(ids)
    indeg: dict[str, int] = {}
    dependents: dict[str, list[str]] = {i: [] for i in ids}
    for t in tasks:
        known = sorted({d for d in t["deps"] if d in id_set})
        indeg[t["id"]] = len(known)
        for d in known:
            dependents[d].append(t["id"])
    queue = sorted([i for i in ids if indeg[i] == 0])
    order: list[str] = []
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for nxt in sorted(set(dependents[cur])):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
                queue.sort()
    if len(order) < len(ids):
        return order, sorted(set(ids) - set(order)), dependents
    return order, [], dependents


# ---------------------------------------------------------------- 补丁定位


def locate_patch_dir(patch_root: Path, task_id: str) -> Path | None:
    """在 <patch_root>/<task_id>/ 或 <patch_root>/<task_id>/<worker>/ 下找含 manifest.json 的目录。"""
    direct = patch_root / task_id
    if (direct / "manifest.json").is_file():
        return direct
    if direct.is_dir():
        for sub in sorted(direct.iterdir()):
            if sub.is_dir() and (sub / "manifest.json").is_file():
                return sub
    return None


# ---------------------------------------------------------------- 命令执行


def git_run(repo: Path, args: list[str], timeout: int) -> tuple[int, str]:
    """在 --repo 内执行 git（列表形式，不 shell=True）。返回 (退出码, stdout+stderr)。"""
    try:
        p = subprocess.run(
            ["git", "-C", str(repo)] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "git 命令超时"
    except OSError as exc:
        return -1, f"git 执行失败：{exc}"


def empty_evidence_text(command: str, rc: int, cwd: Path) -> str:
    """命令无输出时写入的最小可复核内容（LF 行尾，保证证据文件恒为非空）。

    - <command> 用原始命令字符串（不规范化），便于人直接复制重跑；
    - <cwd> 用 --repo 的绝对路径（run_command 的 cwd 即 repo）；
    - 有输出（哪怕只有 stderr）时不走此路径，保持只写原始输出、不加头部。
    """
    return (
        "[check_integration] 命令无输出\n"
        f"command: {command}\n"
        f"exit_code: {rc}\n"
        f"cwd: {cwd}\n"
    )


def run_command(command: str, cwd: Path, timeout: int, evidence_path: Path | None) -> tuple[int, bool]:
    """执行用户提供的门禁/消费方命令（shell=True，用户给的是完整命令行）。

    返回 (退出码, 是否超时)。stdout+stderr 写入 evidence_path（若有）。
    """
    timed_out = False
    try:
        p = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = (p.stdout or "") + (p.stderr or "")
        rc = p.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        parts: list[str] = []
        if isinstance(exc.stdout, bytes):
            parts.append(exc.stdout.decode("utf-8", "replace"))
        if isinstance(exc.stderr, bytes):
            parts.append(exc.stderr.decode("utf-8", "replace"))
        output = "".join(parts)
        rc = -1
    except Exception as exc:  # noqa: BLE001
        output = f"{type(exc).__name__}: {exc}"
        rc = -1
    if evidence_path is not None:
        try:
            if output:
                # 有输出（哪怕只有 stderr）：保持现状，只写原始输出，不加头部
                evidence_path.write_text(output, encoding="utf-8")
            else:
                # 无输出：写入最小可复核内容，避免产生 0 字节证据文件
                evidence_path.write_bytes(empty_evidence_text(command, rc, cwd).encode("utf-8"))
        except OSError:
            pass
    return rc, timed_out


def mark_dependents_skipped(
    tid: str, dependents: dict[str, list[str]], skipped: set[str], issues: list[dict]
) -> None:
    """合入失败后，标记所有（传递）依赖该任务的后续任务为跳过，并记录提示。"""
    stack = list(dependents.get(tid, []))
    visited: set[str] = set()
    while stack:
        cur = stack.pop()
        if cur in visited or cur in skipped:
            continue
        visited.add(cur)
        skipped.add(cur)
        issues.append(
            finding(
                "skipped-dependency",
                f"任务 {cur} 因依赖链包含任务 {tid}（合入失败）被跳过",
                fatal=False,
                task=cur,
            )
        )
        stack.extend(dependents.get(cur, []))


def run_phase(
    cmds: list[str],
    prefix: str,
    repo: Path,
    evidence_out: Path,
    timeout: int,
    issues: list[dict],
) -> list[dict]:
    """逐条执行门禁（prefix="gate"）或消费方（prefix="consumer"）命令。

    破坏性命令 → cmd-unsafe（判失败，不执行，无证据文件）；
    超时 → cmd-timeout（判失败）；退出码 ≠ 0 → <prefix>-failed（判失败）。
    返回已执行命令的 manifest.commands 条目（含 evidence_sha256）。
    """
    out: list[dict] = []
    for idx, cmd in enumerate(cmds, 1):
        if is_unsafe(cmd):
            issues.append(finding("cmd-unsafe", f"命令含破坏性片段，拒绝执行：{cmd}", command=cmd))
            continue  # 不执行，不产生证据文件
        ev_name = f"{prefix}-{idx:02d}.txt"
        ev_path = evidence_out / ev_name
        rc, timed_out = run_command(cmd, repo, timeout, ev_path)
        if timed_out:
            issues.append(finding("cmd-timeout", f"{prefix} 命令超时（>{timeout}s）：{cmd}", command=cmd))
        elif rc != 0:
            issues.append(finding(f"{prefix}-failed", f"{prefix} 命令退出码 {rc}：{cmd}", command=cmd))
        out.append(
            {
                "command": cmd,
                "exit_code": rc,
                "evidence": ev_name,
                "evidence_sha256": sha256_file(ev_path),
            }
        )
    return out


# ---------------------------------------------------------------- 主流程


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="补丁三关之集成关：DAG 拓扑合入 + 跨补丁所有权 + 范围 + 门禁 + 消费方验证"
    )
    ap.add_argument("--repo", required=True, help="集成分支的干净 worktree（git 仓库）")
    ap.add_argument("--patches", required=True, help="补丁包根目录")
    ap.add_argument("--dag", required=True, help="orchestration.toml 路径")
    ap.add_argument("--gate-cmd", action="append", default=[], help="全量门禁命令，可重复")
    ap.add_argument("--consumer-cmd", action="append", default=[], help="消费方验证命令，可重复")
    ap.add_argument("--evidence-out", required=True, help="证据输出目录")
    ap.add_argument("--base", default=DEFAULT_BASE, help="基线分支，默认 origin/main")
    ap.add_argument("--allow", action="append", default=[], help="集成单独占的共享文件/契约产物，可重复")
    ap.add_argument("--contract-product", action="append", default=[], help="契约产物路径，可重复")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="命令超时秒数，默认 120")
    ap.add_argument("--dry-run", action="store_true", help="只做检查与计划，不 apply、不执行命令")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if args.timeout <= 0:
        die("--timeout 必须为正整数")

    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        die(f"--repo 不是目录：{repo}")
    if not is_git_repo(repo):
        die(f"--repo 不是 git 仓库：{repo}")

    patches_root = Path(args.patches).expanduser().resolve()
    if not patches_root.is_dir():
        die(f"--patches 不是目录：{patches_root}")

    dag_path = Path(args.dag).expanduser().resolve()
    if not dag_path.is_file():
        die(f"--dag 不存在：{dag_path}")

    evidence_out = Path(args.evidence_out).expanduser().resolve()
    try:
        evidence_out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        die(f"--evidence-out 创建失败：{exc}")
    if not evidence_out.is_dir():
        die(f"--evidence-out 不是目录：{evidence_out}")

    base = args.base.strip() or DEFAULT_BASE
    allow_paths = {norm_path(p) for p in args.allow if p.strip()}
    contract_products = [norm_path(p) for p in args.contract_product if p.strip()]

    # 基线预检：在 apply 之前验证 <base> 可解析（只用 git diff，符合安全边界）。
    # 基线不可解析属参数/环境错误 → 退出码 2（先于任何 apply，避免留下半集成状态）。
    pre_rc, pre_out = git_run(repo, ["diff", "--name-only", base], args.timeout)
    if pre_rc != 0:
        die(f"基线不可解析：{base}（git diff --name-only 失败：{pre_out.strip()}）")

    issues: list[dict] = []

    # 1. DAG 拓扑排序
    tasks = load_dag(dag_path)
    order, cycle, dependents = topo_sort(tasks)
    if cycle:
        die(f"DAG 存在依赖环：{' -> '.join(cycle)}")

    # 2. 定位补丁包
    patch_dirs: dict[str, Path] = {}
    for t in tasks:
        tid = t["id"]
        loc = locate_patch_dir(patches_root, tid)
        if loc is None:
            issues.append(finding("patch-missing", f"找不到 {tid} 的补丁包（含 manifest.json）", task=tid))
        else:
            patch_dirs[tid] = loc

    # 读 manifest（utf-8-sig 兼容带 BOM），取 files 作为该补丁的允许路径
    manifest_files: dict[str, list[str]] = {}
    for tid, pdir in patch_dirs.items():
        mpath = pdir / "manifest.json"
        try:
            m = json.loads(mpath.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            issues.append(finding("manifest-invalid", f"{tid} 的 manifest.json 不是合法 JSON：{exc}", task=tid))
            manifest_files[tid] = []
            continue
        if not isinstance(m, dict):
            issues.append(finding("manifest-invalid", f"{tid} 的 manifest.json 顶层不是对象", task=tid))
            manifest_files[tid] = []
            continue
        files = m.get("files")
        if not isinstance(files, list):
            issues.append(finding("manifest-files-invalid", f"{tid} 的 manifest.files 不是数组", task=tid))
            manifest_files[tid] = []
        else:
            manifest_files[tid] = [norm_path(str(f)) for f in files if str(f).strip()]

    # 3. 逐个补丁（按拓扑序）：apply --check → apply
    applied: list[dict] = []
    skipped: set[str] = set()
    for tid in order:
        if tid in skipped:
            continue
        if tid not in patch_dirs:
            continue
        pdir = patch_dirs[tid]
        patch_file = pdir / "change.patch"
        if not patch_file.is_file():
            issues.append(finding("patch-file-missing", f"{tid} 缺 change.patch", task=tid))
            if not args.dry_run:
                applied.append({"task": tid, "patch": str(patch_file), "exit_code": 1})
            mark_dependents_skipped(tid, dependents, skipped, issues)
            continue
        check_rc, check_out = git_run(repo, ["apply", "--check", "--binary", str(patch_file)], args.timeout)
        if check_rc != 0:
            issues.append(
                finding(
                    "apply-check-failed",
                    f"{tid} 的补丁无法应用（git apply --check 失败）：{check_out.strip()}",
                    task=tid,
                )
            )
            if not args.dry_run:
                applied.append({"task": tid, "patch": str(patch_file), "exit_code": 1})
            mark_dependents_skipped(tid, dependents, skipped, issues)
            continue
        if args.dry_run:
            continue  # 只检查，不 apply、不记录 applied
        apply_rc, apply_out = git_run(repo, ["apply", "--binary", str(patch_file)], args.timeout)
        if apply_rc != 0:
            issues.append(
                finding("apply-failed", f"{tid} 的补丁 apply 失败：{apply_out.strip()}", task=tid)
            )
            applied.append({"task": tid, "patch": str(patch_file), "exit_code": 1})
            mark_dependents_skipped(tid, dependents, skipped, issues)
            continue
        applied.append({"task": tid, "patch": str(patch_file), "exit_code": 0})

    # 4. 跨补丁文件所有权：同一路径出现在 >1 个补丁的 files
    file_owners: dict[str, list[str]] = {}
    for tid, files in manifest_files.items():
        for f in files:
            file_owners.setdefault(f, []).append(tid)
    for f, owners in sorted(file_owners.items()):
        if len(owners) > 1:
            issues.append(
                finding(
                    "file-multi-owner",
                    f"文件 {f} 被多个补丁声明所有权：{', '.join(sorted(owners))}",
                )
            )

    # 5. 契约产物唯一 owner（未给 --contract-product 则跳过）
    for prod in contract_products:
        owners = file_owners.get(prod, [])
        if len(owners) > 1:
            issues.append(
                finding(
                    "contract-product-multi-owner",
                    f"契约产物 {prod} 被多个补丁声明所有权：{', '.join(sorted(owners))}",
                )
            )

    # 6. 变更范围：实际变更集 ⊆（补丁 files 并集 ∪ --allow）
    changed_paths: list[str] = []
    scope_violations: list[str] = []
    if not args.dry_run:
        diff_rc, diff_out = git_run(repo, ["diff", "--name-only", base], args.timeout)
        if diff_rc != 0:
            issues.append(finding("scope-check-error", f"git diff --name-only {base} 失败：{diff_out.strip()}"))
            diff_out = ""
        changed_paths = [norm_path(p) for p in diff_out.splitlines() if p.strip()]
        allowed_union = set(allow_paths)
        for files in manifest_files.values():
            allowed_union.update(files)
        scope_violations = [p for p in changed_paths if p not in allowed_union]
        for p in scope_violations:
            issues.append(finding("scope-violation", f"越界路径：{p}（不在任何补丁 files 或 --allow 内）"))

    # 7-8. 门禁与消费方命令（仅非 dry-run）
    commands_manifest: list[dict] = []
    if not args.dry_run:
        commands_manifest.extend(run_phase(args.gate_cmd, "gate", repo, evidence_out, args.timeout, issues))
        commands_manifest.extend(
            run_phase(args.consumer_cmd, "consumer", repo, evidence_out, args.timeout, issues)
        )

    # 9. 生成集成清单（仅非 dry-run）
    if not args.dry_run:
        manifest_obj = {
            "base": base,
            "applied_patches": applied,
            "commands": commands_manifest,
            "scope_violations": scope_violations,
            "changed_paths": changed_paths,
        }
        try:
            (evidence_out / "integration-manifest.json").write_text(
                json.dumps(manifest_obj, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            issues.append(finding("manifest-write-error", f"integration-manifest.json 写入失败：{exc}"))

    # 10. 输出
    fatal_count = sum(1 for f in issues if f["fatal"])
    info_count = sum(1 for f in issues if not f["fatal"])
    ok = fatal_count == 0

    if args.json:
        result = {
            "ok": ok,
            "repo": str(repo),
            "patches": str(patches_root),
            "dag": str(dag_path),
            "applied": applied,
            "changed_paths": changed_paths,
            "issues": issues,
            "summary": {"fatal": fatal_count, "info": info_count},
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"仓库：{repo}")
        print(f"补丁根：{patches_root}")
        print(f"DAG：{dag_path}")
        print(f"基线：{base}")
        if args.dry_run:
            print("模式：dry-run（只检查，不 apply、不执行命令）")
        for f in issues:
            print(f"[{'严重' if f['fatal'] else '提示'}] {f['kind']}: {f['detail']}")
        print()
        if ok:
            print("集成关：通过")
        else:
            print(f"集成关：不通过（失败 {fatal_count} 项，提示 {info_count} 项）")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
