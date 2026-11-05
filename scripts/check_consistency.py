#!/usr/bin/env python3
"""工单 ↔ 编排 DAG 一致性检查（只读）。

用法：
    python scripts/check_consistency.py --tickets tickets --dag orchestration.toml
    python scripts/check_consistency.py --tickets tickets --dag orchestration.toml --json

检查项（对应 SPEC.md P-4、references/03-ticket.md §3.4、references/04-parallelism.md §2）：
    ① 单号集合：工单标题「# 工单 <单号>：」 vs [[tasks]].id
    ② 归属文件集：工单「## 归属」→「本单独占文件：」 vs [[tasks]].files
    ③ 依赖：工单「## 归属」→「- 依赖：」 vs [[tasks]].depends_on
    ④ 基线：工单「## 约束」→「- 基线：」 vs [[tasks]].base_branch

容错：单个工单文件损坏 / 缺小节 / 单行解析失败 → 记 parse-error / missing-section 后继续；
      只有「工单目录不存在 / DAG 文件不存在 / DAG 非法 TOML」才退出码 2。

输入编码：工单 Markdown 与 DAG TOML 一律按 utf-8-sig 读，兼容带/不带 BOM
          （Windows 的 Set-Content / Out-File -Encoding UTF8 默认写 BOM）；
          输出 stdout / stderr 一律 UTF-8。

只读，不修改任何文件。需要 Python 3.11+（tomllib）。
退出码：0 完全一致；1 存在不一致；2 参数或读取错误。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


DEFAULT_BASE = "origin/main"
ISSUE_KINDS = ("mismatch", "ticket-only", "dag-only", "missing-section", "parse-error")

TITLE_RE = re.compile(r"^#\s*工单\s+([A-Za-z0-9\-_]+)\s*[:：]")
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
OWNER_MARKER_RE = re.compile(r"^[-*]\s*本单独占文件\s*[：:]")
DEP_RE = re.compile(r"^[-*]\s*依赖\s*[：:]\s*(.*)$")
BASE_RE = re.compile(r"^[-*]\s*基线\s*[：:]\s*(.*)$")
BACKTICK_RE = re.compile(r"`([^`]*)`")
ID_TOKEN_RE = re.compile(r"[A-Za-z0-9\-_]+")


def die(msg: str) -> NoReturn:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(2)


def normalize_path(raw: str) -> str:
    """路径归一：统一分隔符为 /，忽略前后空白。"""
    return raw.strip().replace("\\", "/")


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def fmt_set(values: set[str]) -> str:
    return ", ".join(sorted(values)) if values else "-"


@dataclass
class Ticket:
    """单个工单文件的解析结果。"""

    tid: str
    path: Path
    files: set[str] = field(default_factory=set)
    deps: set[str] = field(default_factory=set)
    baseline: str | None = None
    owner_section: bool = False   # 「## 归属」小节存在
    owner_marker: bool = False    # 「本单独占文件：」条目存在
    constraint_section: bool = False  # 「## 约束」小节存在


def find_sections(lines: list[str]) -> dict[str, list[tuple[int, str]]]:
    """按「## 小节名」切分；同名小节只保留第一次出现。"""
    sections: dict[str, list[tuple[int, str]]] = {}
    seen: set[str] = set()
    current: str | None = None
    for i, line in enumerate(lines, 1):
        m = HEADING_RE.match(line)
        if m:
            name = m.group(1).strip()
            if name in seen:
                current = f"{name}#dup{i}"
            else:
                seen.add(name)
                current = name
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        sections[current].append((i, line))
    return sections


def get_section(sections: dict[str, list[tuple[int, str]]], name: str) -> list[tuple[int, str]] | None:
    if name in sections:
        return sections[name]
    for key, body in sections.items():
        if key.startswith(name):
            return body
    return None


def extract_path(entry: str) -> str:
    """从条目文本取路径：优先取反引号包裹的内容；没有反引号则取整条文本。"""
    m = BACKTICK_RE.search(entry)
    if m:
        return m.group(1).strip()
    body = entry.lstrip("-* \t").strip()
    return body


