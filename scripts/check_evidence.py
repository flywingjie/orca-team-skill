#!/usr/bin/env python3
"""补丁三关之正确关：工单验收项 × manifest.checks × evidence 的机器可读结论。

用法：
    python scripts/check_evidence.py --ticket tickets/API-014.md \
        --patch-dir patches/API-014/backend

    python scripts/check_evidence.py --ticket ... --patch-dir ... --json

检查项（对应 references/07-verification.md §2.2 正确关）：
    ① 工单验收项覆盖：解析「## 验收」小节的 | # | 命令 | 期望结果 | 表格
    ② 命令是否真实执行：工单验收命令与 manifest.checks 规范化后匹配，且 exit_code == 0
    ③ evidence 存在且非空：checks[].evidence 相对补丁包目录解析
    ④ manifest 基本完整性：必填字段齐全、files 非空、checks 结构合法
    ⑤ 负路径覆盖：未勾选项只报告，不计入失败

只读，不修改任何文件。仅用标准库，需要 Python 3.11+。
读入工单与 manifest 用 utf-8-sig：兼容 Windows 上 PowerShell 5.1 默认写出的 UTF-8 BOM，
带 BOM 与不带 BOM 的输入必须得到同一条正确关结论。
退出码：0 正确关通过；1 有失败项；2 参数错误或文件读取/解析失败。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

# manifest.json 必填字段（references/07-verification.md §1.1）
REQUIRED_FIELDS = ("ticket", "worker", "base_commit", "files", "checks")

# 负路径覆盖清单：- [ ] / - [x] / - [X]
CHECKBOX_RE = re.compile(r"^\s*[-*+]\s*\[( |x|X)\]\s*(.*)$")
# 表格分隔行：|---|---| 或 |:--|--:|
SEPARATOR_CELL_RE = re.compile(r"^:?-{2,}:?$")
# 标题行：## 验收 / ### 验收记录
HEADING_RE = re.compile(r"^(#{1,6})\s*(.*?)\s*$")
# 水平分隔线
HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")


def die(msg: str) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(2)


# ---------------------------------------------------------------- 文本工具


def strip_backticks(text: str) -> str:
    """去掉反引号与首尾空白。"""
    return text.replace("`", "").strip()


def normalize_command(text: str) -> str:
    """命令规范化：去首尾空白 → 折叠连续空白 → 去行尾续行反斜杠。

    实现顺序说明：先按行去掉行尾续行反斜杠，再折叠空白。若先折叠空白，
    行尾反斜杠将不再是「行尾」，续行语义会丢失（`cmd \\` + `args` 应拼成
    `cmd args` 而不是 `cmd \\ args`）。
    """
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    parts: list[str] = []
    for line in s.split("\n"):
        line = line.strip()
        if line.endswith("\\"):
            line = line[:-1].rstrip()
        if line:
            parts.append(line)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def split_row(line: str) -> list[str]:
    """拆分 Markdown 表格行，反引号内的 `|` 不作为分隔符。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    for ch in s:
        if ch == "`":
            in_code = not in_code
            buf.append(ch)
        elif ch == "|" and not in_code:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    cells.append("".join(buf).strip())
    return cells


def is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(SEPARATOR_CELL_RE.match(c.strip()) for c in cells if c.strip() != "")


def is_acceptance_header(cells: list[str]) -> bool:
    """表头形如 | # | 命令 | 期望结果 |。"""
    if len(cells) < 3:
        return False
    if cells[0].strip() not in ("#", "序号"):
        return False
    return "命令" in cells[1] and cells[2].strip().startswith("期望")


def extract_section(text: str, heading: str) -> str:
    """取指定标题的小节正文；遇到同级或更高级标题、水平分隔线即结束。"""
    lines = text.splitlines()
    start: int | None = None
    level = 2
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and m.group(2).strip().startswith(heading):
            start = i + 1
            level = len(m.group(1))
            break
    if start is None:
        return ""
    out: list[str] = []
    for line in lines[start:]:
        if HR_RE.match(line):
            break
        m = HEADING_RE.match(line)
        if m and len(m.group(1)) <= level:
            break
        out.append(line)
    return "\n".join(out)


# ------------------------------------------------------------ 工单解析


def parse_acceptance_items(section: str) -> list[dict]:
    """解析「## 验收」小节中的验收表，返回验收项列表。"""
    items: list[dict] = []
    lines = section.splitlines()
    total = len(lines)
    i = 0
    while i < total:
        line = lines[i]
        if line.strip().startswith("|"):
            header = split_row(line)
            if is_acceptance_header(header):
                i += 1
                row_no = 0
                while i < total and lines[i].strip().startswith("|"):
                    cells = split_row(lines[i])
                    if not is_separator_row(cells):
                        row_no += 1
                        items.append(
                            {
                                "row": row_no,
                                "command": strip_backticks(cells[1]) if len(cells) > 1 else "",
                                "expectation": strip_backticks(cells[2]) if len(cells) > 2 else "",
                                "malformed": len(cells) < 3,
                            }
                        )
                    i += 1
                continue
        i += 1
    return items


