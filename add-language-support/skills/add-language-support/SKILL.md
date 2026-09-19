---
name: add-language-support
description: Add support for a new source-code language to the codebase_assistant ingestion pipeline (chunker.py's AST-based chunking, run_on_repo.py's file discovery, ingest.py, and requirements.txt). Use when asked to add parsing/chunking/ingestion support for a new language (e.g. "add JavaScript support", "make the chunker handle Go", "extend ingestion to TypeScript"), or to extend retrieval to a language it doesn't currently cover.
---

# Add language support

This project's chunking pipeline is currently Python-only (`chunker.py`, via
the stdlib `ast` module) plus JavaScript (`chunker_js.py`, via tree-sitter —
added as the reference implementation this skill is modeled on). This skill
is the repeatable procedure for adding another language, end-to-end: real
parsing, real DB rows, real retrieval — not just a chunker unit that never
gets proven against the rest of the system.

**Do not skip the verification steps.** A chunker that only passes its own
unit tests but was never actually ingested and retrieved through
`hybrid_search()` is not "done" — this project's own build log (BUGLOG.md)
exists specifically because "looks right" and "verified against the real
pipeline" have diverged before.

## 0. Confirm the schema needs nothing new

Read `alembic/versions/0501014deeca_create_chunks_table.py` first. The
`chunks` table's `symbol_type` enum (`function`/`class`/`method`/`module`)
and every other column are already language-agnostic — `file_path` and
`content` are plain text, not Python-specific. Adding a language should
**not** require a new migration. If you find yourself wanting a `language`
column or a new `symbol_type` value, stop and reconsider — it almost
certainly isn't needed (retrieval never filters or branches on language).

## 1. Pick a parsing strategy — verify it installs before committing to it

