"""
Embed chunks and store them in Postgres.

Real path: fastembed's default model (BAAI/bge-small-en-v1.5, confirmed
384-dim) downloads from huggingface.co on first run and caches locally
after that. This is the code you actually keep in your project.
"""

from fastembed import TextEmbedding
from chunker import Chunk

EMBEDDING_DIM = 384  # must match the migration's Vector(384) column


def embed_chunks(chunks: list[Chunk]) -> list[list[float]]:
    """
    Embed each chunk's docstring + content together. Concatenating them
    (rather than embedding content alone) means a well-documented function
    is findable by intent ("parse a config file") even if the code itself
    uses different words than the query.
    """
    model = TextEmbedding()  # downloads BAAI/bge-small-en-v1.5 on first run
    texts = [
        f"{c.docstring or ''}\n\n{c.content}"
        for c in chunks
    ]
    embeddings = list(model.embed(texts))
    return [e.tolist() for e in embeddings]
