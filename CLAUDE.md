# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A RAG (retrieval-augmented generation) app that answers natural-language questions about a Python codebase, grounded in actual retrieved source. Pipeline: `POST /ingest` clones a repo and AST-chunks every `.py` file into functions/classes/methods/module blocks, embeds each chunk (FastEmbed), and stores them in Postgres+pgvector. `POST /ask` embeds the question, runs hybrid retrieval (exact symbol-name match + pgvector semantic search, merged via Reciprocal Rank Fusion with exact-match pinning), sends the retrieved chunks to an LLM (Groq by default, Bedrock as an alternate per-request provider), and strips any citation the model invented that doesn't match a chunk actually retrieved. The same retrieval layer is separately exposed as a standalone MCP tool, and separately again wrapped in a LangGraph tool-calling agent (reasoner/critic/finalize) with a citation-retry-and-human-review loop — **the LangGraph agent is not wired into the FastAPI app** (see "Not implemented / not live" below).

There is no ORM layer — no `models.py`, no declarative `Base` classes anywhere. The `chunks` table schema lives only in the single Alembic migration (`alembic/versions/0501014deeca_create_chunks_table.py`); every query in `retrieval.py` and `ingest.py` is raw `sqlalchemy.text()` SQL against literal column names.

### Key file map

| File | Role |
|---|---|
| `app.py` | FastAPI app. Only three routes exist: `GET /`, `POST /ingest`, `POST /ask`. Does not import `graph.py`. |
| `db.py` | Single module-level async engine/session (asyncpg), created once at import. Translates `sslmode` out of `DATABASE_URL` into `connect_args`. |
| `chunker.py` | Pure-AST chunker. Top-level `def`/`class` only (no nested-function handling). Classes produce a class chunk *and* one chunk per method (deliberate overlap). Non-claimed top-level lines become one synthetic `<module>` chunk. |
| `embed.py` / `embed_stub.py` | Real embeddings (FastEmbed, 384-dim) vs. deterministic hash-based stub, switched by `EMBEDDINGS_PROVIDER=stub`. |
| `ingest.py` | `find_py_files` → `chunk_file` → `embed_chunks` → insert with `ON CONFLICT ON CONSTRAINT uq_chunks_identity DO NOTHING`. |
| `retrieval.py` | `hybrid_search()`: exact-match tiers + pgvector KNN + tier-1 exact-match pinning + RRF merge (`_rrf_merge`, explicit tiebreak — see below). |
| `validate_citations.py` | `parse_citations` / `validate_citations` (structured report, test-only) / `strip_invalid_citations` (the one actually used in production code paths). |
| `generate.py` | Builds the LLM prompt, calls Groq or Bedrock, then a single `strip_invalid_citations` pass. No retry. |
| `mcp_server.py` | `FastMCP` (mcp 1.x API) stdio server, one tool: `query_codebase`. Independent process from `app.py`. |
| `graph.py` | LangGraph `reasoner ↔ tools → critic → finalize` agent. `finalize` has the real citation retry/interrupt loop. Not reachable from any FastAPI route. |
| `checkpointer.py` | `AsyncPostgresSaver` singleton backing `graph.py`'s checkpointing. |
| `tests/conftest.py` | Session-scoped real Postgres (`codeqa_test`) migrated via the real Alembic revision, not a hand-rolled schema. |

## Tech stack and versions that matter