def extract_value(raw: str) -> str:
    """从「键：值」右侧取标量值：优先取反引号包裹的内容；否则去尾部句读。"""
    value = raw.strip().rstrip("。；;")
    m = BACKTICK_RE.search(value)
    if m:
        return m.group(1).strip()
    return value.strip()


def parse_ticket(path: Path) -> tuple[Ticket | None, list[dict]]:
    """解析单个工单文件；返回 (工单, 问题列表)。失败不抛异常。"""
    issues: list[dict] = []
    try:
        # utf-8-sig：兼容带/不带 BOM（Windows 下 Set-Content/Out-File -Encoding UTF8 默认写 BOM）
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        issues.append(
            {
                "kind": "parse-error",
                "ticket": path.name,
                "detail": f"{path.name}：不是合法 UTF-8 文本，文件解析失败（{exc}）",
            }
        )
        return None, issues
    except OSError as exc:
        issues.append(
            {
                "kind": "parse-error",
                "ticket": path.name,
                "detail": f"{path.name}：文件读取失败（{exc}）",
            }
        )
        return None, issues

    lines = text.splitlines()

    tid: str | None = None
    for line in lines:
        m = TITLE_RE.match(line)
        if m:
            tid = m.group(1)
            break
    if tid is None:
        issues.append(
            {
                "kind": "parse-error",
                "ticket": path.name,
                "detail": f"{path.name}：未找到工单标题行（# 工单 <单号>：），文件解析失败",
            }
        )
        return None, issues

    ticket = Ticket(tid=tid, path=path)
    sections = find_sections(lines)

    # ---- 「## 归属」：独占文件 + 依赖 ----
    owner = get_section(sections, "归属")
    if owner is None:
        issues.append(
            {"kind": "missing-section", "ticket": tid, "detail": "缺少「## 归属」小节"}
        )
    else:
        ticket.owner_section = True

        marker_index = -1
        marker_indent = 0
        for idx, (_lineno, line) in enumerate(owner):
            if OWNER_MARKER_RE.match(line.strip()):
                marker_index = idx
                marker_indent = indent_of(line)
                break

        if marker_index < 0:
            issues.append(
                {
                    "kind": "missing-section",
                    "ticket": tid,
                    "detail": "「## 归属」小节缺少「本单独占文件：」条目",
                }
            )
        else:
            ticket.owner_marker = True
            for lineno, line in owner[marker_index + 1 :]:
                if HEADING_RE.match(line):
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                if not (stripped.startswith("- ") or stripped.startswith("-\t") or stripped == "-"):
                    continue
                if indent_of(line) <= marker_indent:
                    # 与「本单独占文件：」同级的条目 → 文件清单结束
                    break
                raw_path = extract_path(stripped)
                if not raw_path:
                    issues.append(
                        {
                            "kind": "parse-error",
                            "ticket": tid,
                            "detail": f"{path.name}:{lineno} 归属文件条目为空，无法解析出路径",
                        }
                    )
                    continue
                ticket.files.add(normalize_path(raw_path))

        for _lineno, line in owner:
            m = DEP_RE.match(line.strip())
            if m:
                ticket.deps = set(ID_TOKEN_RE.findall(m.group(1)))
                break

    # ---- 「## 约束」：基线 ----
    constraint = get_section(sections, "约束")
    if constraint is None:
        issues.append(
            {"kind": "missing-section", "ticket": tid, "detail": "缺少「## 约束」小节"}
        )
    else:
        ticket.constraint_section = True
        for lineno, line in constraint:
            m = BASE_RE.match(line.strip())
            if m:
                value = extract_value(m.group(1))
                if not value:
                    issues.append(
                        {
                            "kind": "parse-error",
                            "ticket": tid,
                            "detail": f"{path.name}:{lineno} 「基线」取值为空，无法解析",
                        }
                    )
                else:
                    ticket.baseline = value
                break

    return ticket, issues


def to_str_list(value: object) -> list[str] | None:
    """把 TOML 字段转成字符串列表；形态非法时返回 None。"""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    return None


