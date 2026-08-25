"""
Full ingestion: walk a repo, chunk every .py file, embed each chunk,
insert into Postgres.
"""

import sys
import os
import psycopg2

from run_on_repo import find_py_files
from chunker import chunk_file
from embed import embed_chunks

from dotenv import load_dotenv
load_dotenv(override=True)

DB_URL = os.environ["DATABASE_URL"]


def ingest(repo_root: str, repo_id: str):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    inserted, skipped, failed = 0, 0, 0

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
                cur.execute(
                    """
                    INSERT INTO chunks
                        (repo_id, file_path, symbol_name, symbol_type,
                         start_line, end_line, docstring, content,
                         is_trivial, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT ON CONSTRAINT uq_chunks_identity DO NOTHING
                    """,
                    (
                        repo_id, c.file_path, c.symbol_name, c.symbol_type,
                        c.start_line, c.end_line, c.docstring, c.content,
                        c.is_trivial, embedding,
                    ),
                )

                if cur.rowcount == 1:
                    inserted += 1
                else:
                    skipped += 1

            except Exception as e:
                conn.rollback()
                failed += 1
                print(f"  INSERT FAIL {path} :: {c.symbol_name}: {e}")
                continue

    conn.commit()
    cur.close()
    conn.close()

    print(
        f"\nInserted: {inserted}, "
        f"skipped (dupes): {skipped}, "
        f"failed: {failed}"
    )


if __name__ == "__main__":
    ingest(sys.argv[1], sys.argv[2])