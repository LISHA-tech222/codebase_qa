"""
End-to-end verification that JS support (chunker_js.py) actually works
through the real pipeline, not just at the chunker-unit level: real
Postgres insert, then real hybrid_search() retrieval (exact-match tier +
RRF + citation-shape), exercising the exact same code path a live /ask
or MCP query_codebase call would use.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from chunker import chunk_file
from embed_stub import stub_embed
from tests.test_ingestion import _insert_chunk

PY_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.py")
JS_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.js")


def test_js_chunks_ingest_into_real_db(db_conn):
    chunks = chunk_file(JS_FIXTURE)
    cur = db_conn.cursor()
    for c in chunks:
        _insert_chunk(cur, c)
    db_conn.commit()

    cur.execute(
        "SELECT symbol_name, symbol_type FROM chunks WHERE repo_id = 'test_repo' AND file_path = %s",
        (JS_FIXTURE,),
    )
    rows = {name: t for name, t in cur.fetchall()}
    assert rows["retry"] == "function"
    assert rows["Config"] == "class"
    assert rows["Config.load"] == "method"


@pytest.mark.asyncio
async def test_hybrid_search_finds_js_symbol_with_real_citation(db_conn):
    """The retrieval layer (exact-match SQL, RRF merge, citation label
    construction) is entirely language-agnostic -- it operates on
    file_path/symbol_name/content text columns, never on file extension.
    This proves that's actually true, not just structurally likely."""
    chunks = chunk_file(JS_FIXTURE)
    cur = db_conn.cursor()
    for c in chunks:
        _insert_chunk(cur, c)
    db_conn.commit()

    from retrieval import hybrid_search
    results = await hybrid_search("notImplementedYet", stub_embed("notImplementedYet"))
    assert len(results) > 0
    top = results[0]
    assert top[2] == "notImplementedYet"  # symbol_name
    assert top[1] == JS_FIXTURE  # file_path

    from validate_citations import strip_invalid_citations
    citation = f"{top[1]}:{top[4]}-{top[5]}"
    answer = f"This stub isn't implemented yet [{citation}]."
    cleaned = strip_invalid_citations(answer, [
        {"file_path": r[1], "start_line": r[4], "end_line": r[5]} for r in results
    ])
    assert citation in cleaned  # a real JS chunk's citation survives validation


@pytest.mark.asyncio
async def test_hybrid_search_returns_both_languages_for_colliding_symbol_name(db_conn):
    """Both fixtures define a top-level `retry` function/method with the
    same name in different languages. Real multi-repo/multi-language
    ingestion could plausibly collide like this -- proves retrieval
    doesn't silently prefer one language or drop the other."""
    cur = db_conn.cursor()
    for c in chunk_file(PY_FIXTURE):
        _insert_chunk(cur, c)
    for c in chunk_file(JS_FIXTURE):
        _insert_chunk(cur, c)
    db_conn.commit()

    from retrieval import hybrid_search
    results = await hybrid_search("retry", stub_embed("retry"), top_k=10)
    file_paths = {r[1] for r in results if r[2] == "retry"}
    assert PY_FIXTURE in file_paths
    assert JS_FIXTURE in file_paths
