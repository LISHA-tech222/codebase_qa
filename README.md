# Codebase Q&A Assistant

> **Ask natural-language questions about a Python codebase and get answers grounded in the actual source code.**

A production-ready RAG application that ingests Python repositories, retrieves relevant code using **hybrid exact + semantic search**, and generates source-grounded answers with **validated citations**.

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.141.1-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![pgvector](https://img.shields.io/badge/pgvector-vector%20search-336791)](https://github.com/pgvector/pgvector)
[![Docker](https://img.shields.io/badge/Docker-deployed-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Render](https://img.shields.io/badge/Render-production-46E3B7?logo=render&logoColor=white)](https://render.com/)

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

Chunks and embeddings are stored in PostgreSQL with pgvector.

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

### 5. Generation

The retrieved chunks are passed to:

```text
Groq
openai/gpt-oss-20b
```

The model generates an answer grounded in the retrieved source.

### 6. Citation validation

Generated citations are checked against the actual retrieved chunks.

A citation that does not correspond to retrieved source is removed rather than trusted.

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

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| API | FastAPI |
| Server | Uvicorn |
| Parsing | Python AST |
| Embeddings | FastEmbed / BAAI/bge-small-en-v1.5 |
| Vector dimension | 384 |
| Database | PostgreSQL |
| Vector search | pgvector |
| Migrations | Alembic |
| LLM | Groq / openai/gpt-oss-20b |
| Frontend | HTML/CSS/JavaScript |
| Containerization | Docker |
| CI | GitHub Actions |
| Deployment | Render |

FastAPI provides the API layer and automatic interactive API documentation; pgvector provides vector similarity search inside PostgreSQL. citeturn0search8turn0search0

---

## 📁 Project Structure

```text
codebase_assistant/
│
├── app.py                    # FastAPI API + web UI entry point
├── ingest.py                 # Repository ingestion pipeline
├── retrieval.py              # Hybrid retrieval + RRF
├── generate.py               # Groq LLM generation
├── embed.py                  # Production embeddings
├── embed_stub.py             # Deterministic test embeddings
├── chunker.py                # AST-based code chunking
├── run_on_repo.py            # Python file discovery
├── validate_citations.py     # Citation validation
│
├── templates/
│   └── index.html            # Browser UI
│
├── alembic/                  # Database migrations
├── tests/                    # Automated tests
│
├── .github/
│   └── workflows/
│       └── test.yml          # CI pipeline
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

FastAPI provides automatic interactive API documentation at `/docs`. citeturn0search8turn0search4

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

Tests use deterministic stub embeddings so CI does not depend on downloading the production FastEmbed model.

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

---

## ☁️ Deployment

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

---

## 🔐 Security

Secrets are kept out of source control.

`.env` is excluded from the Docker image and should be included in `.gitignore`.

Production secrets should be configured through the deployment platform's environment-variable system.

The GitHub repository URL is passed to Git as an argument rather than being interpolated into a shell command.

---

## ⚠️ Known Limitations

- **Python only:** multi-language parsing is not implemented yet.
- **Synchronous ingestion:** large repositories can make `/ingest` take a long time.
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
- **Docker containerization**
- **CI/CD**
- **cloud deployment**
- **frontend/backend integration**
- **debugging production failures**

The retrieval system was also evaluated through failure cases, leading to a deliberate change from pure RRF to exact-match pinning.

That makes the project an example of engineering based on observed system behavior rather than simply implementing a predetermined architecture.

---

## 🎯 Interview Summary

> **Codebase Q&A Assistant** is a production-deployed RAG system for querying Python repositories. I built AST-based chunking to preserve semantic code structures, generated 384-dimensional FastEmbed embeddings, and stored them in PostgreSQL with pgvector. I implemented hybrid retrieval combining exact symbol matching with semantic vector search and RRF, then pinned exact matches after testing showed pure RRF could produce incorrect rankings. Retrieved source is passed to a Groq-hosted LLM, and citations are validated against the actual retrieved chunks. The application is exposed through FastAPI, containerized with Docker, tested with GitHub Actions, deployed on Render, and includes a custom browser UI.

---

## 📌 Project Documentation

For the detailed engineering history, see:

- `BUGLOG.md` — full debugging/build history.
- `PROJECT_RECORD.md` — architecture, decisions, deployment history, limitations, and interview notes.

---

## 📚 References

- [FastAPI documentation](https://fastapi.tiangolo.com/) — API framework and interactive API documentation. citeturn0search8
- [pgvector](https://github.com/pgvector/pgvector) — PostgreSQL vector similarity search. citeturn0search0

---

## 📄 License

Add your preferred license here before publishing the repository publicly.
