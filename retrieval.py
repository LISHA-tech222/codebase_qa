"""
Hybrid retrieval: exact symbol-name match + semantic vector search,
combined via Reciprocal Rank Fusion (RRF).

RRF score for a chunk = sum over each ranked list it appears in of
    1 / (k + rank_in_that_list)
where rank is 1-indexed and k is a smoothing constant (60 is the
standard default from the original RRF paper — large enough that
being rank 1 vs rank 3 in one list doesn't completely dominate the
other list's contribution).

Why RRF over "exact always wins" or "weighted score blend":
- Exact-always-wins is too rigid — a semantically perfect match that
  isn't a literal substring of the symbol name would always lose to
  a mediocre exact match, even a match on an unrelated trivial chunk.
- A raw weighted blend (e.g. 0.5*cosine_sim + 0.5*exact_score) requires
  the two scores to be on comparable scales, which they aren't:
  cosine similarity and "match tier" are different units. RRF sidesteps
  this because it only cares about RANK POSITION within each list, not
  the raw score magnitude — so no scale-matching is needed.

Step 0 (async rework): DB access now goes through async SQLAlchemy Core
(db.py's engine), not psycopg2. RRF merge itself is pure Python/CPU —
no I/O, so it stays a plain sync function called from async code.
"""

from sqlalchemy import text

from db import async_session

RRF_K = 60


async def _exact_match_search(session, query: str, limit: int = 20) -> list[int]:
    """
    Tiered keyword/symbol-name match:
      tier 1: exact symbol name match
      tier 2: symbol name starts with query
      tier 3: symbol name contains query
      tier 4: query appears in content (fallback keyword search)
    Returns list of chunk ids in rank order (tier, then symbol_name).
    """
    result = await session.execute(
        text("""
            SELECT id,
                CASE
                    WHEN symbol_name = :q THEN 1
                    WHEN symbol_name ILIKE :q || '%' THEN 2
                    WHEN symbol_name ILIKE '%' || :q || '%' THEN 3
                    ELSE 4
                END AS tier
            FROM chunks
            WHERE symbol_name ILIKE '%' || :q || '%'
               OR content ILIKE '%' || :q || '%'
            ORDER BY tier, symbol_name
            LIMIT :limit
        """),
        {"q": query, "limit": limit},
    )
    return [row[0] for row in result.fetchall()]  # already rank-ordered


async def _semantic_search(session, query_embedding: list[float], limit: int = 20) -> list[int]:
    """Vector similarity search via pgvector cosine distance."""
    result = await session.execute(
        text("""
            SELECT id
            FROM chunks
            ORDER BY embedding <=> (:emb)::vector
            LIMIT :limit
        """),
        {"emb": str(query_embedding), "limit": limit},
    )
    return [row[0] for row in result.fetchall()]  # already rank-ordered


def _rrf_merge(*ranked_id_lists) -> list[int]:
    """Combine multiple rank-ordered id lists into one RRF-scored ranking."""
    scores: dict[int, float] = {}
    for ranked_ids in ranked_id_lists:
        for rank, chunk_id in enumerate(ranked_ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores.keys(), key=lambda cid: -scores[cid])


async def hybrid_search(query: str, query_embedding: list[float], top_k: int = 10):
    """
    Tier-1 exact matches (symbol_name == query, case-insensitive) are
    pinned to the top of the results, ahead of everything else. This is
    deliberate: a real, unambiguous exact-name match is a "hard" signal
    that shouldn't be probabilistically outranked by "soft" semantic
    similarity noise. Everything else (lower exact-match tiers + all
    semantic results) is merged below the pinned results via RRF.
    """
    async with async_session() as session:
        exact_ids = await _exact_match_search(session, query)
        semantic_ids = await _semantic_search(session, query_embedding)

        # Tier-1 pin: re-derive which ids were exact (case-insensitive) matches.
        pinned_result = await session.execute(
            text("SELECT id FROM chunks WHERE lower(symbol_name) = lower(:q)"),
            {"q": query},
        )
        pinned_ids = [row[0] for row in pinned_result.fetchall()]

        remaining_exact = [i for i in exact_ids if i not in pinned_ids]
        merged_rest = _rrf_merge(remaining_exact, semantic_ids)
        merged_rest = [i for i in merged_rest if i not in pinned_ids]

        merged_ids = (pinned_ids + merged_rest)[:top_k]

        if not merged_ids:
            return []

        rows_result = await session.execute(
            text("""
                SELECT id, file_path, symbol_name, symbol_type, start_line, end_line, docstring, content
                FROM chunks WHERE id = ANY(:ids)
            """),
            {"ids": merged_ids},
        )
        rows = {row[0]: row for row in rows_result.fetchall()}

    # preserve RRF rank order — the SQL ANY() query above doesn't guarantee it
    return [rows[cid] for cid in merged_ids if cid in rows]