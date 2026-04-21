"""
Regression test: bot response strings must be in English.
Korean is only allowed in comments, docstrings, and sheet column keys
that must match the existing Google Sheets structure.

Run: python -m pytest tracker/test_no_korean_in_responses.py -v
  or: python tracker/test_no_korean_in_responses.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# Unicode Hangul syllable block: U+AC00–U+D7A3
_KOREAN_RANGE = (0xAC00, 0xD7A3)

# Sheet column keys that MUST stay Korean to match the existing Google Sheet structure.
# Changing these would break read/write without a corresponding sheet migration.
_ALLOWED_KOREAN_STRINGS = {
    "날짜/시간", "스페이스 이름", "스페이스 ID",
    "⚡ 5단어 요약", "원본 내용", "🔍 상세 컨텍스트", "미디어 링크",
    "우선순위", "담당자", "업무 내용", "상태",
    "진행중",   # sheet status value for active tasks
    "긴급",     # sheet priority badge
}

# Modules whose string literals are checked (response-generating code only).
_RESPONSE_MODULES = [
    Path(__file__).parent / "command_router.py",
    Path(__file__).parent / "llm_client.py",
    Path(__file__).parent / "task_tracker.py",
    Path(__file__).parent / "main.py",
    Path(__file__).parent.parent / "wiki" / "interceptor.py",
    Path(__file__).parent.parent / "wiki" / "validator.py",
    Path(__file__).parent.parent / "wiki" / "ai_client.py",
]


def _has_korean(s: str) -> bool:
    return any(_KOREAN_RANGE[0] <= ord(c) <= _KOREAN_RANGE[1] for c in s)


def _collect_string_literals(source: str) -> list[tuple[int, str]]:
    """Extract (line_no, value) for every string literal in the AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            results.append((node.lineno, node.value))
    return results


def _is_docstring(source_lines: list[str], lineno: int) -> bool:
    """Heuristic: the line just holds a triple-quoted string (module/func/class docstring)."""
    idx = lineno - 1
    if idx < 0 or idx >= len(source_lines):
        return False
    stripped = source_lines[idx].strip()
    return stripped.startswith('"""') or stripped.startswith("'''")


def check_module(path: Path) -> list[str]:
    """Return list of violation messages for a single module."""
    if not path.exists():
        return []

    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    violations: list[str] = []

    for lineno, value in _collect_string_literals(source):
        if not _has_korean(value):
            continue
        if value in _ALLOWED_KOREAN_STRINGS:
            continue
        if _is_docstring(source_lines, lineno):
            continue
        violations.append(f"  {path.name}:{lineno}: Korean in string literal: {value!r}")

    return violations


def test_no_korean_in_response_literals() -> None:
    all_violations: list[str] = []
    for module_path in _RESPONSE_MODULES:
        all_violations.extend(check_module(module_path))

    if all_violations:
        msg = "Korean string literals found in response modules:\n" + "\n".join(all_violations)
        raise AssertionError(msg)


if __name__ == "__main__":
    try:
        test_no_korean_in_response_literals()
        print("PASS — no Korean string literals in response modules")
        sys.exit(0)
    except AssertionError as e:
        print(f"FAIL\n{e}")
        sys.exit(1)
