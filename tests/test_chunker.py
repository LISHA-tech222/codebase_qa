import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunker import chunk_file

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.py")


def test_extracts_top_level_function():
    chunks = chunk_file(FIXTURE)
    fn = next(c for c in chunks if c.symbol_name == "retry")
    assert fn.symbol_type == "function"
    assert fn.docstring == "Retry a function up to `attempts` times."


def test_class_and_methods_both_produced():
    """Regression test for the design decision: classes AND their methods
    should each become their own chunk (deliberate overlap)."""
    chunks = chunk_file(FIXTURE)
    class_chunk = next(c for c in chunks if c.symbol_name == "Config" and c.symbol_type == "class")
    method_chunks = [c for c in chunks if c.symbol_type == "method" and c.symbol_name.startswith("Config.")]

    assert class_chunk is not None
    method_names = {c.symbol_name for c in method_chunks}
    assert method_names == {"Config.__init__", "Config.load", "Config.save"}
    # methods' lines should be a subset of the class's line range
    for m in method_chunks:
        assert class_chunk.start_line <= m.start_line
        assert m.end_line <= class_chunk.end_line


def test_module_level_code_captured_not_dropped():
    """Regression test: imports and constants must not silently vanish."""
    chunks = chunk_file(FIXTURE)
    module_chunk = next(c for c in chunks if c.symbol_type == "module")
    assert "MAX_RETRIES" in module_chunk.content
    assert "import json" in module_chunk.content


def test_trivial_body_detection_ignores_real_oneliners():
    """Regression test for bug log #1: a genuine one-line function with
    real logic must NOT be flagged as trivial."""
    chunks = chunk_file(FIXTURE)
    save = next(c for c in chunks if c.symbol_name == "Config.save")
    assert save.is_trivial is False


def test_trivial_body_detection_catches_pass_only():
    import ast
    from chunker import _is_trivial_body
    tree = ast.parse("def stub():\n    pass")
    fn_node = tree.body[0]
    assert _is_trivial_body(fn_node) is True


def test_parse_failure_raises_syntax_error(tmp_path):
    bad_file = tmp_path / "broken.py"
    bad_file.write_text("def f(:\n    pass")
    import pytest as pt
    with pt.raises(SyntaxError):
        chunk_file(str(bad_file))
