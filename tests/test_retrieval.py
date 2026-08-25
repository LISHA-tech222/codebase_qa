import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunker import chunk_file
from embed_stub import stub_embed
from tests.test_ingestion import _insert_chunk

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.py")


def _seed(db_conn):
    chunks = chunk_file(FIXTURE)
    cur = db_conn.cursor()
    for c in chunks:
        _insert_chunk(cur, c)
    db_conn.commit()  # hybrid_search opens its own connection, needs committed data
    return chunks


def test_exact_match_returns_correct_symbol(db_conn):
    _seed(db_conn)
    from retrieval import _exact_match_search
    cur = db_conn.cursor()
    ids = _exact_match_search(cur, "Config")
    cur.execute("SELECT symbol_name FROM chunks WHERE id = %s", (ids[0],))
    # tier-1 (exact) match should come first
    assert cur.fetchone()[0] == "Config"


def test_exact_match_pinned_above_noisy_semantic_result(db_conn):
    """Regression test for bug log #5: a true exact match must not be
    outranked by coincidental semantic noise. This directly reproduces
    the failure we found manually (AnsiToWin32 case) using the fixture's
    'Config' class instead."""
    _seed(db_conn)
    from retrieval import hybrid_search
    results = hybrid_search("Config", stub_embed("Config"))
    assert len(results) > 0
    top_symbol_name = results[0][2]
    assert top_symbol_name == "Config"


def test_hybrid_search_falls_back_to_semantic_when_no_exact_match(db_conn):
    _seed(db_conn)
    from retrieval import hybrid_search
    results = hybrid_search("qwertyzzznotarealsymbol", stub_embed("qwertyzzznotarealsymbol"))
    # should not crash, should not error, may return semantic-only results
    assert isinstance(results, list)