def parse_negative_paths(section: str) -> list[str]:
    """返回「负路径覆盖：」之后未勾选（- [ ]）的条目文本。"""
    lines = section.splitlines()
    marker = None
    for i, line in enumerate(lines):
        if line.strip().replace(" ", "").startswith("负路径覆盖"):
            marker = i
            break
    scope = lines[marker:] if marker is not None else lines
    unchecked: list[str] = []
    for line in scope:
        m = CHECKBOX_RE.match(line)
        if m and m.group(1) == " ":
            unchecked.append(m.group(2).strip())
    return unchecked


# ------------------------------------------------------------ manifest


def load_manifest(path: Path) -> dict:
    if not path.exists():
        die(f"manifest 不存在：{path}")
    if not path.is_file():
        die(f"manifest 不是文件：{path}")
    try:
        # utf-8-sig：兼容 Windows 上 PowerShell 5.1 的 Set-Content/Out-File 默认写出的 BOM
        text = path.read_text(encoding="utf-8-sig")
    except Exception as e:  # noqa: BLE001
        die(f"manifest 读取失败：{e}")
    try:
        data = json.loads(text)
    except Exception as e:  # noqa: BLE001
        die(f"manifest 不是合法 JSON：{e}")
    if not isinstance(data, dict):
        die("manifest 顶层不是 JSON 对象")
    return data


def resolve_evidence(patch_dir: Path, ev: str) -> Path:
    """evidence 相对补丁包目录解析；同时兼容 POSIX `/` 与 Windows `\\`。"""
    rel = ev.replace("\\", "/")
    p = PurePosixPath(rel)
    first = p.parts[0] if p.parts else ""
    if p.is_absolute() or (len(first) == 2 and first[1] == ":"):
        return Path(rel)
    return patch_dir.joinpath(*p.parts)


