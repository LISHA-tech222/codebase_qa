"""
Full ingestion: walk a repo, chunk every .py file, embed each chunk,
insert into Postgres.

Step 0 (async rework): DB inserts go through async SQLAlchemy (db.py).
chunk_file() and embed_chunks() stay plain sync calls, called inline —
they're CPU-bound, not I/O-bound, so wrapping them in async buys
nothing (see build log / Step 0 decision entry for the full reasoning).
"""

import sys

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from run_on_repo import find_py_files
from chunker import chunk_file
from embed import embed_chunks
from db import async_session


async def ingest(repo_root: str, repo_id: str):
    inserted, skipped, failed = 0, 0, 0

    async with async_session() as session:
        for path in find_py_files(repo_root):
            try:
                chunks = chunk_file(path)
            except Exception as e:
                failed += 1
                print(f"  PARSE FAIL {path}: {e}")
                continue

            if not chunks:
                continue

            try:
                embeddings = embed_chunks(chunks)
            except Exception as e:
                failed += len(chunks)
                print(f"  EMBED FAIL {path}: {e}")
                continue

            for c, embedding in zip(chunks, embeddings):
                try:
                    result = await session.execute(
                        text("""
                            INSERT INTO chunks
                                (repo_id, file_path, symbol_name, symbol_type,
                                 start_line, end_line, docstring, content,
                                 is_trivial, embedding)
                            VALUES (:repo_id, :file_path, :symbol_name, :symbol_type,
                                    :start_line, :end_line, :docstring, :content,
                                    :is_trivial, :embedding)
                            ON CONFLICT ON CONSTRAINT uq_chunks_identity DO NOTHING
                        """),
                        {
                            "repo_id": repo_id,
                            "file_path": c.file_path,
                            "symbol_name": c.symbol_name,
                            "symbol_type": c.symbol_type,
                            "start_line": c.start_line,
                            "end_line": c.end_line,
                            "docstring": c.docstring,
                            "content": c.content,
                            "is_trivial": c.is_trivial,
                            "embedding": str(embedding),
                        },
                    )

                    if result.rowcount == 1:
                        inserted += 1
                    else:
                        skipped += 1

                except IntegrityError as e:
                    await session.rollback()
                    failed += 1
                    print(f"  INSERT FAIL {path} :: {c.symbol_name}: {e}")
                    continue

        await session.commit()

    print(
        f"\nInserted: {inserted}, "
        f"skipped (dupes): {skipped}, "
        f"failed: {failed}"
    )


if __name__ == "__main__":
    import asyncio
    asyncio.run(ingest(sys.argv[1], sys.argv[2]))