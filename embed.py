"""
Embed chunks and queries using fastembed.

The model is loaded lazily and reused for all embedding calls.

Step 4 addition: when mcp_server.py is spawned as a subprocess by an
MCP client (e.g. langchain-mcp-adapters' MultiServerMCPClient), the MCP
SDK deliberately strips the subprocess's environment down to HOME/PATH/
TERM plus whatever's explicitly passed -- there's no way for a parent
test process to monkeypatch this module's embed_query the way every
other test in this project does, since it's a genuinely separate OS
process. EMBEDDINGS_PROVIDER=stub lets a test pass that one env var
through the subprocess's env dict instead, matching the project's
existing pattern of env-driven config (BEDROCK_MODEL_ID, LANGFUSE_HOST).
"""

import os

from fastembed import TextEmbedding
from chunker import Chunk
from embed_stub import stub_embed

EMBEDDING_DIM = 384  # must match the migration's Vector(384) column

_model = None
_USE_STUB = os.environ.get("EMBEDDINGS_PROVIDER") == "stub"


def _get_model():
    """Load the embedding model once and reuse it."""
    global _model

    if _model is None:
        _model = TextEmbedding()

    return _model


def embed_chunks(chunks: list[Chunk]) -> list[list[float]]:
    """
    Embed each chunk's docstring + content together.
    """
    texts = [
        f"{c.docstring or ''}\n\n{c.content}"
        for c in chunks
    ]

    if _USE_STUB:
        return [stub_embed(t) for t in texts]

    embeddings = list(_get_model().embed(texts))
    return [e.tolist() for e in embeddings]


def embed_query(text: str) -> list[float]:
    """
    Embed a single search query.
    """
    if _USE_STUB:
        return stub_embed(text)

    embedding = next(_get_model().embed([text]))
    return embedding.tolist()