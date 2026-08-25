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

TEST_DB = "codeqa_test"
TEST_DB_URL = f"postgresql+psycopg2://codeqa_user:devpassword@localhost:5432/{TEST_DB}"
TEST_DB_URL_PSYCOPG = f"dbname={TEST_DB} user=codeqa_user password=devpassword host=localhost"
DEMO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    os.environ["PSYCOPG_DB_URL"] = TEST_DB_URL_PSYCOPG

    # Superuser-only steps (CREATE DATABASE, CREATE EXTENSION) are run via
    # `psql` as the postgres OS user (peer auth), matching how we've done
    # this manually throughout the project — avoids fighting TCP superuser
    # password auth from Python for something that's genuinely a one-time
    # admin action, not app logic.
    subprocess.run(
        ["su", "postgres", "-c", f"psql -c 'DROP DATABASE IF EXISTS {TEST_DB};'"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["su", "postgres", "-c", f"psql -c 'CREATE DATABASE {TEST_DB} OWNER codeqa_user;'"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["su", "postgres", "-c", f"psql -d {TEST_DB} -c 'CREATE EXTENSION IF NOT EXISTS vector;'"],
        check=True, capture_output=True, text=True,
    )

    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DB_URL
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=DEMO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Migration failed:\n{result.stdout}\n{result.stderr}")

    yield


@pytest.fixture
def db_conn():
    """A connection wrapped in a transaction that's rolled back after the test."""
    conn = psycopg2.connect(TEST_DB_URL_PSYCOPG)
    yield conn
    conn.rollback()
    conn.close()