Prefer a `tree-sitter` grammar package if one exists for the target
language (`pip install --dry-run tree-sitter tree-sitter-<lang>` — check
this actually resolves to real wheels before writing any code against it,
the way this project checked network/dependency assumptions in BUGLOG #4
and #36 rather than assuming). If no maintained tree-sitter grammar exists,
fall back to a lightweight regex/brace-matching approach in the same spirit
as this project's own chunkers — both are explicitly "minimal AST chunker —
demo version," not production-grade parsers, so a proportionate fallback is
consistent with the project, not a compromise of it.

## 2. Inspect the real parse tree before writing extraction code

Do not guess node type names or field names from memory or from another
language's grammar. Different tree-sitter grammars use different node type
names for conceptually similar constructs (e.g. `class_declaration` vs
`class_definition` vs `class_specifier`). Before writing `chunker_<lang>.py`,
run something like:

```python
from tree_sitter import Language, Parser
import tree_sitter_<lang> as ts_lang

parser = Parser(Language(ts_lang.language()))
tree = parser.parse(b"<a small representative snippet with a function, a class+method, and a comment>")
for child in tree.root_node.children:
    print(child.type, child.start_point, child.end_point)
```

and actually read the output. When chunker_js.py was built this way, this
step caught two real things that would otherwise have been assumed wrong:
`tree_sitter.Node` has no `.sexp()` method in tree-sitter 0.26.0 (despite
that being a commonly-remembered API from older versions), and `class_body`
children include punctuation tokens (`{`, `}`) alongside `method_definition`
nodes, which must be filtered out explicitly rather than assumed absent.

## 3. Register the extension

In `chunker.py`:
- Add the new extension to `SUPPORTED_EXTENSIONS`.
- Add a branch to `chunk_file()`'s dispatcher that lazily imports your new
  module (lazy, not top-level — sidesteps a circular import back to
  `chunker.py` for the shared `Chunk` dataclass, and keeps the new
  language's parser package an optional dependency for anyone only ever
  chunking the languages they actually use).

`run_on_repo.py`'s `find_source_files()` walker reads `SUPPORTED_EXTENSIONS`
directly — it needs no separate edit. `ingest.py` calls `find_source_files()`
and `chunk_file()` generically — it needs no separate edit either. If you
find yourself editing either of those two files for a language addition,
that's a sign the extension point broke somewhere; fix the dispatch table
instead of special-casing the new language downstream.

## 4. Implement `chunker_<lang>.py`

Reuse the shared `Chunk` dataclass from `chunker.py` (`from chunker import
Chunk`) rather than inventing a parallel shape. Implement `chunk_<lang>_file(file_path: str) -> list[Chunk]` covering, in the same spirit as the
existing chunkers:

- **Top-level functions** → `symbol_type="function"`.
- **Classes + their methods** → one `"class"` chunk for the whole class
  *and* one `"method"` chunk per method (deliberate overlap — matches both
  `chunker.py` and `chunker_js.py`; better recall for "how does X.method
  work" questions, at the cost of some duplicated content, an intentional,
  already-documented tradeoff in this project, not a new one to relitigate).
- **A docstring-equivalent, if the language has a real convention for one**
  (e.g. JSDoc `/** */` block comments for JS). If the language has no such
  convention, leave `docstring=None` rather than inventing one — and say so
  explicitly in the module's docstring, the way `chunker_js.py` documents
  that its module chunk's docstring is always `None`. Don't silently drop
  real per-symbol docstrings if the language *does* have a formal one
  (Python's case) — `embed.py` embeds `docstring + content` together, so a
  skipped docstring is a real, measurable retrieval-quality regression, not
  a cosmetic gap.
- **`is_trivial` based on actual body shape**, not line count or a text
  heuristic. This is not optional: BUGLOG #1 is the origin bug for this
  exact mistake (a line-count heuristic flagged a real one-liner as
  trivial). Check the real AST/tree-sitter nodes for "empty body" and
  "not-implemented-style stub" shapes specific to the target language.
- **A synthetic `<module>` chunk** covering every top-level line not
  claimed by a function/class/method chunk (imports, top-level constants,
  side-effecting statements) — computed the same way both existing
  chunkers do it: track `claimed_ranges` as you go, then subtract from the
  full set of line numbers.
  - **Known trap, found and fixed while building `chunker_js.py`:** a
    doc-comment (e.g. JSDoc) immediately preceding a top-level declaration
    is a *separate sibling node* in the parse tree — its own line range is
    NOT part of the declaration node's `start`/`end`. If you extract it as
    a docstring but forget to also add its own line range to
    `claimed_ranges`, the exact same text leaks a second time into the
    `<module>` chunk. Whenever you successfully extract a doc-comment as a
    chunk's docstring, claim its line range too. Verify this concretely:
    print the `<module>` chunk's `content` for your fixture and confirm no
    docstring text you already extracted elsewhere appears in it.

## 5. Fixture + unit tests

Add `tests/fixtures/sample.<ext>` — mirror `tests/fixtures/sample.py`'s
shape (one top-level function with a docstring, one class with an
`__init__`-equivalent and two other methods, one module-level constant,
one deliberately trivial/not-implemented function) so the new language's
test suite is directly comparable to the Python one. Add
`tests/test_chunker_<lang>.py` mirroring `tests/test_chunker.py`'s tests:
top-level function + docstring extraction, class+methods overlap, module
chunk captures unclaimed lines *and does not duplicate anything already
extracted as a docstring*, trivial-body detection on a real stub, and
`chunk_file()` dispatch-by-extension.

## 6. Real end-to-end verification — not optional

Unit tests against the chunker alone are necessary but not sufficient.
Prove the new language works through the actual pipeline other code will
use it through:

1. Ingest the new fixture into the real test DB (`_insert_chunk`, same
   helper `tests/test_ingestion.py` already defines — reuse it, don't
   reinvent it).
2. Call `retrieval.hybrid_search()` against it with a query matching one
   of the new fixture's real symbol names, and confirm the returned row's
   `file_path`/`symbol_name`/line range are correct.
3. Build a citation label from that real row (`f"{file_path}:{start}-{end}"`)
   and run it through `validate_citations.strip_invalid_citations()` —
   confirm a genuine citation for the new language survives (isn't
   stripped as if it were fabricated). This is the concrete proof that
   citation validation doesn't secretly assume Python file paths.
4. If your project already has multiple languages, seed a fixture from
   each language with a *colliding* symbol name and confirm
   `hybrid_search()` returns results from both — proves retrieval doesn't
   silently prefer or drop a language when names collide across files.

Add these as real, permanent pytest tests (see `tests/test_ingestion_js.py`
for the pattern), not a throwaway script — they're regression coverage for
"did packaging/refactoring this language support break the real pipeline,"
which unit tests against the chunker alone cannot catch.

## 7. Dependencies

Add the new tree-sitter grammar package to `requirements.txt`, pinned to
the exact version you verified against in step 1/2 — this project pins
dependency versions precisely where a version mismatch has previously
caused real breakage (see BUGLOG #25, #36 for two examples of exactly
that), not out of general caution.

## 8. Log it

Add an entry to `BUGLOG.md` in the existing format (what broke → what was
assumed wrong → what was actually wrong → the fix), covering whatever you
actually found while implementing — a real grammar node-type surprise, a
leakage bug like the JSDoc one above, a dependency that didn't install
cleanly, or (if truly nothing went wrong) a short, honest note saying so
rather than inventing a struggle that didn't happen. This project's build
log is a genuine incident record, not a formality — don't pad it and don't
skip it.
