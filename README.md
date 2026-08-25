# Codebase Q&A Assistant

Ask natural-language questions about a Python codebase and get answers grounded
in the actual source, with `file_path:start_line-end_line` citations validated
against what was actually retrieved (not just trusted from the LLM's output).

## How it works

1. **Ingestion** (`chunker.py`) — walks each `.py` file's AST and extracts every
   function, class, and method as its own chunk, plus a synthetic per-file
   `module` chunk for imports/constants that live outside any function or class.
2. **Embeddings** (`embed.py`) — each chunk's docstring + content is embedded
   with `fastembed` (`BAAI/bge-small-en-v1.5`, 384-dim).
3. **Storage** (`alembic/`, Postgres + pgvector) — chunks are stored with a
   uniqueness constraint on `(repo_id, file_path, symbol_name, start_line)` so
   re-ingesting a repo doesn't duplicate rows.
4. **Retrieval** (`retrieval.py`) — hybrid search: exact symbol-name matching
   + pgvector cosine similarity, combined via Reciprocal Rank Fusion, with
   true exact matches pinned above the RRF-merged results (see Design
   Decisions below for why).
5. **Answer generation** (`generate.py`, `validate_citations.py`) — sends
   retrieved chunks to a Groq-hosted model (`llama-3.3-70b-versatile`, free
   tier, OpenAI-compatible API) with a system prompt requiring inline
   citations, then validates every citation against the chunks actually
   retrieved and silently strips any that don't match.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your Neon Postgres URL and GROQ_API_KEY
alembic upgrade head
python ingest.py /path/to/some/repo my_repo_id
```

Then query it (see `retrieval.py` + `generate.py` for the pieces to wire into
a CLI or web endpoint — this repo currently exposes the pipeline as importable
functions, not yet a served API).

## Testing

```bash
pytest tests/ -v
```

Tests run against a real, separate Postgres database (`codeqa_test`), created
and migrated fresh via the actual Alembic migration on every run — not a
hand-rolled schema shortcut. Embeddings in tests use a deterministic hash-based
stub (`embed_stub.py`) instead of the real model, so the suite doesn't need
network access to Hugging Face or an API key.

## Design decisions (and why)

- **Module-level code** (imports, constants) is bundled into one synthetic
  `module` chunk per file rather than dropped, so nothing indexed is silently
  lost from retrieval.
- **Classes are chunked both as a whole AND per-method.** Better recall for
  "how does X.method work" queries, at the cost of deliberate content overlap
  between a class chunk and its method chunks.
- **Exact matches are pinned above RRF-merged results, not blended in purely
  by rank.** Testing showed pure RRF could let a real exact symbol-name match
  get outranked by two mediocre-but-present signals elsewhere — see bug log
  entry #5. A hard signal (exact name match) shouldn't be probabilistically
  overridable by a soft one (semantic similarity).
- **Hallucinated citations are validated and silently stripped, not trusted.**
  An LLM instructed to cite `[file:start-end]` can still invent a
  plausible-looking one. Every citation is checked against the chunks that
  were actually retrieved before being shown.

## Known limitations

- Python only — no multi-language support yet (tree-sitter would be the
  natural next step).
- The synthetic `module` chunk's line range is an approximate `min/max` of
  its (possibly non-contiguous) lines, not a literal contiguous span — a
  citation on this chunk type may be slightly imprecise.
- Silently stripping an invalid citation removes the fake citation tag but
  not the unsupported claim itself — right now there's no visual distinction
  in the output between "this sentence has a verified citation" and "this
  sentence never had one to begin with." A stronger version of this app would
  surface that distinction in the UI.

See `BUGLOG.md` for the full build log of real issues hit and fixed along the way.
