# Codebase Q&A Assistant

> **Ask natural-language questions about a Python codebase and get answers grounded in the actual source code.**

A production-ready RAG application that ingests Python repositories, retrieves relevant code using **hybrid exact + semantic search**, and generates source-grounded answers with **validated citations**.

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.141.1-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![pgvector](https://img.shields.io/badge/pgvector-vector%20search-336791)](https://github.com/pgvector/pgvector)
[![Docker](https://img.shields.io/badge/Docker-deployed-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Render](https://img.shields.io/badge/Render-production-46E3B7?logo=render&logoColor=white)](https://render.com/)
[![AWS](https://img.shields.io/badge/AWS-EC2%20%2B%20RDS-FF9900?logo=amazonaws&logoColor=white)](https://aws.amazon.com/)

---

## ✨ What It Does

Give the application a GitHub repository and ask questions such as:

> **"How does the configuration parser work?"**

or:

> **"What does `Config.load()` do?"**

The system doesn't simply ask an LLM to guess. It first retrieves relevant source-code chunks from the repository, then gives those chunks to the LLM as context.

The final response includes source locations such as:

```text
[config.py:42-67]
```

Citations are checked against the chunks that were actually retrieved instead of blindly trusting whatever citation the LLM generates.

---

## 🧠 Architecture

```text
                    GitHub Repository
                           │
                           ▼
                    POST /ingest
                           │
                           ▼
                    Git Clone + AST
                           │
                           ▼
                  Semantic Code Chunks
                           │
                           ▼
                    FastEmbed Model
                 BAAI/bge-small-en-v1.5
                           │
                           ▼
                 PostgreSQL + pgvector
                           │
                           │
                    User Question
                           │
                           ▼
                    embed_query()
                           │
                ┌──────────┴──────────┐
                │                     │
          Exact Symbol          Semantic Search
             Search              pgvector
                │                     │
                └──────────┬──────────┘
                           ▼
                  Exact Match Pinning
                           │
                           ▼
                  Reciprocal Rank Fusion
                           │
                           ▼
                    Top-K Code Chunks
                           │
                           ▼
                    Groq LLM
               openai/gpt-oss-20b
                           │
                           ▼
                 Citation Validation
                           │
                           ▼
                  Answer + Sources
                           │
                           ▼
                      Web UI
```

The retrieval layer above is also exposed directly as an MCP tool (`query_codebase`), so any MCP-compatible client — not just this web UI — can query the same hybrid search without going through the LLM-generation step. See [MCP Tool](#-mcp-tool) below.

---

## 🔍 RAG Pipeline

### 1. Ingestion

`chunker.py` walks Python ASTs and extracts meaningful code structures:

- functions
- classes
- methods
- module-level code

A synthetic `module` chunk preserves imports, constants, and other code outside functions/classes.

### 2. Embeddings

Each chunk embeds:

```text
docstring + source code
```

using:

```text
BAAI/bge-small-en-v1.5
384 dimensions
```

The model is lazily loaded and reused.

### 3. Storage

Chunks and embeddings are stored in PostgreSQL with pgvector, queried through async SQLAlchemy Core (`db.py`) and the asyncpg driver.

The project uses a repository-aware uniqueness constraint based on:

```text
(repo_id, file_path, symbol_name, start_line)
```

so re-ingesting a repository does not create duplicate chunks.

### 4. Retrieval

Retrieval combines:

- exact symbol-name matching
- keyword/content matching
- semantic vector similarity

True case-insensitive exact symbol matches are **pinned to the top**.

The remaining candidates are merged using **Reciprocal Rank Fusion (RRF)** with:

```text
k = 60
```

Retrieval can optionally be scoped to a single repository via `repo_id` (used by the MCP tool); the web UI's `/ask` endpoint leaves it unscoped, searching across all ingested repositories, matching its original behavior.

### 5. Generation

By default, the retrieved chunks are passed to:

```text
Groq (AsyncGroq)
openai/gpt-oss-20b
```

The model generates an answer grounded in the retrieved source.

AWS Bedrock is also supported as an alternate provider, selectable per-request (`"provider": "bedrock"` in the `/ask` request body) via Bedrock's unified `converse()` API — see [LLM Providers](#-llm-providers) below. Groq stays the default and the actual deployment path.

### 6. Citation validation

Generated citations are checked against the actual retrieved chunks.

A citation that does not correspond to retrieved source is removed rather than trusted. The same validation runs regardless of which LLM provider generated the answer.

---

## 🔀 LLM Providers

`/ask` supports two LLM providers, chosen per-request via an optional `provider` field (defaults to `"groq"` if omitted — existing callers are unaffected):

```json
{
  "question": "How does the configuration parser work?",
  "provider": "bedrock"
}
```

- **`groq`** (default) — Groq's free tier, `openai/gpt-oss-20b`, via `AsyncGroq`. This is the actual production path.
- **`bedrock`** — AWS Bedrock, via the unified `converse()` API (not the older, model-specific `invoke_model()` — one request shape works across Bedrock's model providers, so switching models is a config change). `boto3` has no official async client, so the call is offloaded with `asyncio.to_thread()` rather than awaited directly, to avoid blocking the event loop.

**Status:** code-complete and verified against a mocked AWS client (request assembly, response parsing, and citation validation all confirmed working correctly together) — a live call against real AWS has not yet been made, pending AWS account verification completing. Documented honestly as pending rather than claimed as done; see `BUGLOG.md` #20–21.

**Cost/security note:** AWS credentials are intentionally kept **local-only** (`.env`), not deployed to Render. `/ask` has no auth check, so live AWS credentials on a public endpoint would let anyone trigger real, repeated AWS charges. Without credentials in production, the Bedrock code path stays real and demonstrable, but fails safely at $0 cost if it's ever hit on the live deployment. A single local verification call is estimated at well under a cent (Claude 3 Haiku pricing; an even cheaper model, Amazon Nova Micro, is swappable via the `BEDROCK_MODEL_ID` env var with no code change).

---

## 🔌 MCP Tool

The hybrid retrieval layer is also exposed as a standalone [Model Context Protocol](https://modelcontextprotocol.io/) server (`mcp_server.py`), independent of the FastAPI app — it imports the same service layer (`retrieval.py`, `embed.py`) but does not touch `app.py` or any existing route.

**Tool:** `query_codebase(query: str, repo_id: str, top_k: int = 5) -> list[ValidatedChunk]`

- **Transport:** stdio
- **Single tool, no resources**
- Each returned chunk includes a `citation` field (`file_path:start_line-end_line`) built directly from its own database row — valid by construction, since this tool performs no LLM call to validate against.

Verified against three independent MCP clients: an in-process protocol test (`mcp.shared.memory` + `ClientSession`), the official [MCP Inspector](https://github.com/modelcontextprotocol/inspector), and Claude Desktop (via `mcpServers` in `claude_desktop_config.json`) against the live production database.

**Run it:**

```bash
python mcp_server.py
```

---

## 💡 Key Engineering Decisions

### AST-based chunking

Instead of splitting files by arbitrary line ranges, the system uses Python AST boundaries.

**Why?**

Functions, classes, and methods are semantic units of code. This makes retrieval more meaningful for code-specific questions.

### Module chunks

Module-level imports/constants are kept in a synthetic `module` chunk rather than dropped.

**Why?**

Important context should not silently disappear from the retrieval index.

**Tradeoff:** module chunks can have approximate line ranges because their source lines may be non-contiguous.

### Classes + methods

Classes are indexed both as whole chunks and as individual method chunks.

**Why?**

A whole class provides context while method chunks improve focused retrieval for questions such as:

```text
How does X.method work?
```

**Tradeoff:** this intentionally creates some content overlap.

### Hybrid retrieval

Exact matching is important for source code because identifiers are meaningful.

Semantic search is important because users may describe code without using the exact identifier.

Therefore:

```text
Exact matching + Semantic search
```

works better than either alone.

### Why RRF?

Exact-match scores and cosine-similarity scores are not naturally comparable.

Instead of inventing arbitrary weights, RRF combines ranked lists:

```text
RRF score = Σ 1 / (k + rank)
```

with `k = 60`.

### Why pin exact matches?

Testing showed that pure RRF could allow a mediocre result to outrank a true exact symbol match.

An exact identifier match is treated as a **hard signal**; semantic similarity is a **soft signal**.

So true exact matches are pinned above the RRF results.

### Citation validation

An LLM can produce a plausible-looking citation that was never retrieved.

The application therefore validates citations against the retrieved source rather than trusting the model's output.

### Async I/O, not async everything

Database queries and the Groq LLM call are I/O-bound, so they were converted to async (asyncpg/async SQLAlchemy, `AsyncGroq`). Embedding inference (FastEmbed) and AST parsing are CPU-bound — wrapping them in `async def` would add complexity with zero benefit, since `asyncio` concurrency only helps work that's waiting on I/O. They stayed plain sync calls, invoked inline.

### What "citation-validated" means with no LLM call inside a tool

`validate_citations.py` checks an LLM's *generated answer* against retrieved chunks — it has nothing to check inside a pure-retrieval MCP tool. Rather than calling the LLM inside `query_codebase` itself (which would duplicate `/ask` and add latency/cost to every tool call), each returned chunk's citation is built directly from its own database row, making it valid by construction.

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| API | FastAPI (async) |
| Server | Uvicorn |
| Database access | async SQLAlchemy Core + asyncpg |
| Parsing | Python AST |
| Embeddings | FastEmbed / BAAI/bge-small-en-v1.5 |
| Vector dimension | 384 |
| Database | PostgreSQL |
| Vector search | pgvector |
| Migrations | Alembic |
| LLM | Groq (AsyncGroq) / openai/gpt-oss-20b, default; AWS Bedrock (converse API) as alternate provider |
| Tool protocol | MCP (Model Context Protocol) |
| Frontend | HTML/CSS/JavaScript |
| Containerization | Docker |
| CI | GitHub Actions |
| Deployment (production) | Render |
| Deployment (infra exercise) | AWS EC2 + RDS |

FastAPI provides the API layer and automatic interactive API documentation; pgvector provides vector similarity search inside PostgreSQL. citeturn0search8turn0search0

---

## 📁 Project Structure

```text
codebase_assistant/
│
├── app.py                    # FastAPI API + web UI entry point (async routes)
├── db.py                     # Async SQLAlchemy engine/session (shared by retrieval.py, ingest.py)
├── mcp_server.py              # MCP server exposing query_codebase as a standalone tool
├── ingest.py                  # Repository ingestion pipeline (async DB inserts)
├── retrieval.py               # Hybrid retrieval + RRF (async, optional repo_id scoping)
├── generate.py                 # Groq LLM generation (AsyncGroq)
├── embed.py                   # Production embeddings
├── embed_stub.py              # Deterministic test embeddings
├── chunker.py                 # AST-based code chunking
├── run_on_repo.py              # Python file discovery
├── validate_citations.py       # Citation validation
│
├── templates/
│   └── index.html             # Browser UI
│
├── alembic/                   # Database migrations
├── tests/                      # Automated tests (pytest-asyncio for the async retrieval layer)
├── pytest.ini                 # asyncio_mode + session-scoped event loop config
│
├── .github/
│   └── workflows/
│       └── test.yml            # CI pipeline
│
├── Dockerfile
├── .dockerignore
├── requirements.txt
├── alembic.ini
├── README.md
└── BUGLOG.md
```

---

## 🚀 Running Locally

### 1. Clone the project

```bash
git clone <your-repository-url>
cd codebase_assistant
```

### 2. Create a virtual environment

Windows PowerShell:

```powershell
python -m venv venv
venv\Scripts\activate
```

Linux/macOS:

```bash
python -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create `.env`:

```env
DATABASE_URL=your_postgresql_connection_string
GROQ_API_KEY=your_groq_api_key
```

Never commit `.env`.

### 5. Run migrations

```bash
alembic upgrade head
```

### 6. Start the API

```bash
uvicorn app:app --reload
```

Open:

```text
http://127.0.0.1:8000/
```

Interactive API documentation:

```text
http://127.0.0.1:8000/docs
```

FastAPI provides automatic interactive API documentation at `/docs`. citeturn0search8turn0search4

---

## 🐳 Docker

Build:

```bash
docker build -t codebase-assistant .
```

Run locally:

```powershell
docker run --rm -p 8003:8000 --env-file .env -e PORT=8000 codebase-assistant
```

Open:

```text
http://127.0.0.1:8003/
```

The production image installs Git because `/ingest` clones repositories inside the container.

---

## 🔌 API

### `GET /`

Returns the application UI.

### `POST /ingest`

Request:

```json
{
  "repo_url": "https://github.com/username/repository",
  "repo_id": "my_repo"
}
```

Example response:

```json
{
  "status": "ingested",
  "repo_id": "my_repo"
}
```

### `POST /ask`

Request:

```json
{
  "question": "How does the configuration parser work?",
  "top_k": 5
}
```

Example response:

```json
{
  "answer": "The configuration parser ...",
  "sources": [
    "config.py:42-67",
    "loader.py:10-31"
  ]
}
```

---

## 🧪 Testing

Run:

```bash
pytest tests/ -v
```

CI runs the tests against a separate PostgreSQL/pgvector service.

The workflow:

```text
GitHub Actions
      ↓
PostgreSQL + pgvector
      ↓
Alembic migration
      ↓
pytest
```

Tests use deterministic stub embeddings so CI does not depend on downloading the production FastEmbed model. The async retrieval tests use `pytest-asyncio` with a session-scoped event loop, matching the lifetime of `db.py`'s module-level async engine.

---

## 🐛 Notable Engineering Problems Solved

This project involved several real debugging problems rather than being built as a straight-line demo.

### AST trivial-body detection

A line-count heuristic incorrectly classified legitimate one-line functions as empty.

**Solution:** inspect the actual AST body.

### PostgreSQL ENUM migration

Alembic hit:

```text
DuplicateObject: type "symbol_type" already exists
```

**Solution:** remove redundant explicit ENUM creation because SQLAlchemy's PostgreSQL dialect creates it during table creation.

### Restricted embedding environment

FastEmbed initially hit:

```text
403 Forbidden
Host not in allowlist: huggingface.co
```

**Solution:** use deterministic stub embeddings for isolated tests while retaining real FastEmbed for production.

### Retrieval ranking

Pure RRF could rank a weaker result above an exact symbol match.

**Solution:** pin tier-1 exact matches before RRF.

### Docker environment

The container initially failed because:

- `DATABASE_URL` was not supplied at runtime.
- Git was missing from the slim Python image.
- Uvicorn was initially unavailable.

These were fixed through runtime environment injection, Docker dependency installation, and a project-specific Dockerfile.

### Render ingestion

The first `/ingest` request appeared to keep loading while the embedding model was downloading and the repository was being processed.

Final production result:

```text
Inserted: 45
skipped: 0
failed: 0
```

### RDS credentials setup defaulted to a paid option

During AWS migration, "Managed in AWS Secrets Manager" was the pre-selected credentials method for RDS, which incurs an ongoing per-secret charge outside free tier.

**Solution:** switched to self-managed credentials before creating the database; RDS Proxy (which has the same Secrets Manager dependency) was left disabled for the same reason.

### `pg_restore` errors that looked like a failed migration but weren't

A `pg_restore` run against RDS returned a list of "already exists" and "duplicate key" errors, which initially looked like a broken restore.

**Root cause:** the restore had already completed successfully on an earlier run — every error was a duplicate-object/duplicate-key error, which only occurs when the schema and data are already present.

**Solution:** verified the actual state directly with `\dt` and row counts against the known source count, rather than trusting the error list at face value.

### `pgvector` extension not enabled by default on RDS

A fresh RDS PostgreSQL instance does not have `pgvector` enabled, and restoring a schema with `vector`-typed columns fails without it.

**Solution:** ran `CREATE EXTENSION IF NOT EXISTS vector;` on the target database before restoring.

### asyncpg rejected the `sslmode` connection-string parameter

Converting the DB layer to async (asyncpg/SQLAlchemy), the real Neon `DATABASE_URL` includes `?sslmode=require` — a psycopg2/libpq-specific parameter name asyncpg doesn't accept at all.

**Solution:** parse `DATABASE_URL` in `db.py`, strip `sslmode` from the query string, and translate it into `connect_args={"ssl": True}` for `create_async_engine`. Found only against the real production database — the local test DB (no SSL requirement) didn't surface it.

### mcp 2.x renamed `FastMCP` to `MCPServer`

The commonly-documented `from mcp.server.fastmcp import FastMCP` import fails on the currently-installed SDK version, with an explicit migration message pointing to the renamed `mcp.server.mcpserver.MCPServer`.

**Solution:** inspected the actually-installed SDK version and its real API via `inspect.signature()` before writing any server code, rather than assuming an older API shape from memory.

### SQLAlchemy's `text()` mis-parsed a Postgres cast in a bind parameter

Adding `repo_id` filtering, a `:repo_id::text` inline cast silently truncated the bound parameter name to `repo_i`, dropping the value entirely and causing a raw SQL syntax error downstream in asyncpg.

**Solution:** confirmed the truncation directly by inspecting `text(...)._bindparams.keys()` in isolation, then switched to `CAST(:repo_id AS text)`, which parses correctly.

### Verifying `asyncio.to_thread()` actually offloads, before relying on it for Bedrock

`boto3` has no official async client, and calling it directly from an `async def` route would block the event loop — but rather than assume `asyncio.to_thread()` solves that, it needed proof.

**Solution:** ran a 1-second blocking sync call via `asyncio.to_thread()` concurrently with an async task printing ticks every 0.2s. The async task completed all 5 ticks uninterrupted, and total wall time was ~1.0s (concurrent) rather than ~2.0s (sequential) — confirming the offload is real, not just correctly-shaped code.

### AWS Bedrock live verification pending account activation

The Bedrock integration is code-complete and verified against a mocked `boto3` client (request assembly via AWS's actual `Converse` service model, response parsing, citation validation all confirmed working together) — but a real call against live AWS hasn't been made yet, since the AWS account used for this is still going through verification.

**Status:** documented honestly as pending rather than claimed as done. Once verified, model access still needs to be explicitly enabled for the target model in the Bedrock console before a real call succeeds — a separate, no-cost step from account activation.

---

## ☁️ Deployment

### Production — Render

The application is containerized with Docker and deployed on Render through the GitHub repository.

Production startup:

```text
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Production verification included:

```text
GET  /        → 200 OK
GET  /docs   → 200 OK
POST /ingest → 200 OK
POST /ask    → 200 OK
```

The production repository ingestion successfully stored:

```text
45 chunks
0 duplicates
0 failures
```

followed by a successful `/ask` request.

### Infrastructure exercise — AWS (EC2 + RDS)

Separately from the always-on Render deployment, the same application was deployed to AWS to build hands-on experience with core AWS services named in target job descriptions. This was a deliberate, scoped exercise — not a second production environment — and was torn down after verification and documentation to avoid ongoing cost.

**Stack used:**

- **EC2** (`t3.micro`, Ubuntu 22.04) running the existing Docker image directly (`docker build` + `docker run --restart unless-stopped`) — no docker-compose, since this project deploys from a single Dockerfile
- **RDS PostgreSQL** (16.14, pgvector-enabled via `CREATE EXTENSION vector`), data migrated from Neon using `pg_dump` / `pg_restore` with `--no-owner --no-privileges`
- **VPC security groups**: RDS was not publicly accessible; its inbound rule referenced the EC2 instance's security group directly (SG-to-SG), rather than an IP range
- **Elastic IP** allocated so the demo URL stayed stable across the exercise
- **IAM**: dedicated IAM user for console access (not root), MFA on root, budget/cost alerting configured

**Why RDS instead of just keeping Neon:** RDS is explicitly named in target job descriptions, and the VPC/security-group configuration work is itself the transferable skill being demonstrated — not just a connection-string swap.

**Why EC2 instead of ECS/Fargate:** Fargate has no free-tier allowance and bills per vCPU-second immediately; a single EC2 instance running the existing image demonstrates the same containerized-deployment skill without that cost.

**Why SG-to-SG instead of IP-based rules:** EC2's traffic to RDS originates from its security-group identity inside the VPC, not from an externally visible IP — referencing the security group directly is both the correct pattern and more secure than any IP allowlist.

This exercise is documented in detail, including the full debugging log, in `PROJECT_RECORD.md` / `BUGLOG.md`.

---

## 🔐 Security

Secrets are kept out of source control.

`.env` is excluded from the Docker image and should be included in `.gitignore`.

Production secrets should be configured through the deployment platform's environment-variable system.

The GitHub repository URL is passed to Git as an argument rather than being interpolated into a shell command.

---

## ⚠️ Known Limitations

- **Python only:** multi-language parsing is not implemented yet.
- **Synchronous ingestion architecture:** `/ingest` still holds the HTTP request open for the full clone + chunk + embed + insert pipeline, even though the individual DB/LLM calls inside it are now async — this is a request/response architecture limitation, not an I/O-blocking one. A background-job/status-endpoint design would address it separately.
- **Module citations:** synthetic module chunks may have approximate line ranges.
- **Citation UX:** invalid citation tags are removed, but the unsupported claim itself can remain.
- **Repository size:** very large repositories may require incremental indexing or background jobs.
- **Private repositories:** authenticated GitHub cloning is not implemented yet.

---

## 🔮 Future Roadmap

### Retrieval

- Better duplicate suppression.
- Metadata-aware filtering.
- Repository/version-aware retrieval.
- More ranking experiments.
- Multi-language parsing with tree-sitter.

### Ingestion

- Background ingestion jobs.
- Progress/status endpoint.
- Incremental re-indexing.
- Git commit tracking.
- Repository size limits.

### UI

- Chat history.
- Markdown rendering.
- Syntax-highlighted code.
- Clickable GitHub source links.
- Streaming responses.
- Dark mode.

### Trust

- Verified citation indicators.
- Unsupported-claim highlighting.
- Source-code previews.
- Confidence/relevance indicators.

### Security

- Private repository authentication.
- GitHub OAuth/App integration.
- Rate limiting.
- More strict repository URL validation.

### Infrastructure

- Re-run the AWS exercise with CI/CD deploying to EC2 (GitHub Actions → ECR → SSH deploy).
- Add basic Terraform for the EC2/RDS/security-group resources used in the AWS exercise.

### Model integrations

- ~~AWS Bedrock as an alternate/additional LLM provider alongside Groq~~ — implemented; live verification pending AWS account activation (see [LLM Providers](#-llm-providers)).
- Langfuse tracing for prompts/retrievals/outputs.
- LangGraph, if the actual control flow shows real branching/routing worth modeling as a graph.

---

## 💼 Why This Project Is Interesting

This project goes beyond simply calling an LLM API.

It demonstrates:

- **AST-based program analysis**
- **RAG architecture**
- **semantic embeddings**
- **vector databases**
- **hybrid information retrieval**
- **ranking algorithms**
- **citation validation**
- **PostgreSQL schema design**
- **database migrations**
- **REST API development**
- **async Python (asyncio, async SQLAlchemy/asyncpg)**
- **Model Context Protocol (MCP) server implementation**
- **multi-provider LLM integration (Groq + AWS Bedrock)**
- **Docker containerization**
- **CI/CD**
- **cloud deployment (Render + AWS EC2/RDS)**
- **frontend/backend integration**
- **debugging production failures**

The retrieval system was also evaluated through failure cases, leading to a deliberate change from pure RRF to exact-match pinning.

That makes the project an example of engineering based on observed system behavior rather than simply implementing a predetermined architecture.

---

## 🎯 Interview Summary

> **Codebase Q&A Assistant** is a production-deployed RAG system for querying Python repositories. I built AST-based chunking to preserve semantic code structures, generated 384-dimensional FastEmbed embeddings, and stored them in PostgreSQL with pgvector. I implemented hybrid retrieval combining exact symbol matching with semantic vector search and RRF, then pinned exact matches after testing showed pure RRF could produce incorrect rankings. Retrieved source is passed to an LLM, and citations are validated against the actual retrieved chunks. I later converted the DB and LLM layers to async (asyncpg/async SQLAlchemy, AsyncGroq), correctly distinguishing I/O-bound calls worth converting from CPU-bound work that async wouldn't help; wrapped the retrieval layer as an MCP tool usable by any MCP-compatible client, verified against three independent MCP clients including Claude Desktop; and added AWS Bedrock as a second, per-request-selectable LLM provider alongside Groq, using Bedrock's unified `converse()` API and correctly offloading its sync-only client with `asyncio.to_thread()`. The Bedrock path is code-complete and verified against a mocked AWS client — live verification is honestly still pending AWS account activation, and I deliberately kept AWS credentials out of the public production deployment for cost/security reasons rather than risk exposing a billing surface on an unauthenticated endpoint. The application is exposed through FastAPI, containerized with Docker, tested with GitHub Actions, deployed on Render, and includes a custom browser UI. Separately, I deployed the same application to AWS (EC2 + RDS, with RDS locked down via security-group-to-security-group referencing rather than IP allowlisting) as a scoped infrastructure exercise to close the AWS/DevOps gap in target job descriptions.

---

## 📌 Project Documentation

For the detailed engineering history, see:

- `BUGLOG.md` — full debugging/build history.
- `PROJECT_RECORD.md` — architecture, decisions, deployment history, limitations, and interview notes.

---

## 📚 References

- [FastAPI documentation](https://fastapi.tiangolo.com/) — API framework and interactive API documentation. citeturn0search8
- [pgvector](https://github.com/pgvector/pgvector) — PostgreSQL vector similarity search. citeturn0search0
- [Model Context Protocol](https://modelcontextprotocol.io/) — open protocol for connecting LLM applications to external tools and data sources.

---

## 📄 License

Add your preferred license here before publishing the repository publicly.