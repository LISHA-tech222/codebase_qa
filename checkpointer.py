"""
Postgres-backed checkpointer for the LangGraph orchestration (Step 4).

Chosen over MemorySaver: checkpoints must survive process restarts for
human-in-the-loop (plan item 4) to be genuinely resumable, not just
"resumable as long as the process happens to stay up." Uses psycopg
(v3) -- a THIRD Postgres driver in this project alongside psycopg2
(Alembic) and asyncpg (the app's main DB layer). Deliberate, not an
accident: each does a different job (sync migrations, async app
queries, checkpoint persistence with its own required connection
kwargs) and none of the three can cleanly do another's job.

Worth noting for its own sake: psycopg (built on libpq, like psycopg2)
accepts `sslmode` directly in the connection string with no
translation needed -- unlike db.py's asyncpg engine, which had to parse
`sslmode` out of the URL and pass it as a separate connect_arg (Step 0,
BUGLOG #3), because asyncpg reimplements the Postgres wire protocol
itself instead of wrapping libpq.
"""
"""
Postgres-backed checkpointer for the LangGraph orchestration (Step 4).
...
"""

import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
import os

from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from dotenv import load_dotenv
load_dotenv(override=True)

_pool: AsyncConnectionPool | None = None
_checkpointer: AsyncPostgresSaver | None = None


async def get_checkpointer() -> AsyncPostgresSaver:
    """
    Lazy singleton, same pattern as db.py's engine and graph.py's MCP
    client: one pool, .setup() called once (idempotent -- creates the
    checkpoint tables if they don't already exist), reused across every
    graph invocation rather than reopened per request.
    """
    global _pool, _checkpointer
    if _checkpointer is None:
        _pool = AsyncConnectionPool(
            conninfo=os.environ["DATABASE_URL"],
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await _pool.open()
        _checkpointer = AsyncPostgresSaver(_pool)
        await _checkpointer.setup()
    return _checkpointer