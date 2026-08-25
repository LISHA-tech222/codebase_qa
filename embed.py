"""
Embed chunks and queries using fastembed.

The model is loaded lazily and reused for all embedding calls.
"""

from fastembed import TextEmbedding
from chunker import Chunk

EMBEDDING_DIM = 384  # must match the migration's Vector(384) column

_model = None


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

    embeddings = list(_get_model().embed(texts))
    return [e.tolist() for e in embeddings]


def embed_query(text: str) -> list[float]:
    """
    Embed a single search query.
    """
    embedding = next(_get_model().embed([text]))
    return embedding.tolist()