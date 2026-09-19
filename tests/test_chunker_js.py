import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunker import chunk_file, SUPPORTED_EXTENSIONS

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.js")


def test_chunk_file_dispatches_js_by_extension():
    """chunk_file() (the same public entry point used for .py) must route
    a .js file to the JS chunker without the caller needing to know that."""
    chunks = chunk_file(FIXTURE)
    assert any(c.symbol_name == "retry" for c in chunks)


def test_extracts_top_level_function_with_jsdoc():
    chunks = chunk_file(FIXTURE)
    fn = next(c for c in chunks if c.symbol_name == "retry")
    assert fn.symbol_type == "function"
    assert fn.docstring == "Retry a function up to `attempts` times."


def test_class_and_methods_both_produced():
    """Same deliberate overlap as the Python chunker: a class chunk AND
    one chunk per method."""
    chunks = chunk_file(FIXTURE)
    class_chunk = next(c for c in chunks if c.symbol_name == "Config" and c.symbol_type == "class")
    method_chunks = [c for c in chunks if c.symbol_type == "method" and c.symbol_name.startswith("Config.")]

    assert class_chunk is not None
    method_names = {c.symbol_name for c in method_chunks}
    assert method_names == {"Config.constructor", "Config.load", "Config.save"}
    for m in method_chunks:
        assert class_chunk.start_line <= m.start_line
        assert m.end_line <= class_chunk.end_line


def test_method_jsdoc_extracted():
    chunks = chunk_file(FIXTURE)
    load = next(c for c in chunks if c.symbol_name == "Config.load")
    assert load.docstring == "Load config from disk as JSON."


def test_arrow_function_assigned_to_const_is_a_function_chunk():
    chunks = chunk_file(FIXTURE)
    main = next(c for c in chunks if c.symbol_name == "main")
    assert main.symbol_type == "function"


def test_plain_const_is_not_treated_as_a_function_chunk():
    chunks = chunk_file(FIXTURE)
    assert not any(c.symbol_name == "MAX_RETRIES" for c in chunks)


def test_export_wrapper_unwrapped_and_trivial_body_detected():
    """Regression test for the JS analog of bug log #1: `export function
    notImplementedYet() { throw new Error(...) }` must (a) still be
    correctly named despite the `export` wrapper and (b) be flagged
    trivial for its NotImplementedError-style stub body."""
    chunks = chunk_file(FIXTURE)
    fn = next(c for c in chunks if c.symbol_name == "notImplementedYet")
    assert fn.is_trivial is True


def test_module_level_code_captured_not_dropped():
    chunks = chunk_file(FIXTURE)
    module_chunk = next(c for c in chunks if c.symbol_type == "module")
    assert "MAX_RETRIES" in module_chunk.content
    assert 'import fs from "fs"' in module_chunk.content


def test_jsdoc_comment_not_duplicated_into_module_chunk():
    """Regression test: a top-level JSDoc comment must be claimed by the
    declaration it documents, not leak a second time into the synthetic
    <module> chunk alongside being correctly extracted as that chunk's
    docstring (found by manually inspecting the module chunk's content
    against this exact fixture before this test existed)."""
    chunks = chunk_file(FIXTURE)
    module_chunk = next(c for c in chunks if c.symbol_type == "module")
    assert "Retry a function" not in module_chunk.content
    assert "Load config from disk" not in module_chunk.content


def test_js_is_in_supported_extensions():
    assert ".js" in SUPPORTED_EXTENSIONS


def test_unsupported_extension_raises():
    import pytest
    with pytest.raises(ValueError):
        chunk_file("whatever.rb")
