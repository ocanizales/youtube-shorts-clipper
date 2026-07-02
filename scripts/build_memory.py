"""Generate PROJECT_MAP.md — a deterministic, no-LLM, no-network code map.

Part of the continuity protocol (see CLAUDE.md): a fresh Claude session reads
CLAUDE.md -> HANDOFF.md -> PROJECT_MAP.md and can resume cold.

Usage:  python scripts/build_memory.py
Output: PROJECT_MAP.md at the repo root (overwritten each run).
"""

from __future__ import annotations

import ast
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".ruff_cache", "data_cache", "logs", "state", "downloads", "clips",
    "uploads", "transcripts", "graphify-out", "build", "dist",
}


def signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [a.arg for a in fn.args.args if a.arg not in ("self", "cls")]
    if fn.args.vararg:
        args.append("*" + fn.args.vararg.arg)
    if fn.args.kwarg:
        args.append("**" + fn.args.kwarg.arg)
    prefix = "async " if isinstance(fn, ast.AsyncFunctionDef) else ""
    return f"{prefix}{fn.name}({', '.join(args)})"


def first_line(doc: str | None) -> str:
    return doc.strip().splitlines()[0] if doc else ""


def describe(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        return [f"  - (unparseable: {exc})"]
    lines: list[str] = []
    doc = first_line(ast.get_docstring(tree))
    if doc:
        lines.append(f"  - _{doc}_")
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            head = f"class {node.name}({bases})" if bases else f"class {node.name}"
            cdoc = first_line(ast.get_docstring(node))
            lines.append(f"  - **{head}**" + (f" — {cdoc}" if cdoc else ""))
            methods = [
                signature(m) for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not m.name.startswith("_")
            ]
            if methods:
                lines.append(f"    - {'; '.join(methods)}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                fdoc = first_line(ast.get_docstring(node))
                lines.append(f"  - `{signature(node)}`" + (f" — {fdoc}" if fdoc else ""))
    return lines


def main() -> int:
    py_files = sorted(
        p for p in ROOT.rglob("*.py")
        if not any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts)
    )
    out: list[str] = [
        f"# PROJECT_MAP — {ROOT.name}",
        f"_Generated {date.today()} by scripts/build_memory.py — do not edit by hand._",
        "",
    ]
    for p in py_files:
        rel = p.relative_to(ROOT).as_posix()
        out.append(f"## {rel}")
        out.extend(describe(p) or ["  - (empty)"])
        out.append("")
    reqs = sorted(ROOT.glob("requirements*.txt"))
    if reqs:
        out.append("## Dependencies")
        for r in reqs:
            deps = [
                ln.split("==")[0].split(">=")[0].strip()
                for ln in r.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")
            ]
            out.append(f"  - {r.name}: {', '.join(deps)}")
        out.append("")
    (ROOT / "PROJECT_MAP.md").write_text("\n".join(out), encoding="utf-8")
    print(f"PROJECT_MAP.md written: {len(py_files)} modules mapped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
