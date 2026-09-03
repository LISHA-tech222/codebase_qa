# Bug Log

Format: what broke → what I assumed was wrong → what was actually wrong → the fix.

1. **Empty-body detector used a line-count heuristic (≤2 non-blank lines)**
   → assumed short functions were stubs
   → actually flagged legitimate one-liners (e.g. `return CSI + str(code) + 'm'`) as empty
   → fixed by checking the actual AST body (`Pass` / `Ellipsis` / docstring-only) instead of line count.

2. **`_is_trivial_body`'s own docstring broke the file's parse**
   → assumed I could quote `"""doc"""` literally inside a triple-quoted docstring
   → actually the inner `"""` closed the outer docstring early → `SyntaxError`
   → fixed by rewording the example instead of literally quoting triple-quote syntax.

3. **Alembic migration failed with `DuplicateObject: type "symbol_type" already exists`**
   → assumed I needed to explicitly create the Postgres ENUM type before creating the table
   → actually SQLAlchemy's Postgres dialect auto-creates an `Enum` column's type as a side
     effect of `create_table()` — my explicit `.create()` call ran first and collided with it
   → fixed by removing the explicit `.create()` call; `downgrade()` still calls `.drop()`
     explicitly since table teardown doesn't imply the type gets dropped too.

4. **`fastembed`'s default model failed to download** with `403 Forbidden` / `Host not in allowlist: huggingface.co`
   → this sandbox's network is locked to an allowlist (PyPI, npm, GitHub, etc.) that doesn't include huggingface.co
   → not fixable from inside the sandbox; confirmed via web search that `BAAI/bge-small-en-v1.5` (fastembed's default)
     really is 384-dim, so the migration's `Vector(384)` column is correct
   → worked around *for sandbox demo purposes only* with a deterministic hash-based pseudo-embedding
     (`fake_embed_demo_only.py`) so the DB insert pipeline could still be tested end-to-end. Real project
     uses `embed.py` (real fastembed) — this won't be an issue on a normal dev machine with full internet access.

5. **Pure RRF let a mediocre match beat a true exact match**
   → assumed RRF's rank-based math would naturally surface the obviously-correct exact symbol match first
   → actually a chunk with a weak exact-tier (rank 5, substring match) + a coincidentally-high semantic
     rank (rank 2, meaningless in this sandbox test since embeddings are hash-based, but the *mechanism*
     is real regardless of embedding quality) scored 0.0315 vs the true exact match's 0.0164 — outranking
     it despite being the wrong answer
   → decided pure RRF isn't safe for a "hard" signal like exact-name match; fixed by pinning true
     tier-1 exact matches (case-insensitive `symbol_name == query`) to the top of results, then
     RRF-merging everything else below them.

6. **Test DB setup: `psycopg2.OperationalError: fe_sendauth: no password supplied`**
   → assumed connecting as the `postgres` superuser over TCP from Python would just work
   → actually `pg_hba.conf` requires a password for TCP superuser connections that peer
     (unix-socket, OS-user-matched) auth doesn't need — the working pattern used everywhere
     else in this project was `su postgres -c psql` (peer auth), not a direct psycopg2 TCP
     connection as `postgres`
   → fixed by running the admin-only steps (CREATE DATABASE, CREATE EXTENSION) via `subprocess`
     calls to `psql` as the `postgres` OS user, matching the working pattern, instead of fighting
     TCP superuser auth from Python for what's genuinely a one-time admin action.

7. **`CREATE EXTENSION vector` failed with `InsufficientPrivilege` even after granting `codeqa_user` CREATEDB**
   → assumed CREATEDB privilege would be enough to also create extensions in a database that user owns
   → actually creating extensions specifically requires superuser (or a narrower `pg_read_server_files`-style
     grant not set up here), separate from database ownership/creation rights
   → fixed as part of #6 above — extension creation also routed through the superuser `psql` path.

8. **My own test asserted the wrong method set for `Config`**
   → assumed `Config` only had `load`/`save` and wrote the test around that
   → actually `Config.__init__` is also a real method the chunker correctly extracts — the test
     was wrong, not the chunker
   → fixed the assertion to include `Config.__init__`. Kept as a log entry because it's a good
     example of a test failure that was signal, not noise — worth remembering not to assume a
     failing test always means the *implementation* is wrong.

9. **Renamed `fake_embed_demo_only.py` → `embed_stub.py`, `fake_embed()` → `stub_embed()`**
   → originally treated as sandbox-only scaffolding to delete before moving to the real repo
   → actually worth keeping deliberately: it's what lets CI (Step 8) run the full test suite
     without network access to Hugging Face or a real API key — the exact kind of environment
     constraint the plan's Step 8 warns you'll hit, just discovered one step earlier than expected
   → renamed across `ingest.py` and both test files touching it, reran the full 17-test suite
     to confirm zero regressions from the rename.

10. **Swapped `generate.py` from Anthropic API to Groq** (free tier, no credit
    card, OpenAI-compatible `chat.completions.create` interface) to match
    the actual free-tier stack being used (Groq + Neon + Render). Verified
    the `groq` package's client signature matches what the code expects
    and that the module imports cleanly, but have NOT yet made a real
    API call with a live key — that's a real test still owed once running
    on my own machine with an actual `GROQ_API_KEY`.

---

## Design decisions made (with reasoning, for interview prep)

- **Module-level code** (imports, constants, module docstring): bundled into one
  synthetic `<module>` chunk per file, rather than dropped or attached to every
  chunk. Rationale: nothing gets silently lost from retrieval; cheaper than
  attaching imports to every function chunk.
  - Known tradeoff: module chunk's line range is just `min/max` of its
    (possibly non-contiguous) lines — approximate, not a literal contiguous
    span. Accepted as-is; a citation on this chunk type may be slightly
    imprecise.

- **Class + method chunking**: classes are chunked whole AND each method gets
  its own chunk. Rationale: better recall for "how does X.method work"-style
  queries. Known tradeoff: deliberate content overlap between a class chunk
  and its method chunks — will need de-duping/ranking logic at retrieval time
  (Step 5) so results aren't near-duplicates.

- **"Large chunk" flagging** only applies to `function`/`method` types, not
  `class` — a class is naturally long because it includes every method, so
  line count there isn't a meaningful complexity signal.

- **Schema: added `repo_id`/`repo_name`**, not just `file_path`, once it was
  clear that ingesting a second repo would make `file_path` ambiguous
  (`utils.py` exists in a lot of repos). `file_path` alone is not a stable
  identity key once you support >1 repo.

- **`symbol_type` as a Postgres ENUM, not free text.** Traded off migration
  friction (adding a new symbol_type later requires an `ALTER TYPE`) for
  DB-level validation — a typo in application code becomes a DB error
  instead of silently corrupting data. Worth it since the type set (function/
  class/method/module) is stable and unlikely to grow often.

- **Hybrid retrieval ranking: pinned exact matches, not pure RRF.** Initially
  implemented pure Reciprocal Rank Fusion (merge exact-match ranks and
  semantic ranks purely by rank position, no pinning). A real test proved
  this lets a true exact match get outranked by two mediocre-but-present
  signals elsewhere (see bug log #5). Fixed by treating tier-1 exact
  symbol-name matches as a hard signal, pinned above the RRF-merged
  remainder. Chose `k=60` for RRF (the standard default from the original
  RRF paper) as the smoothing constant for the non-pinned remainder.

- **Citation validation, not just citation instruction.** The system prompt
  tells the model to cite `[file:start-end]`, but an instruction alone
  doesn't stop a model from inventing a plausible-looking citation that
  was never actually retrieved. Added `validate_citations()` to check every
  parsed citation against the *actual* set of retrieved chunks and flag
  any that don't match. Tested against a deliberately mixed real/fake
  citation example to confirm it catches hallucination rather than
  rubber-stamping anything shaped like `[file.py:N-M]`.

- **Strip-silently means an unsupported claim now LOOKS like a correctly
  uncited statement.** Chose to silently strip invalid citation tags
  (rather than flag or retry) so bad answers still read naturally. Real
  tradeoff, tested and confirmed: after stripping, "It also has a
  reset_all method that clears all styling." reads exactly as confidently
  as a true, properly-cited sentence — there's no visual difference
  between "this claim was never actually cited" and "this claim's
  citation was caught as fake and removed." Accepted this for now since
  it matches the "silently strip" decision, but flagging honestly:
  a stronger version of this app would visually distinguish cited vs.
  uncited sentences in the UI, not just clean up the text.

11. **`asyncpg.connect()` rejected `sslmode` from the Neon connection string**
    → assumed swapping the driver prefix (postgresql:// -> postgresql+asyncpg://)
      was the only change needed to make DATABASE_URL work with asyncpg
    → actually asyncpg doesn't accept `sslmode` as a URL query param or kwarg
      at all — that's psycopg2/libpq-specific naming; asyncpg wants SSL
      configured via a separate `ssl` connect arg
    → fixed by parsing DATABASE_URL, stripping `sslmode` from the query
      string, and passing connect_args={"ssl": True} to create_async_engine
      when sslmode was require/verify-ca/verify-full. Verified against a
      synthetic Neon-style URL (sslmode stripped, ssl=True set) and against
      the real local DB (no sslmode present, no ssl arg forced) — both
      paths tested, not just the happy path.

12. **pytest-asyncio's default per-test event loop broke db.py's module-level engine**
    → assumed the existing test structure (one test function = one isolated unit)
      would just work once test_retrieval.py's calls were made async
    → actually pytest-asyncio creates a NEW event loop per test function by default,
      but db.py's async engine (and its asyncpg connection pool) is a module-level
      singleton created once at import — connections created under the first test's
      event loop broke when reused under the next test's fresh loop:
      "InterfaceError: cannot perform operation: another operation is in progress"
    → fixed by pinning both asyncio_default_fixture_loop_scope and
      asyncio_default_test_loop_scope to "session" in pytest.ini, so all async
      tests share one event loop for the whole session — matching db.py's actual
      engine lifetime. Verified: ran test_retrieval.py alone (passed), then the
      full suite together (16/17 passed, one pre-existing sandbox-only failure
      unrelated to this).
