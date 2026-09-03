"""
Session-scoped: create a real 'codeqa_test' database (separate from the
dev 'codeqa' db that has real ingested colorama data) and run our actual
Alembic migration against it — same schema-creation path as production,
not a hand-rolled CREATE TABLE that could drift from the real migration.

Per-test: wrap each test in a transaction that's rolled back afterward,
so tests don't leak data into each other regardless of order.
"""

import os
import subprocess
import psycopg2
import pytest
from dotenv import load_dotenv
import pytest_asyncio

TEST_DB = "codeqa_test"
TEST_DB_URL = f"postgresql+psycopg2://codeqa_user:devpassword@localhost:5432/{TEST_DB}"
TEST_DB_URL_PSYCOPG = f"dbname={TEST_DB} user=codeqa_user password=devpassword host=localhost"
DEMO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    load_dotenv(override=True)
    os.environ["PSYCOPG_DB_URL"] = os.environ["DATABASE_URL"]
    yield
    # cleanup: remove any rows tests created, so they don't pollute real data
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute("DELETE FROM chunks WHERE repo_id = 'test_repo'")
    conn.commit()
    cur.close()
    conn.close()


@pytest.fixture
def db_conn():
    """A connection wrapped in a transaction that's rolled back after the test."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    yield conn
    conn.rollback()
    conn.close()

@pytest_asyncio.fixture
async def async_db_session():
    """
    Added for Step 0 (async rework): an async SQLAlchemy session for tests
    that exercise retrieval.py's async functions directly. Uses the same
    db.py engine the app itself uses, against the same DATABASE_URL as
    db_conn above — so data seeded via the sync db_conn/psycopg2 fixture
    (used for setup, since raw inserts are simpler there) is visible to
    queries made through this async session, same underlying database.
    """
    from db import async_session as _async_session_factory
    async with _async_session_factory() as session:
        yield session
