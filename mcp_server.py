"""
MCP server exposing the codebase Q&A retrieval layer as a single tool:
query_codebase. Wraps the existing hybrid_search service layer directly
(retrieval.py, embed.py) — does not touch app.py or any existing
FastAPI route.

Transport: stdio (default for MCPServer.run()). Single tool, no
resources, per plan.

"citation-validated": each returned chunk's citation is constructed
directly from its own real DB row (file_path, start_line, end_line) —
not from LLM output — so validity is guaranteed by construction, not by
running validate_citations.py's LLM-answer checker (which has nothing
to check here, since this tool makes no LLM call itself). See BUGLOG /
master record for the full reasoning behind this decision.
"""

from pydantic import BaseModel
from mcp.server.mcpserver import MCPServer

from retrieval import hybrid_search
from embed import embed_query

mcp = MCPServer(name="codebase-qa", version="0.1.0")


class ValidatedChunk(BaseModel):
    file_path: str
    symbol_name: str
    symbol_type: str
    start_line: int
    end_line: int
    docstring: str | None
    content: str
    citation: str  # e.g. "utils.py:10-15" — built from this row's own data


@mcp.tool()
async def query_codebase(query: str, repo_id: str, top_k: int = 5) -> list[ValidatedChunk]:
    """
    Search a specific ingested repository's code using hybrid retrieval
    (exact symbol-name match + pgvector semantic search, RRF-merged).
    Returns the matching chunks, each with a citation label built
    directly from the chunk's own file_path and line range.
    """
    query_embedding = embed_query(query)
    results = await hybrid_search(query, query_embedding, repo_id=repo_id, top_k=top_k)

    return [
        ValidatedChunk(
            file_path=r[1],
            symbol_name=r[2],
            symbol_type=r[3],
            start_line=r[4],
            end_line=r[5],
            docstring=r[6],
            content=r[7],
            citation=f"{r[1]}:{r[4]}-{r[5]}",
        )
        for r in results
    ]


if __name__ == "__main__":
    mcp.run()