def exit_code_ok(value: Any) -> bool:
    """exit_code 是否表示成功（0）。非 0 / 缺失 / 非法一律视为失败。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        try:
            return int(value.strip()) == 0
        except ValueError:
            return False
    return False


# ------------------------------------------------------------ 检查项


def finding(kind: str, detail: str, command: str = "", fatal: bool = True) -> dict:
    return {"kind": kind, "detail": detail, "command": command, "fatal": bool(fatal)}


def check_acceptance_items(items: list[dict]) -> list[dict]:
    """① 工单验收项覆盖。"""
    out: list[dict] = []
    for item in items:
        if item["malformed"]:
            out.append(
                finding(
                    "empty-command",
                    f"验收表第 {item['row']} 行不足 3 列，读不到命令",
                    item["command"],
                )
            )
            continue
        if not item["command"]:
            out.append(finding("empty-command", f"验收表第 {item['row']} 行命令为空"))
        if not item["expectation"]:
            out.append(
                finding(
                    "empty-expectation",
                    f"验收表第 {item['row']} 行期望结果为空",
                    item["command"],
                )
            )
    if not items:
        out.append(
            finding(
                "no-acceptance-items",
                "工单「验收」段没有可解析的验收项（表头形如 | # | 命令 | 期望结果 |）",
            )
        )
    return out


def check_commands_executed(items: list[dict], check_list: list) -> tuple[list[dict], int]:
    """② 命令是否真实执行：返回 (findings, 已执行条数)。"""
    out: list[dict] = []
    normalized: list[tuple[str, dict]] = []
    for chk in check_list:
        if isinstance(chk, dict):
            normalized.append((normalize_command(strip_backticks(str(chk.get("command", "")))), chk))

    executed = 0
    for item in items:
        cmd = item["command"]
        if not cmd:
            continue  # 已由 empty-command 报出，不重复报 command-not-executed
        target = normalize_command(cmd)
        matches = [(c, chk) for c, chk in normalized if c and c == target]
        if not matches:
            out.append(
                finding(
                    "command-not-executed",
                    f"验收命令未出现在 manifest.checks 中：{cmd}",
                    cmd,
                )
            )
            continue
        executed += 1
        for _c, chk in matches:
            ec = chk.get("exit_code")
            if not exit_code_ok(ec):
                out.append(
                    finding(
                        "command-failed",
                        f"验收命令 exit_code != 0（exit={ec!r}）：{cmd}",
                        cmd,
                    )
                )
    return out, executed


def check_evidence_files(check_list: list, patch_dir: Path) -> list[dict]:
    """③ evidence 存在且非空。"""
    out: list[dict] = []
    for idx, chk in enumerate(check_list, 1):
        if not isinstance(chk, dict):
            continue  # 结构非法已在 ④ 报出
        cmd = strip_backticks(str(chk.get("command", "")))
        ev = chk.get("evidence")
        if not isinstance(ev, str) or not ev.strip():
            out.append(finding("evidence-missing", f"checks[{idx}] 缺少 evidence 字段", cmd))
            continue
        path = resolve_evidence(patch_dir, ev)
        if not path.exists():
            out.append(finding("evidence-missing", f"checks[{idx}] evidence 不存在：{ev}", cmd))
        elif not path.is_file():
            out.append(finding("evidence-missing", f"checks[{idx}] evidence 不是文件：{ev}", cmd))
        elif path.stat().st_size == 0:
            out.append(finding("evidence-empty", f"checks[{idx}] evidence 为空（0 字节）：{ev}", cmd))
    return out


def check_manifest_integrity(manifest: dict, check_list: list) -> list[dict]:
    """④ manifest 基本完整性。"""
    out: list[dict] = []
    for key in REQUIRED_FIELDS:
        if key not in manifest:
            out.append(finding("manifest-missing-field", f"manifest 缺少必填字段：{key}"))
    files = manifest.get("files")
    if "files" in manifest:
        if not isinstance(files, list):
            out.append(finding("manifest-missing-field", "manifest.files 不是数组（结构非法）"))
        elif not files:
            out.append(finding("manifest-empty-files", "manifest.files 是空数组"))
    if "checks" in manifest and not isinstance(manifest.get("checks"), list):
        out.append(finding("manifest-missing-field", "manifest.checks 不是数组（结构非法）"))
    for idx, chk in enumerate(check_list, 1):
        if not isinstance(chk, dict):
            out.append(finding("manifest-missing-field", f"checks[{idx}] 不是对象（结构非法）"))
    return out


def check_negative_paths(unchecked: list[str]) -> list[dict]:
    """⑤ 负路径覆盖：只报告，不计入失败。"""
    if not unchecked:
        return []
    detail = "负路径覆盖未勾选 %d 项：%s" % (len(unchecked), "；".join(unchecked))
    return [finding("negative-path-unchecked", detail, "", fatal=False)]


# ------------------------------------------------------------ 主流程


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="补丁三关之正确关：工单验收项 × manifest.checks × evidence 核对（只读）"
    )
    ap.add_argument("--ticket", required=True, help="工单 Markdown 路径")
    ap.add_argument("--patch-dir", required=True, help="补丁包目录（含 manifest.json 与 evidence/）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    ticket_path = Path(args.ticket).expanduser()
    if not ticket_path.exists():
        die(f"工单不存在：{ticket_path}")
    if not ticket_path.is_file():
        die(f"工单不是文件：{ticket_path}")
    try:
        # utf-8-sig：带 BOM 的工单也能读，且不会把 BOM 混进正文首字符
        ticket_text = ticket_path.read_text(encoding="utf-8-sig")
    except Exception as e:  # noqa: BLE001
        die(f"工单读取失败：{e}")

    patch_dir = Path(args.patch_dir).expanduser()
    if not patch_dir.exists():
        die(f"补丁包目录不存在：{patch_dir}")
    if not patch_dir.is_dir():
        die(f"补丁包路径不是目录：{patch_dir}")

    manifest = load_manifest(patch_dir / "manifest.json")

    section = extract_section(ticket_text, "验收")
    items = parse_acceptance_items(section)
    unchecked = parse_negative_paths(section)

    raw_checks = manifest.get("checks")
    check_list: list = raw_checks if isinstance(raw_checks, list) else []

    f1 = check_acceptance_items(items)
    f2, executed = check_commands_executed(items, check_list)
    f3 = check_evidence_files(check_list, patch_dir)
    f4 = check_manifest_integrity(manifest, check_list)
    f5 = check_negative_paths(unchecked)
    findings = f1 + f2 + f3 + f4 + f5

    fatal_count = sum(1 for f in findings if f["fatal"])
    info_count = sum(1 for f in findings if not f["fatal"])
    ok = fatal_count == 0

    if args.json:
        result = {
            "ok": ok,
            "ticket": str(ticket_path.resolve()),
            "patch_dir": str(patch_dir.resolve()),
            "acceptance_items": len(items),
            "executed": executed,
            "checks": findings,
            "summary": {"fatal": fatal_count, "info": info_count},
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"工单：{ticket_path.resolve()}")
        print(f"补丁包：{patch_dir.resolve()}")
        print(f"验收项：{len(items)}    已执行：{executed}")
        for f in findings:
            print(f"[{'严重' if f['fatal'] else '提示'}] {f['kind']}: {f['detail']}")
        print()
        if ok:
            print("正确关：通过")
        else:
            print(f"正确关：不通过（失败 {fatal_count} 项，提示 {info_count} 项）")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