def load_dag(path: Path) -> tuple[list[dict], list[dict]]:
    """读取 DAG；返回 (任务列表, 问题列表)。文件级错误以退出码 2 终止。"""
    if tomllib is None:  # pragma: no cover
        die("需要 Python 3.11+（tomllib）。")
    try:
        # utf-8-sig：兼容带/不带 BOM 的 TOML。
        # 注意：tomllib.load(binary) 不会剥掉 BOM，会报 "Invalid statement"，故走 loads。
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        die(f"DAG 不是合法 TOML：{exc}")
    except UnicodeDecodeError as exc:
        die(f"DAG 不是合法 UTF-8 文本：{exc}")
    except OSError as exc:
        die(f"DAG 读取失败：{exc}")

    raw_tasks = data.get("tasks", [])
    if raw_tasks is None:
        raw_tasks = []
    if not isinstance(raw_tasks, list):
        die("DAG 的 tasks 不是数组。")

    issues: list[dict] = []
    tasks: list[dict] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_tasks, 1):
        if not isinstance(raw, dict):
            issues.append(
                {"kind": "parse-error", "ticket": "", "detail": f"DAG 第 {i} 个 [[tasks]] 不是表"}
            )
            continue
        tid = raw.get("id")
        if not isinstance(tid, str) or not tid.strip():
            issues.append(
                {"kind": "parse-error", "ticket": "", "detail": f"DAG 第 {i} 个 [[tasks]] 缺少 id"}
            )
            continue
        tid = tid.strip()
        if tid in seen:
            issues.append(
                {"kind": "parse-error", "ticket": tid, "detail": f"DAG 中任务 id 重复：{tid}"}
            )
            continue
        seen.add(tid)

        files = to_str_list(raw.get("files"))
        if files is None:
            issues.append(
                {
                    "kind": "parse-error",
                    "ticket": tid,
                    "detail": f"DAG 任务 {tid} 的 files 不是字符串或字符串数组",
                }
            )
            files = []
        deps = to_str_list(raw.get("depends_on"))
        if deps is None:
            issues.append(
                {
                    "kind": "parse-error",
                    "ticket": tid,
                    "detail": f"DAG 任务 {tid} 的 depends_on 不是字符串或字符串数组",
                }
            )
            deps = []
        base = raw.get("base_branch")
        if base is None:
            base = DEFAULT_BASE
        elif not isinstance(base, str):
            issues.append(
                {
                    "kind": "parse-error",
                    "ticket": tid,
                    "detail": f"DAG 任务 {tid} 的 base_branch 不是字符串",
                }
            )
            base = DEFAULT_BASE
        elif not base.strip():
            base = DEFAULT_BASE

        tasks.append(
            {
                "id": tid,
                "files": {normalize_path(f) for f in files},
                "deps": set(deps),
                "base": base.strip(),
            }
        )
    return tasks, issues


