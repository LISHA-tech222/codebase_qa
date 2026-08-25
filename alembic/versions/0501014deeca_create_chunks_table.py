"""create chunks table

Revision ID: 0501014deeca
Revises: 
Create Date: 2026-08-23 18:52:24.688614

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = '0501014deeca'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Chosen after Step 4 embedding model research: fastembed's default model
# (BAAI/bge-small-en-v1.5) outputs 384-dim vectors. If the embedding model
# changes later, this constant AND the column both need to change together.
EMBEDDING_DIM = 384

symbol_type_enum = sa.Enum(
    "function", "class", "method", "module",
    name="symbol_type",
)


def upgrade() -> None:
    """Upgrade schema."""
    # pgvector's CREATE EXTENSION was already run manually against the
    # dev DB, but doing it here too makes the migration reproducible
    # against a fresh database (e.g. in CI).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # NOTE: no explicit symbol_type_enum.create() call here. SQLAlchemy's
    # Postgres dialect auto-creates an Enum column's type as a side effect
    # of create_table() -- calling .create() ourselves first caused
    # "DuplicateObject: type symbol_type already exists". Downgrade()
    # still calls .drop() explicitly since create_table's teardown
    # doesn't imply the reverse.
    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("repo_id", sa.Text, nullable=False),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("symbol_name", sa.Text, nullable=False),
        sa.Column("symbol_type", symbol_type_enum, nullable=False),
        sa.Column("start_line", sa.Integer, nullable=False),
        sa.Column("end_line", sa.Integer, nullable=False),
        sa.Column("docstring", sa.Text, nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("is_trivial", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
    )

    # Prevents duplicate rows if the same commit of the same repo gets
    # ingested twice (re-deploy, webhook firing twice, etc.)
    op.create_unique_constraint(
        "uq_chunks_identity",
        "chunks",
        ["repo_id", "file_path", "symbol_name", "start_line"],
    )

    # Exact/keyword search path (Step 5) will filter/sort on these a lot.
    op.create_index("ix_chunks_repo_id", "chunks", ["repo_id"])
    op.create_index("ix_chunks_symbol_name", "chunks", ["symbol_name"])

    # IVFFlat index for approximate nearest-neighbor search on the semantic
    # path (Step 5). Deliberately NOT created here — see note in README:
    # IVFFlat needs representative data present to build good clusters, so
    # we add this index in a later migration after Step 4 has populated
    # real embeddings, rather than on an empty table.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_chunks_symbol_name", table_name="chunks")
    op.drop_index("ix_chunks_repo_id", table_name="chunks")
    op.drop_constraint("uq_chunks_identity", "chunks", type_="unique")
    op.drop_table("chunks")
    symbol_type_enum.drop(op.get_bind(), checkfirst=True)
