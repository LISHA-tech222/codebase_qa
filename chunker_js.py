"""
JavaScript chunker — added via skills/add-language-support (see BUGLOG for
the verification trail). Same shape and same deliberate limits as
chunker.py's Python implementation: top-level declarations only, no nested
function handling, extracts docstring-equivalent + line range per chunk.

Parser: tree-sitter + tree-sitter-javascript. Node types/field names below
were confirmed directly against a real parse (tree_sitter 0.26.0,
tree_sitter_javascript 0.25.0) before being relied on here, not assumed
from memory -- e.g. tree_sitter.Node has no .sexp() in this version, and
class_body's real children include method_definition nodes alongside
punctuation tokens that must be filtered out.

What counts as a chunk (mirrors chunker.py's Python rules, JS-shaped):
- `function foo() {}`                          -> symbol_type "function"
- `class Foo { ... }`                          -> one "class" chunk (whole
  class) + one "method" chunk per method_definition in its body (Foo.bar),
  same deliberate overlap as the Python class+method chunks.
- `const foo = () => {}` / `= function () {}`  -> symbol_type "function"
  (only when the declarator's value is itself a function; a plain
  `const X = 42` is not treated as a function chunk).
- `export function foo() {}` / `export const foo = () => {}` /
  `export class Foo {}` / `export default function foo() {}` -- the
  `export`/`export default` wrapper is unwrapped first, then handled by
  the same rules above (the export keyword itself doesn't change what
  kind of chunk something becomes).
- Everything else at top level (imports, plain `const`/`let` values,
  side-effecting statements) is folded into one synthetic `<module>`
  chunk, same "nothing silently drops out of retrieval" rule as Python's
  module chunk.

Known, accepted gap (documented, not silently skipped): there is no JS
equivalent of Python's PEP 257 module docstring convention, so the
synthetic `<module>` chunk's docstring is always None for JS files --
unlike the Python chunker, which pulls `ast.get_docstring(tree)`.
"""

from tree_sitter import Language, Parser
import tree_sitter_javascript as tsjs

from chunker import Chunk

_parser = None


def _get_parser() -> Parser:
    """Lazy singleton, same pattern as embed.py's _get_model()."""
    global _parser
    if _parser is None:
        _parser = Parser(Language(tsjs.language()))
    return _parser


def _slice(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1:end])


def _line_range(node) -> tuple[int, int]:
    # tree-sitter's start_point/end_point rows are 0-indexed; this
    # project's Chunk schema (and chunker.py's Python side) uses
    # 1-indexed, inclusive start/end lines -- confirmed by direct parse
    # (a node opening on source line 6 reports start_point.row == 5).
    return node.start_point.row + 1, node.end_point.row + 1


def _extract_jsdoc(node) -> tuple[str | None, tuple[int, int] | None]:
    """
    JS has no formal docstring, but a `/** ... */` block comment
    immediately preceding a declaration is the real-world convention
    (JSDoc). Mirrors why chunker.py bothers extracting Python
    docstrings at all: embed.py embeds docstring + content together, so
    silently skipping this would degrade retrieval quality for exactly
    the chunks a human is most likely to have documented.

    Returns (docstring, comment_line_range). The caller must add
    comment_line_range to claimed_ranges when it's not None -- found by
    testing against a real fixture: without this, a top-level JSDoc
    comment's lines were never "claimed" by anything (the claimed range
    only ever covered the declaration itself, not the comment sitting
    just above it), so the exact same text leaked a second time into
    the synthetic <module> chunk alongside being correctly extracted as
    this chunk's docstring.
    """
    prev = node.prev_sibling
    if prev is None or prev.type != "comment":
        return None, None
    text = prev.text.decode("utf-8")
    if not text.startswith("/**"):
        return None, None
    comment_range = _line_range(prev)
    text = text.removeprefix("/**").removesuffix("*/")
    lines = [ln.strip().lstrip("*").strip() for ln in text.splitlines()]
    cleaned = "\n".join(ln for ln in lines if ln)
    return (cleaned or None), comment_range


def _is_trivial_js_body(body_node) -> bool:
    """
    True if a function/method body has no real logic -- an empty `{}`,
    or a single `throw new Error(...)` / `throw new Error()` stub (the
    JS analog of Python's `raise NotImplementedError`). Checks actual
    statement nodes, not line count, same principle as chunker.py's
    _is_trivial_body (see BUGLOG #1 for why line-count heuristics are
    the wrong tool here).
    """
    if body_node is None:
        return True
    statements = [c for c in body_node.children if c.type not in ("{", "}", "comment")]
    if not statements:
        return True
    if len(statements) == 1 and statements[0].type == "throw_statement":
        arg = statements[0].children[1] if len(statements[0].children) > 1 else None
        if arg is not None and arg.type == "new_expression":
            ctor = arg.child_by_field_name("constructor")
            if ctor is not None and ctor.text.decode("utf-8") == "Error":
                return True
    return False


