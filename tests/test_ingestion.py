import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunker import chunk_file
from embed_stub import stub_embed  # deterministic test/CI embedder, see embed_stub.py

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.py")


def _insert_chunk(cur, c, repo_id="test_repo"):
    cur.execute(
        """
        INSERT INTO chunks
            (repo_id, file_path, symbol_name, symbol_type,
             start_line, end_line, docstring, content, is_trivial, embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT ON CONSTRAINT uq_chunks_identity DO NOTHING
        """,
        (repo_id, c.file_path, c.symbol_name, c.symbol_type,
         c.start_line, c.end_line, c.docstring, c.content,
         c.is_trivial, stub_embed(c.content)),
    )
    return cur.rowcount


def test_ingestion_inserts_all_chunks(db_conn):
    chunks = chunk_file(FIXTURE)
    cur = db_conn.cursor()
    for c in chunks:
        _insert_chunk(cur, c)

    cur.execute("SELECT count(*) FROM chunks WHERE repo_id = 'test_repo'")
    assert cur.fetchone()[0] == len(chunks)


def test_reingesting_same_repo_does_not_duplicate(db_conn):
    """Regression test: the uniqueness constraint (repo_id, file_path,
    symbol_name, start_line) must actually prevent duplicate rows on a
    re-run, not just exist unused in the schema."""
    chunks = chunk_file(FIXTURE)
    cur = db_conn.cursor()

    for c in chunks:
        _insert_chunk(cur, c)
    first_count_query = "SELECT count(*) FROM chunks WHERE repo_id = 'test_repo'"
    cur.execute(first_count_query)
    first_count = cur.fetchone()[0]

    # re-ingest the exact same file again
    rowcounts = [_insert_chunk(cur, c) for c in chunks]
    cur.execute(first_count_query)
    second_count = cur.fetchone()[0]

    assert second_count == first_count  # no new rows
    assert all(rc == 0 for rc in rowcounts)  # every insert was a no-op conflict


def test_symbol_type_enum_rejects_invalid_value(db_conn):
    cur = db_conn.cursor()
    import psycopg2
    with __import__("pytest").raises(psycopg2.errors.InvalidTextRepresentation):
        cur.execute(
            """
            INSERT INTO chunks
                (repo_id, file_path, symbol_name, symbol_type, start_line, end_line, content)
            VALUES ('t', 'x.py', 'foo', 'not_a_real_type', 1, 2, 'x')
            """
        )
