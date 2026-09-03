"""
Single async SQLAlchemy engine + session factory, shared by retrieval.py
and ingest.py. Created once at import time, same lazy-singleton spirit as
_get_model() in embed.py.

DATABASE_URL in .env stays in its current psycopg2-style form
(postgresql://...?sslmode=require) — we don't force you to rewrite it.
This module swaps the driver at the SQLAlchemy layer and translates
psycopg2-only URL params (sslmode) into what asyncpg actually accepts.
"""

import os
from urllib.parse import urlparse, parse_qs, urlunparse

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from dotenv import load_dotenv
load_dotenv(override=True)

_raw_url = os.environ["DATABASE_URL"]

# asyncpg needs its own driver prefix — swap psycopg2-style postgresql://
# for postgresql+asyncpg:// without making you touch the .env file.
if _raw_url.startswith("postgresql://"):
    _async_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _raw_url.startswith("postgres://"):
    _async_url = _raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
else:
    _async_url = _raw_url

# asyncpg's connect() doesn't accept `sslmode` as a kwarg — it's a
# psycopg2/libpq-ism. Bug found running this against a real Neon DB:
# TypeError: connect() got an unexpected keyword argument 'sslmode'.
# Fix: strip it from the URL query string and pass the equivalent as
# connect_args, which is how SQLAlchemy forwards driver-specific options.
_parsed = urlparse(_async_url)
_query = parse_qs(_parsed.query)
_sslmode = _query.pop("sslmode", [None])[0]
ASYNC_DB_URL = urlunparse(_parsed._replace(query=""))

_connect_args = {}
if _sslmode in ("require", "verify-ca", "verify-full"):
    _connect_args["ssl"] = True

engine = create_async_engine(
    ASYNC_DB_URL, pool_size=5, max_overflow=10, connect_args=_connect_args
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)