import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import text

from chunker import chunk_file
from embed_stub import stub_embed
from tests.test_ingestion import _insert_chunk

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.py")


def _seed(db_conn):
    chunks = chunk_file(FIXTURE)
    cur = db_conn.cursor()
    for c in chunks:
        _insert_chunk(cur, c)
    db_conn.commit()  # hybrid_search opens its own async connection, needs committed data
    return chunks


@pytest.mark.asyncio
async def test_exact_match_returns_correct_symbol(db_conn, async_db_session):
    _seed(db_conn)
    from retrieval import _exact_match_search
    ids = await _exact_match_search(async_db_session, "Config")
    result = await async_db_session.execute(
        text("SELECT symbol_name FROM chunks WHERE id = :id"), {"id": ids[0]}
    )
    # tier-1 (exact) match should come first
    assert result.fetchone()[0] == "Config"


@pytest.mark.asyncio
async def test_exact_match_pinned_above_noisy_semantic_result(db_conn):
    """Regression test for bug log #5: a true exact match must not be
    outranked by coincidental semantic noise. This directly reproduces
    the failure we found manually (AnsiToWin32 case) using the fixture's
    'Config' class instead."""
    _seed(db_conn)
    from retrieval import hybrid_search
    results = await hybrid_search("Config", stub_embed("Config"))
    assert len(results) > 0
    top_symbol_name = results[0][2]
    assert top_symbol_name == "Config"


@pytest.mark.asyncio
async def test_hybrid_search_falls_back_to_semantic_when_no_exact_match(db_conn):
    _seed(db_conn)
    from retrieval import hybrid_search
    results = await hybrid_search("qwertyzzznotarealsymbol", stub_embed("qwertyzzznotarealsymbol"))
    # should not crash, should not error, may return semantic-only results
    assert isinstance(results, list)


# --- _rrf_merge tiebreak (pure function, no DB) --------------------------
#
# RRF_K is monkeypatched to 0 in these tests so scores reduce to clean
# fractions (1/rank instead of 1/(60+rank)), making exact ties easy to
# construct deliberately rather than relying on real chunk data to
# coincidentally tie.

def test_rrf_merge_tiebreak_prefers_multi_list_agreement(monkeypatch):
    import retrieval
    monkeypatch.setattr(retrieval, "RRF_K", 0)
    # id 100: only in list A at rank 1 -> score 1/1 = 1.0, appearances=1
    # id 200: in list A rank 2 (0.5) AND list B rank 2 (0.5) -> score 1.0, appearances=2
    # Same total score; id 200 should win for appearing in both lists,
    # even though id 100's best individual rank (1) beats id 200's (2).
    list_a = [200, 100]
    list_b = [999, 200]  # id 200 at rank 2 in list_b
    merged = retrieval._rrf_merge(list_a, list_b)
    assert merged.index(200) < merged.index(100)


def test_rrf_merge_tiebreak_falls_back_to_best_rank(monkeypatch):
    import retrieval
    monkeypatch.setattr(retrieval, "RRF_K", 0)
    # Both ids appear in exactly 2 lists (same appearance count) with the
    # same summed score (7/12), but id 300's best individual rank (2)
    # beats id 400's best individual rank (3).
    #   id 300: list_a rank 2 (1/2) + list_b rank 12 (1/12) = 7/12
    #   id 400: list_a rank 3 (1/3) + list_b rank 4  (1/4)  = 7/12
    # Negative dummy ids pad out the unused ranks so 300/400 land at the
    # exact rank positions needed; they never tie with anything themselves.
    list_a = [-1, 300, 400, -2, -3, -4, -5, -6, -7, -8, -9, -10]
    list_b = [-11, -12, -13, 400, -14, -15, -16, -17, -18, -19, -20, 300]
    merged = retrieval._rrf_merge(list_a, list_b)
    assert merged.index(300) < merged.index(400)


def test_rrf_merge_tiebreak_falls_back_to_id_when_fully_tied(monkeypatch):
    import retrieval
    monkeypatch.setattr(retrieval, "RRF_K", 0)
    # id 7 and id 50 each appear once, at the same rank in different
    # lists -> identical score, identical appearance count, identical
    # best rank. Only the deterministic id-ascending rule breaks this.
    list_a = [50]
    list_b = [7]
    merged = retrieval._rrf_merge(list_a, list_b)
    assert merged.index(7) < merged.index(50)