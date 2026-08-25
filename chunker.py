"""
Minimal AST chunker — demo version.

Walks a Python file's AST and extracts each top-level function and class
as its own chunk, with docstring + line range.

NOTE: this deliberately only handles top-level defs for now (not nested
methods inside classes) so you can see the raw shape before deciding
how to handle nesting yourself.
"""

import ast
import json
from dataclasses import dataclass, asdict


@dataclass
class Chunk:
    file_path: str
    symbol_name: str
    symbol_type: str  # "function" | "class" | "method" | "module"
    start_line: int
    end_line: int
    docstring: str | None
    content: str
    is_trivial: bool = False  # True for functions/methods with no real logic


def _slice(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1:end])


def _is_trivial_body(node) -> bool:
    """
    True if a function/method body has no real logic -- just a docstring,
    `pass`, `...`, or a single `raise NotImplementedError`.

    This checks the actual AST nodes rather than guessing from line count,
    so a genuine one-line function like `return x + 1` is NOT flagged,
    but a docstring-only body or a bare `pass` body IS flagged.
    """
    body = node.body

    # Strip a leading docstring — it doesn't count as "real" logic.
    if body and isinstance(body[0], ast.Expr) and isinstance(
        getattr(body[0], "value", None), ast.Constant
    ) and isinstance(body[0].value.value, str):
        body = body[1:]

    if not body:
        return True  # docstring-only or fully empty

    if len(body) == 1:
        stmt = body[0]
        if isinstance(stmt, ast.Pass):
            return True
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
            return True  # body is just `...`
        if isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call):
            if isinstance(stmt.exc.func, ast.Name) and stmt.exc.func.id == "NotImplementedError":
                return True
        if isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Name):
            if stmt.exc.id == "NotImplementedError":
                return True

    return False


def chunk_file(file_path: str) -> list[Chunk]:
    with open(file_path, "r") as f:
        source = f.read()

    tree = ast.parse(source, filename=file_path)
    lines = source.splitlines()
    chunks = []
    claimed_ranges = []  # (start, end) line ranges already covered

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start, end = node.lineno, node.end_lineno
            claimed_ranges.append((start, end))

            if isinstance(node, ast.ClassDef):
                # 1. Whole class as one chunk
                chunks.append(Chunk(
                    file_path=file_path,
                    symbol_name=node.name,
                    symbol_type="class",
                    start_line=start,
                    end_line=end,
                    docstring=ast.get_docstring(node),
                    content=_slice(lines, start, end),
                ))
                # 2. Each method as its own chunk too (deliberate overlap —
                # see design notes on why this trades storage for recall).
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        m_start, m_end = child.lineno, child.end_lineno
                        chunks.append(Chunk(
                            file_path=file_path,
                            symbol_name=f"{node.name}.{child.name}",
                            symbol_type="method",
                            start_line=m_start,
                            end_line=m_end,
                            docstring=ast.get_docstring(child),
                            content=_slice(lines, m_start, m_end),
                            is_trivial=_is_trivial_body(child),
                        ))
            else:
                chunks.append(Chunk(
                    file_path=file_path,
                    symbol_name=node.name,
                    symbol_type="function",
                    start_line=start,
                    end_line=end,
                    docstring=ast.get_docstring(node),
                    content=_slice(lines, start, end),
                    is_trivial=_is_trivial_body(node),
                ))

    # Synthetic module chunk: everything at top level NOT inside a
    # function/class (imports, constants, module docstring, etc.)
    module_line_nums = set(range(1, len(lines) + 1))
    for start, end in claimed_ranges:
        module_line_nums -= set(range(start, end + 1))

    if module_line_nums:
        sorted_nums = sorted(module_line_nums)
        module_content = "\n".join(lines[n - 1] for n in sorted_nums)
        if module_content.strip():
            chunks.append(Chunk(
                file_path=file_path,
                symbol_name="<module>",
                symbol_type="module",
                start_line=sorted_nums[0],
                end_line=sorted_nums[-1],
                docstring=ast.get_docstring(tree),
                content=module_content,
            ))

    return chunks


if __name__ == "__main__":
    chunks = chunk_file("sample.py")
    for c in chunks:
        print(json.dumps(asdict(c), indent=2))
        print("---")