def compare(tickets: list[Ticket], tasks: list[dict]) -> list[dict]:
    """双向比对工单与 DAG，产出不一致清单。"""
    issues: list[dict] = []
    ticket_ids = {t.tid for t in tickets}
    dag_ids = {t["id"] for t in tasks}
    by_id = {t["id"]: t for t in tasks}

    # ① 单号集合
    for tid in sorted(ticket_ids - dag_ids):
        issues.append(
            {
                "kind": "ticket-only",
                "ticket": tid,
                "detail": "单号集合：仅工单有，DAG 中没有对应任务",
            }
        )
    for tid in sorted(dag_ids - ticket_ids):
        issues.append(
            {
                "kind": "dag-only",
                "ticket": tid,
                "detail": "单号集合：仅 DAG 有，工单目录中没有对应工单",
            }
        )

    for ticket in sorted(tickets, key=lambda t: t.tid):
        task = by_id.get(ticket.tid)
        if task is None:
            continue  # 单号集合问题已单独报出，此处不重复比对

        # ② 归属文件集
        if ticket.owner_section and ticket.owner_marker:
            only_ticket = ticket.files - task["files"]
            only_dag = task["files"] - ticket.files
            if only_ticket and only_dag:
                issues.append(
                    {
                        "kind": "mismatch",
                        "ticket": ticket.tid,
                        "detail": (
                            "归属文件集不一致："
                            f"仅工单有 [{fmt_set(only_ticket)}]；仅 DAG 有 [{fmt_set(only_dag)}]"
                        ),
                    }
                )
            elif only_ticket:
                issues.append(
                    {
                        "kind": "ticket-only",
                        "ticket": ticket.tid,
                        "detail": f"归属文件集：仅工单有 [{fmt_set(only_ticket)}]（DAG 未列）",
                    }
                )
            elif only_dag:
                issues.append(
                    {
                        "kind": "dag-only",
                        "ticket": ticket.tid,
                        "detail": f"归属文件集：仅 DAG 有 [{fmt_set(only_dag)}]（工单未列）",
                    }
                )

        # ③ 依赖
        if ticket.owner_section:
            only_ticket = ticket.deps - task["deps"]
            only_dag = task["deps"] - ticket.deps
            if only_ticket or only_dag:
                issues.append(
                    {
                        "kind": "mismatch",
                        "ticket": ticket.tid,
                        "detail": (
                            "依赖不一致："
                            f"仅工单有 [{fmt_set(only_ticket)}]；仅 DAG 有 [{fmt_set(only_dag)}]"
                        ),
                    }
                )

        # ④ 基线
        if ticket.constraint_section and ticket.baseline is not None:
            if ticket.baseline != task["base"]:
                issues.append(
                    {
                        "kind": "mismatch",
                        "ticket": ticket.tid,
                        "detail": f"基线不一致：工单为 '{ticket.baseline}'，DAG 为 '{task['base']}'",
                    }
                )

    return issues


def summarize(issues: list[dict]) -> dict[str, int]:
    summary = {kind: 0 for kind in ISSUE_KINDS}
    for issue in issues:
        kind = issue.get("kind", "")
        summary[kind] = summary.get(kind, 0) + 1
    return summary


def main() -> int:
    # stdout / stderr 统一 UTF-8，避免 Windows 管道按 GBK 解码导致中文乱码
    # （见 references/08-orca-cli.md 坑 2）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        description="工单 ↔ 编排 DAG 一致性检查（只读）",
        epilog="退出码：0 完全一致；1 存在不一致；2 参数或读取错误。",
    )
    ap.add_argument("--tickets", required=True, help="工单目录（扫描其中所有 *.md）")
    ap.add_argument("--dag", required=True, help="orchestration.toml 路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    tickets_dir = Path(args.tickets)
    if not tickets_dir.exists():
        die(f"工单目录不存在：{tickets_dir}")
    if not tickets_dir.is_dir():
        die(f"工单路径不是目录：{tickets_dir}")
    tickets_dir = tickets_dir.resolve()

    dag_path = Path(args.dag)
    if not dag_path.exists():
        die(f"DAG 文件不存在：{dag_path}")
    if not dag_path.is_file():
        die(f"DAG 路径不是文件：{dag_path}")
    dag_path = dag_path.resolve()

    issues: list[dict] = []
    tickets: list[Ticket] = []
    for path in sorted(
        p for p in tickets_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".md"
    ):
        ticket, ticket_issues = parse_ticket(path)
        issues.extend(ticket_issues)
        if ticket is not None:
            tickets.append(ticket)

    tasks, dag_issues = load_dag(dag_path)
    issues.extend(dag_issues)
    issues.extend(compare(tickets, tasks))

    result = {
        "ok": not issues,
        "tickets_dir": str(tickets_dir),
        "dag": str(dag_path),
        "checked_tickets": len(tickets),
        "checked_tasks": len(tasks),
        "issues": issues,
        "summary": summarize(issues),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"工单目录：{tickets_dir}")
        print(f"DAG：{dag_path}")
        print(f"工单 {len(tickets)} 张，DAG 任务 {len(tasks)} 个")
        print()
        if issues:
            for issue in issues:
                label = issue.get("ticket") or "-"
                print(f"[{issue['kind']}] {label}: {issue['detail']}")
            print()
            print(f"结论：不一致（{len(issues)} 项）")
        else:
            print("结论：一致")

    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
