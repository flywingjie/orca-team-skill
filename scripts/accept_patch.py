#!/usr/bin/env python3
"""补丁三关辅助：范围关 + 证据完整性检查。

用法：
    python scripts/accept_patch.py --manifest patches/API-014/backend/manifest.json \
        --repo /abs/repo \
        --allowed server/api/orders.py --allowed server/tests/test_orders.py

    # 允许目录/通配符
    python scripts/accept_patch.py --manifest ... --repo ... --allowed "server/api/**"

    # 只做静态检查，不调用 git
    python scripts/accept_patch.py --manifest ... --repo ... --allowed ... --no-apply-check

检查项（对应 SPEC.md V-1 ~ V-4）：
    - manifest 必需字段
    - 补丁路径可解析
    - 补丁路径 ⊆ --allowed（支持 glob 与目录前缀）
    - manifest.files 与补丁路径一致
    - 每条 check 的证据文件存在、非空，且位于补丁包目录内
    - 可选：git apply --check
    - 危险路径/凭据文件拦截

退出码：0 全过；1 有失败；2 参数/读取错误。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import NoReturn

DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
PLUS_RE = re.compile(r"^\+\+\+ b/(.+)$")
FORBIDDEN_NAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    ".gh_token",
    "credentials.json",
    "credentials",
    "secrets.json",
    ".netrc",
    "_netrc",
    ".npmrc",
    ".pgpass",
    ".git-credentials",
    ".htpasswd",
}
FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


def is_within(path: Path, root: Path) -> bool:
    """path 解析后是否位于 root 之内（防 ../与绝对路径逃出补丁包）。"""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def die(msg: str) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(2)


def parse_patch_paths(patch_text: str) -> list[str]:
    paths: list[str] = []
    for line in patch_text.splitlines():
        m = DIFF_GIT_RE.match(line)
        if m:
            paths.append(m.group(2))
            continue
        m = PLUS_RE.match(line)
        if m and m.group(1) != "/dev/null":
            paths.append(m.group(1))
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def matches_allowed(path: str, patterns: list[str]) -> bool:
    for raw in patterns:
        pat = raw.replace("\\", "/").rstrip("/")
        p = path.replace("\\", "/")
        if p == pat or p.startswith(pat + "/"):
            return True
        if fnmatch.fnmatch(p, raw) or fnmatch.fnmatch(p, raw.replace("\\", "/")):
            return True
    return False


def is_forbidden(path: str) -> bool:
    p = PurePosixPath(path.replace("\\", "/"))
    if p.name in FORBIDDEN_NAMES:
        return True
    if p.suffix.lower() in FORBIDDEN_SUFFIXES:
        return True
    if p.name.startswith(".env"):
        return True
    return False


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

    ap = argparse.ArgumentParser(description="补丁范围关 + 证据完整性检查")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--patch", default=None, help="默认取 manifest 同目录的 change.patch")
    ap.add_argument("--repo", default=".", help="用于 git apply --check 的仓库路径")
    ap.add_argument("--allowed", action="append", default=[], help="允许的路径/目录/glob，可重复")
    ap.add_argument("--no-apply-check", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    manifest_path = Path(args.manifest).resolve()
    if not manifest_path.exists():
        die(f"manifest 不存在：{manifest_path}")
    base_dir = manifest_path.parent

    try:
        # utf-8-sig 兼容带 BOM 的 manifest（Windows PowerShell 5.1 的 Set-Content 默认写 BOM）
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except Exception as e:  # noqa: BLE001
        die(f"manifest 不是合法 JSON：{e}")

    patch_path = Path(args.patch) if args.patch else base_dir / "change.patch"
    if not patch_path.exists():
        die(f"补丁不存在：{patch_path}")

    errors: list[str] = []
    warnings: list[str] = []

    required = ["ticket", "worker", "base_commit", "files", "checks"]
    for key in required:
        if key not in manifest:
            errors.append(f"manifest 缺少字段：{key}")

    patch_text = patch_path.read_text(encoding="utf-8", errors="replace")
    patch_paths = parse_patch_paths(patch_text)
    if not patch_paths:
        errors.append("补丁中解析不到任何文件路径")

    manifest_files = [str(x).replace("\\", "/") for x in manifest.get("files", [])]
    patch_set = set(patch_paths)
    mf_set = set(manifest_files)

    if args.allowed:
        for p in patch_paths:
            if not matches_allowed(p, args.allowed):
                errors.append(f"越界：{p} 不在 --allowed 内")
    else:
        warnings.append("未提供 --allowed：跳过范围关的路径白名单检查")

    for p in patch_paths:
        if is_forbidden(p):
            errors.append(f"危险文件：{p}（凭据/密钥类文件不得提交）")

    missing_in_patch = sorted(mf_set - patch_set)
    extra_in_patch = sorted(patch_set - mf_set)
    if missing_in_patch:
        errors.append("manifest.files 声称有但补丁中没有：" + ", ".join(missing_in_patch))
    if extra_in_patch:
        errors.append("补丁中有但 manifest.files 没列：" + ", ".join(extra_in_patch))

    evidence_errors: list[str] = []
    for i, chk in enumerate(manifest.get("checks", []), 1):
        if not isinstance(chk, dict):
            errors.append(f"checks[{i}] 不是对象")
            continue
        cmd = chk.get("command")
        ev = chk.get("evidence")
        if not cmd:
            errors.append(f"checks[{i}] 缺少 command")
        if not ev:
            errors.append(f"checks[{i}] 缺少 evidence")
            continue
        ev_path = (base_dir / str(ev)).resolve()
        if not is_within(ev_path, base_dir):
            evidence_errors.append(f"checks[{i}] 证据不在补丁包目录内：{ev}")
        elif not ev_path.exists():
            evidence_errors.append(f"checks[{i}] 证据不存在：{ev}")
        elif ev_path.stat().st_size == 0:
            evidence_errors.append(f"checks[{i}] 证据为空：{ev}")
    errors.extend(evidence_errors)

    apply_result = None
    if not args.no_apply_check:
        repo = Path(args.repo).resolve()
        try:
            p = subprocess.run(
                ["git", "-C", str(repo), "apply", "--check", "--binary", str(patch_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            apply_result = {
                "command": f"git -C {repo} apply --check --binary {patch_path}",
                "exit_code": p.returncode,
                "output": (p.stdout or "") + (p.stderr or ""),
            }
            if p.returncode != 0:
                errors.append("补丁无法应用（git apply --check 失败）")
        except FileNotFoundError:
            errors.append("找不到 git，无法执行 apply --check")
        except Exception as e:  # noqa: BLE001
            errors.append(f"git apply --check 异常：{type(e).__name__}: {e}")

    result = {
        "manifest": str(manifest_path),
        "patch": str(patch_path),
        "ticket": manifest.get("ticket"),
        "worker": manifest.get("worker"),
        "patch_paths": patch_paths,
        "allowed": args.allowed,
        "apply_check": apply_result,
        "errors": errors,
        "warnings": warnings,
        "passed": not errors,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"补丁：{patch_path}")
        print(f"单号：{manifest.get('ticket', '?')}    worker：{manifest.get('worker', '?')}")
        print("\n路径：")
        for p in patch_paths:
            mark = "OK " if (not args.allowed or matches_allowed(p, args.allowed)) else "越界"
            print(f"  [{mark}] {p}")
        if apply_result:
            print(f"\n应用检查：[{apply_result['exit_code']}] {apply_result['command']}")
            if apply_result["output"].strip():
                print(apply_result["output"].strip())
        if warnings:
            print("\n提示：")
            for w in warnings:
                print(f"  - {w}")
        if errors:
            print("\n失败项：")
            for e in errors:
                print(f"  - {e}")
            print("\n结论：不通过")
        else:
            print("\n结论：范围关通过（正确关与集成关仍需实跑门禁）")

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
