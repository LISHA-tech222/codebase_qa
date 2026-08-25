"""
Deterministic stub embedder used in tests and CI.

This is NOT semantically meaningful — it's a hash-based pseudo-vector,
not a real embedding model. It exists so tests and CI can exercise the
full ingestion + retrieval pipeline (including the pgvector column and
vector similarity queries) without needing network access to Hugging
Face or a real API key. See embed.py for the real embedding path used
in production ingestion.
"""

import hashlib
import struct

EMBEDDING_DIM = 384


def stub_embed(text: str) -> list[float]:
    vec = []
    seed = text.encode("utf-8")
    i = 0
    while len(vec) < EMBEDDING_DIM:
        h = hashlib.sha256(seed + str(i).encode()).digest()
        # unpack 8 floats (4 bytes each) per hash round
        for j in range(0, len(h) - 3, 4):
            if len(vec) >= EMBEDDING_DIM:
                break
            val = struct.unpack("f", h[j:j + 4])[0]
            if val == val and abs(val) < 1e6:  # filter NaN/inf from raw bytes
                vec.append(val)
        i += 1
    return vec[:EMBEDDING_DIM]