def _unwrap_export(node):
    """`export function foo() {}` / `export default class Foo {}` wrap the
    real declaration one level deep. Unwrap it so the caller only ever
    has to match against function_declaration/class_declaration/
    lexical_declaration, not every export variant too."""
    if node.type in ("export_statement",):
        decl = node.child_by_field_name("declaration")
        if decl is not None:
            return decl
        # `export default <expr>` (no `declaration` field for some
        # default-export shapes) -- look for a function/class child directly.
        for child in node.children:
            if child.type in ("function_declaration", "class_declaration"):
                return child
    return node


def chunk_js_file(file_path: str) -> list[Chunk]:
    with open(file_path, "rb") as f:
        source_bytes = f.read()
    source_text = source_bytes.decode("utf-8")
    lines = source_text.splitlines()

    tree = _get_parser().parse(source_bytes)
    chunks: list[Chunk] = []
    claimed_ranges: list[tuple[int, int]] = []

    for raw_node in tree.root_node.children:
        node = _unwrap_export(raw_node)

        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue  # anonymous `export default function () {}` -- no symbol_name to key on
            start, end = _line_range(node)
            claimed_ranges.append((start, end))
            docstring, doc_range = _extract_jsdoc(raw_node)
            if doc_range is not None:
                claimed_ranges.append(doc_range)
            chunks.append(Chunk(
                file_path=file_path,
                symbol_name=name_node.text.decode("utf-8"),
                symbol_type="function",
                start_line=start,
                end_line=end,
                docstring=docstring,
                content=_slice(lines, start, end),
                is_trivial=_is_trivial_js_body(node.child_by_field_name("body")),
            ))

        elif node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            class_name = name_node.text.decode("utf-8")
            start, end = _line_range(node)
            claimed_ranges.append((start, end))
            docstring, doc_range = _extract_jsdoc(raw_node)
            if doc_range is not None:
                claimed_ranges.append(doc_range)
            chunks.append(Chunk(
                file_path=file_path,
                symbol_name=class_name,
                symbol_type="class",
                start_line=start,
                end_line=end,
                docstring=docstring,
                content=_slice(lines, start, end),
            ))
            body = node.child_by_field_name("body")
            if body is not None:
                for member in body.children:
                    if member.type != "method_definition":
                        continue
                    m_name_node = member.child_by_field_name("name")
                    if m_name_node is None:
                        continue
                    m_start, m_end = _line_range(member)
                    # No need to claim this method's JSDoc comment range
                    # separately -- it sits inside the class body, which
                    # the whole-class chunk above already claimed.
                    m_docstring, _ = _extract_jsdoc(member)
                    chunks.append(Chunk(
                        file_path=file_path,
                        symbol_name=f"{class_name}.{m_name_node.text.decode('utf-8')}",
                        symbol_type="method",
                        start_line=m_start,
                        end_line=m_end,
                        docstring=m_docstring,
                        content=_slice(lines, m_start, m_end),
                        is_trivial=_is_trivial_js_body(member.child_by_field_name("body")),
                    ))

        elif node.type in ("lexical_declaration", "variable_declaration"):
            found_function = False
            for declarator in node.children:
                if declarator.type != "variable_declarator":
                    continue
                value = declarator.child_by_field_name("value")
                if value is None or value.type not in ("arrow_function", "function_expression"):
                    continue
                name_node = declarator.child_by_field_name("name")
                if name_node is None:
                    continue
                found_function = True
                start, end = _line_range(node)  # whole `const x = ...;` statement, including the keyword
                claimed_ranges.append((start, end))
                docstring, doc_range = _extract_jsdoc(raw_node)
                if doc_range is not None:
                    claimed_ranges.append(doc_range)
                chunks.append(Chunk(
                    file_path=file_path,
                    symbol_name=name_node.text.decode("utf-8"),
                    symbol_type="function",
                    start_line=start,
                    end_line=end,
                    docstring=docstring,
                    content=_slice(lines, start, end),
                    is_trivial=_is_trivial_js_body(value.child_by_field_name("body")),
                ))
            # A plain `const X = 42;` (found_function stays False) is left
            # unclaimed on purpose -- it falls into the module chunk below,
            # same as Python's module-level constants.
            del found_function

    # Synthetic module chunk: every top-level line not already claimed by
    # a function/class/method-bearing declaration above (imports, plain
    # const/let values, side-effecting top-level statements).
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
                docstring=None,  # no JS equivalent of a module docstring -- see module note above
                content=module_content,
            ))

    return chunks
