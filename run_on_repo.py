"""
Run chunker.py across an entire repo directory. Logs:
- files that fail to parse
- empty functions (docstring-only or `pass`-only bodies)
- unusually large single functions (by line count)
"""

import sys
import os
from chunker import chunk_file

LARGE_FN_THRESHOLD = 50  # lines — flag anything bigger for manual review


def find_py_files(root):
    for dirpath, _, filenames in os.walk(root):
        if ".git" in dirpath:
            continue
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def main(root):
    total_chunks = 0
    parse_failures = []
    empty_bodies = []
    large_functions = []

    for path in find_py_files(root):
        try:
            chunks = chunk_file(path)
        except SyntaxError as e:
            parse_failures.append((path, str(e)))
            continue
        except Exception as e:
            parse_failures.append((path, f"{type(e).__name__}: {e}"))
            continue

        for c in chunks:
            total_chunks += 1
            line_count = c.end_line - c.start_line + 1

            if c.is_trivial:
                empty_bodies.append((c.file_path, c.symbol_name))

            # Only flag functions/methods for "large" — a class chunk is
            # naturally long because it includes every method's lines,
            # so line count there isn't a meaningful signal of complexity.
            if line_count > LARGE_FN_THRESHOLD and c.symbol_type in ("function", "method"):
                large_functions.append((c.file_path, c.symbol_name, line_count))

    print(f"Total chunks extracted: {total_chunks}\n")

    print(f"Parse failures: {len(parse_failures)}")
    for path, err in parse_failures:
        print(f"  {path}: {err}")

    print(f"\nSuspiciously empty bodies: {len(empty_bodies)}")
    for path, name in empty_bodies:
        print(f"  {path} :: {name}")

    print(f"\nLarge chunks (> {LARGE_FN_THRESHOLD} lines): {len(large_functions)}")
    for path, name, lc in sorted(large_functions, key=lambda x: -x[2]):
        print(f"  {path} :: {name} ({lc} lines)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