- **Python 3.11.** FastAPI 0.141.1, Uvicorn.
- **Three different Postgres drivers, deliberately, not accidentally**: `asyncpg` 0.31.0 via async SQLAlchemy Core 2.0.52 (`db.py`, the app's actual query path) · `psycopg2-binary` (Alembic migrations, and `tests/conftest.py`'s sync `db_conn` fixture used to seed test data) · `psycopg[binary]` v3 (`checkpointer.py` only, via `langgraph-checkpoint-postgres`). Don't consolidate these onto one driver — each is there because the other two can't cleanly do that job (asyncpg has no sync mode Alembic needs; psycopg v3's pool/row-factory shape is what `AsyncPostgresSaver` expects).
- **pgvector** 0.5.0 Python bindings against a `pgvector/pgvector:pg16` Postgres (CI's service image). Vector column is `Vector(384)`. **No ANN index exists yet** — see "Not implemented" below.
- **Embeddings**: `fastembed` 0.8.0, model `BAAI/bge-small-en-v1.5`, 384 dimensions. `EMBEDDING_DIM` in `embed.py` must stay in lockstep with the migration's `Vector(384)` if either ever changes.
- **RRF**: `RRF_K = 60` in `retrieval.py`, the standard paper default. Score = Σ `1/(k + rank)` over every ranked list a chunk appears in.
- **MCP SDK is pinned to `mcp==1.30.0`, the 1.x `FastMCP` API** (`from mcp.server.fastmcp import FastMCP`) — deliberately, not because anyone forgot to upgrade. `langchain-mcp-adapters` (needed for `graph.py`'s tool-calling) hard-pins `mcp<2.0.0`, and a single venv can only have one `mcp` version installed. Do not "helpfully" migrate `mcp_server.py` to `mcp.server.mcpserver.MCPServer` (the 2.x API) — that will break `graph.py`'s import.
- `langgraph` (currently resolves to 1.2.11), `langchain-mcp-adapters` 0.3.2, `langgraph-checkpoint-postgres`, `langchain-groq` — all unpinned in `requirements.txt` except where noted.
- `groq==0.37.1` — pinned specifically because `langchain-groq` requires `groq<1.0.0`; a later `groq` alone would resolve fine but breaks once `langchain-groq` is also installed.
- LLM models: Groq `openai/gpt-oss-20b` (used identically by both `generate.py` and `graph.py`). Bedrock default `anthropic.claude-3-haiku-20240307-v1:0` via `converse()`, overridable with `BEDROCK_MODEL_ID`.
- Langfuse via the `@observe` decorator only — no explicit `Langfuse()` client instantiation anywhere; it reads `LANGFUSE_*` env vars on first decorated call.

## Real constraints to respect

### The RRF tiebreak rule (`retrieval.py::_rrf_merge`)

When two chunks land on exactly the same RRF score (e.g. one hits only the exact-match list at rank 3, another hits only the semantic list at rank 3 — both score `1/(60+3)`), the order is **not** arbitrary or insertion-order-dependent. The explicit rule, applied in this order:

1. **RRF score, descending** (primary, unchanged).
2. **Number of ranked lists the chunk appears in, descending** — a chunk both the exact-match and semantic lists agree on outranks one only a single list found, even at equal score.
3. **Best (lowest) individual rank the chunk achieved in any list, ascending.**
4. **`chunk_id`, ascending** — a pure determinism floor for the residual case where two chunks are identical on everything above.

This sits *below* tier-1 exact-match pinning in `hybrid_search()` — a true case-insensitive `symbol_name == query` match still wins outright and never enters this tiebreak; it only orders the RRF-merged remainder beneath the pinned block. **Don't reintroduce** a bare `sorted(scores.keys(), key=lambda cid: -scores[cid])` — that was the pre-fix version, and Python's stable sort meant ties silently fell back to dict-insertion order, which depended on which list (`remaining_exact` vs `semantic_ids`) happened to be passed first at the call site. Covered by `tests/test_retrieval.py::test_rrf_merge_tiebreak_*` (pure-function tests against `_rrf_merge` directly, no DB needed — `RRF_K` is monkeypatched to `0` there to get clean fractions for deliberate ties).

### Citation validator: what it guarantees, and two very different call sites

`validate_citations.py` matches each `[file_path:start-end]` tag against the exact `(file_path, start_line, end_line)` tuple of a chunk that was actually retrieved for that request — not a fuzzy or substring match. A citation that doesn't match is silently dropped from the answer text; the surrounding sentence is left intact (only the bracket tag is removed). Known, accepted tradeoff: a false claim whose citation gets stripped now reads exactly like a true, properly-cited one — there is no visual difference in the output.

Two call sites exist and behave very differently — don't assume one implies the other:

- **`/ask` (app.py → generate.py) — the only path wired into the live FastAPI app.** Single-pass: calls the LLM once, then `strip_invalid_citations()` once. No retry, no escalation, no human review.
- **`graph.py`'s `finalize` node — a real escalating retry loop, but not reachable from any route** (see below). 1st invalid citation → loops back to the reasoner with a corrective message and a fresh answer attempt. 2nd *consecutive* invalid citation → pauses via LangGraph's `interrupt()` for human review, backed by `AsyncPostgresSaver` so the pause survives a process restart. Resuming with `"approve"` delivers the (already-stripped) answer; any other resume value replaces it with a fixed "not approved for delivery" message.
- `mcp_server.py`'s `query_codebase` tool never calls either citation function — each returned chunk's `citation` field is built directly from that chunk's own DB row, so it's valid by construction (there's no LLM output inside that tool to check).

`validate_citations()` itself (the function returning a structured `{valid, invalid, has_hallucinated_citation}` report, as opposed to `strip_invalid_citations()`) is exercised only by `tests/test_citations.py` — nothing in the live request path calls it or surfaces that report to a caller.

### Known bugs already fixed — don't reintroduce

- **`_is_trivial_body` (chunker.py) must check the actual AST body shape**, not a line-count heuristic — a real one-liner like `return x + 1` must not be flagged trivial. A line-count heuristic previously misclassified it.
- **Never call `symbol_type_enum.create()` explicitly** before `op.create_table()` in the Alembic migration — SQLAlchemy's Postgres dialect auto-creates the Enum type as a side effect of `create_table()`; an explicit `.create()` collides with it (`DuplicateObject`).
- **Don't pass `sslmode` to asyncpg** as a URL param or kwarg — it's psycopg2/libpq-only and asyncpg rejects it outright. `db.py` already strips it from `DATABASE_URL` and translates it to `connect_args={"ssl": True}`; don't bypass that when touching `db.py`.
- **Don't use `:paramname::pgtype` inline-cast syntax inside SQLAlchemy `text()`** — its bind-param parser mis-splits the name at `::`, silently truncating `:repo_id::text` to a `repo_i` param and dropping the real value. Use `CAST(:repo_id AS text)` instead (already the pattern throughout `retrieval.py`).
- **Don't remove tier-1 exact-match pinning from `hybrid_search()`** in favor of "pure" RRF — testing showed pure RRF can let a weak-but-present match outrank a genuine exact symbol-name match.
- **`mcp_server.py` must stay on `mcp.server.fastmcp.FastMCP`** (mcp 1.x). Don't "upgrade" the import to `mcp.server.mcpserver.MCPServer` — see the MCP SDK version note above.
- **Don't remove the `asyncio_default_fixture_loop_scope`/`asyncio_default_test_loop_scope = "session"` pins in `tests/pytest.ini`** — this project's module-level async singletons (`db.py`'s engine, `graph.py`'s MCP client) don't survive pytest-asyncio's default per-test event loop; removing the pin reintroduces `InterfaceError: cannot perform operation: another operation is in progress`. Even with the pin in place, running the *entire* suite in one `pytest` invocation currently still fails `test_graph.py`'s async tests plus two DB-backed tests in `test_retrieval.py` with event-loop errors, while every affected file passes cleanly run in isolation — a known category of cross-module event-loop flakiness in this test setup (not something a given change necessarily caused; verify by running the affected file alone before assuming a regression).
- **On Windows, don't drop the `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())` call** at the top of `checkpointer.py` and `tests/conftest.py` — psycopg v3's async pool cannot run under `ProactorEventLoop`, which is the Windows default.
- **The reasoner's tool result (`graph.py::search_codebase`) must include the actual retrieved chunk content**, not just a count. A prior version's `ToolMessage` said only `"Found N results"`; the model correctly (and unhelpfully) reported it had no access to code it had technically "found."
- **Don't remove `MAX_SEARCHES` (`graph.py`, currently 5)** or rely on prompt wording alone to stop repeated searching — live testing showed the reasoner can issue the identical query multiple times in a row without a structural cap. Past the limit, tool-binding is dropped entirely so the model literally cannot call the tool again.

## Running things

**Tests** (from repo root, needs a real reachable Postgres with the `vector` extension available — `tests/conftest.py` creates/migrates `codeqa_test` and runs the actual Alembic revision, not a hand-rolled schema):
```bash
pytest tests/ -v
pytest tests/test_retrieval.py -v                      # one file
pytest tests/test_retrieval.py::test_name -v            # one test
```
`EMBEDDINGS_PROVIDER=stub` (already set in this repo's `.env`) avoids real network calls to huggingface.co for embeddings — CI sets the same. `test_live_agent.py` lives at the repo root (not under `tests/`) and is a separate, manually-run script that makes real Groq API calls against `graph.ask_agent` — it is not part of the pytest suite and costs a real API call to run.

**App locally**:
```bash
alembic upgrade head
uvicorn app:app --reload
```
UI at `http://127.0.0.1:8000/`, docs at `/docs`.

**MCP tool, independent of the FastAPI app/process**:
```bash
python mcp_server.py
```
Runs as its own stdio server process — it imports `retrieval.py`/`embed.py` directly but never touches `app.py`. Note `graph.py` also spawns its own copy of this same script as a subprocess (via `MultiServerMCPClient`) when the LangGraph agent runs a search — that's a separate, automatically-managed instance, not something you need to start yourself for the agent to work.

## Not implemented / not live

- **The LangGraph agent (`graph.py`) is not reachable through the FastAPI app.** `app.py` has exactly three routes (`/`, `/ingest`, `/ask`) and does not import `graph.py` anywhere. The only ways to invoke `ask_agent()` are a direct Python call, `tests/test_graph.py` (fully mocked LLM calls), or the standalone `test_live_agent.py` script (real Groq calls). There is no `/agent`-style endpoint despite the reasoner/critic/finalize pipeline being fully built and tested.
- **AWS Bedrock (`"provider": "bedrock"` on `/ask`) has never made a real call against live AWS.** It's verified against a mocked `boto3` client only, pending AWS account verification. Bedrock credentials are intentionally kept local-only (`.env`) and are not deployed to Render, since `/ask` has no auth check.
- **No ANN index (IVFFlat or otherwise) exists on `chunks.embedding`.** Only one Alembic migration exists; its own comment defers the index to a later migration once real embeddings exist. Semantic search currently runs as an exact/sequential `ORDER BY embedding <=> ...` scan, not an approximate/indexed lookup.
- **This repo's `.env` currently sets `EMBEDDINGS_PROVIDER=stub`**, which means local runs of the app itself (not just tests) currently produce deterministic hash-based pseudo-embeddings, not real semantic vectors, until that line is removed or overridden.
- **Python only.** `chunker.py` uses the `ast` module directly; no other language is parsed.
- **`/ingest` is fully synchronous end-to-end** — it holds the HTTP request open for the whole clone + chunk + embed + insert run. No background job or status endpoint exists.
- **No authenticated/private repo cloning** — `/ingest` runs a plain `git clone` of a public URL.
- **No auth on any route.** Anyone who can reach a deployed instance can trigger `/ingest` or invoke either LLM provider through `/ask`.
