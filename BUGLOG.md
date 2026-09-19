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
13. **asyncpg couldn't infer parameter type in `$2 IS NULL OR repo_id = $2` pattern**
    → assumed a plain `:repo_id IS NULL OR repo_id = :repo_id` clause would work
      the same way it does in psycopg2/plain SQL
    → actually asyncpg's prepared-statement protocol requires it can determine
      each parameter's type from context, and couldn't infer one from `IS NULL`
      alone even combined with the later `= :repo_id` comparison
    → attempted fix: `:repo_id::text` inline cast — this created bug #16 below,
      so not the final fix

14. **SQLAlchemy's text() bind-param parser silently truncated `:repo_id::text`**
    → assumed `:paramname::pgtype` (Postgres cast syntax) would parse the same
      as any other `:paramname` reference
    → actually SQLAlchemy's bind-param regex in text() mis-parsed the name
      right up against `::`, registering the param as `repo_i` (one character
      short) instead of `repo_id` — confirmed directly by inspecting
      `text(...)._bindparams.keys()` in isolation before touching the real
      query. The bind value was silently dropped; asyncpg then received the
      literal, unparsed `::text` in the SQL and threw a syntax error.
    → fixed by using `CAST(:repo_id AS text)` instead of `:repo_id::text` —
      confirmed via the same isolated check that this parses correctly, then
      re-verified against a live DB with two separately seeded repos sharing
      an identical symbol name (repo_a and repo_b both have a `reset_all`,
      different bodies) to prove repo_id genuinely isolates results and
      repo_id=None still returns both, unchanged.

15. **FastMCP was renamed/removed in mcp 2.x**
    → assumed `from mcp.server.fastmcp import FastMCP` (the widely-known
      v1 API) would work with whatever mcp version installs today
    → actually mcp 2.x renamed it to MCPServer and moved the import path
      (mcp.server.mcpserver.MCPServer) — the old import raises a
      ModuleNotFoundError with an explicit migration pointer, not a
      silent failure
    → fixed by inspecting the actually-installed version (2.1.1) and its
      real API via inspect.signature() before writing any server code,
      rather than assuming the v1 API from training data. Verified the
      full tool-call path (initialize -> list_tools -> call_tool) through
      an actual in-memory MCP client/server session, not just an import
      check — including confirming repo_id scoping holds through the real
      protocol layer, not just the underlying function.

16. **Claude Desktop: initial "config file" pasted for editing had no `mcpServers` key at all**
    → assumed the file shown was either the wrong file (a known Windows MSIX
      redirection bug in Claude Desktop's "Edit Config" button was initially
      suspected) or an outdated config format
    → actually it was the correct, current claude_desktop_config.json at the
      standard path (%APPDATA%\Claude\claude_desktop_config.json) — it simply
      hadn't had an mcpServers key added yet on this install. A working config
      from another setup confirmed the plain command/args/env structure is
      still correct for local stdio servers
    → fixed by adding an mcpServers.codebase-qa block (DATABASE_URL in env) to
      the existing file, fully restarting Claude Desktop via system tray —
      confirmed working, tool appeared in the tools list and returned correct
      results against the real Neon DB

17. **Verified asyncio.to_thread() genuinely doesn't block the event loop, before relying on it**
    → this needed proof, not assumption, before using it for the Bedrock call
    → ran a 1-second blocking sync call via asyncio.to_thread() concurrently
      with an async task printing ticks every 0.2s — confirmed the async task
      kept running uninterrupted (5 ticks completed) and total wall time was
      ~1.0s (concurrent), not ~2.0s (sequential) — proving the offload is real,
      not just correctly-shaped code

18. **Verified converse() request/response shape via botocore's service model, not memory**
    → rather than assume AWS's exact field names (modelId, messages, system,
      inferenceConfig) and response structure from training data, inspected
      client.meta.service_model.operation_model('Converse') directly to get
      the real required/optional fields and nested shapes before writing any
      request-building code — confirmed messages[].content[].text, system[].text,
      inferenceConfig.maxTokens, and response.output.message.content[0].text
      all match what was actually implemented

19. **TestClient event-loop artifact when chaining two .post() calls in one script**
    → not a code bug in app.py/generate.py — a testing artifact. TestClient
      created a fresh event loop portal for a second .post() call in the same
      script, while db.py's module-level async engine was still bound to the
      first call's loop (same root cause class as bug #14). Confirmed this by
      re-running each provider test alone, in its own process — both passed
      cleanly in isolation. Doesn't affect the real app: uvicorn runs one
      continuous event loop for the server's whole lifetime, so this exact
      failure mode can't occur in production, only in throwaway multi-call
      test scripts

20. **Full request/response/citation-validation path verified end-to-end with a mocked boto3 client**
    → couldn't test a live AWS call yet (see #21), so didn't skip verification
      entirely — mocked boto3.client to run the real code path: request
      assembly (modelId, system, messages, inferenceConfig), response parsing
      (output.message.content[0].text), and citation validation (real citation
      kept, fabricated one stripped) all confirmed working together, plus the
      same test run through the actual FastAPI route (TestClient) to confirm
      provider="bedrock" reaches _answer_bedrock correctly and provider
      omitted still reaches Groq exactly as before Step 2

21. **AWS Bedrock: no live call made yet — new AWS account pending verification, not a code issue**
    → this is a genuine "not yet done," not glossed over: everything up to
      the real AWS network boundary is tested (see #17, #18, #20), but an
      actual converse() call against real AWS has not succeeded yet because
      account verification hasn't completed
    → once verified: still need to explicitly enable model access for the
      target model in the Bedrock console (separate step from account
      verification, no cost) before a real call will succeed
    → deliberately NOT deploying AWS credentials to Render/production even
      after local verification succeeds — /ask has no auth check, so live
      AWS credentials on a public unauthenticated endpoint would be a real
      billing exposure; local-only credentials mean the code path stays
      genuine and demonstrable while the deployed app fails safely at $0
      if ever hit
22. **Verified @observe decorator preserves async function behavior before relying on it**
    → confirmed inspect.iscoroutinefunction() still True after decoration,
      and inspect.signature() is preserved (needed for FastAPI's request
      parsing when decorating the /ask route itself) -- checked directly,
      not assumed from the SDK's documented behavior

23. **Verified Langfuse's export failure doesn't break the actual request**
    → real, important safety property, not just "should work in theory":
      ran a full /ask request with fake Langfuse credentials pointed at the
      real (sandbox-network-blocked) host -- request returned 200 with the
      correct answer; Langfuse's failed span export only logged a warning
      to stderr, never touched the response. Confirms tracing failures
      degrade gracefully rather than taking down the endpoint

24. **First real trace showed 21.71s, far outside the 0.3-4.5s range of others**
    → assumed initial hypothesis: could be Neon free-tier DB auto-suspend/
      resume, Render cold start, or FastEmbed's lazy model load -- did not
      assume which one without checking
    → confirmed by re-running the identical question immediately after, on
      the same already-running local process: second call dropped to 3.16s
    → actual cause: FastEmbed's model is lazily loaded on first use
      (_get_model() in embed.py) and cached after -- the slow trace was the
      one-time per-process model-load cost, not a per-request problem, a
      provider-latency difference, or a real performance bug

25. **langchain-mcp-adapters hard-pins mcp<2.0.0, conflicting with mcp_server.py's mcp 2.x MCPServer**
    → assumed mcp_server.py (built in Step 1 against mcp 2.x's MCPServer)
      would work unchanged as the target of a LangGraph MCP-client retrieve
      node
    → actually langchain-mcp-adapters 0.3.2 declares mcp<2.0.0,>=1.24.0 --
      confirmed via importlib.metadata.requires(), not guessed. Installing
      it silently downgraded mcp to 1.30.0, which has no
      mcp.server.mcpserver.MCPServer at all, breaking mcp_server.py's import
    → real design decision, not a silent workaround: downgraded mcp_server.py
      to mcp 1.x's FastMCP API (a genuine reversal of Step 1's earlier
      FastMCP->MCPServer fix, made deliberately for ecosystem compatibility,
      not a regression). Re-verified functional parity by re-running Step 1's
      exact in-memory protocol test (tool discovery + repo_id-scoped query)
      against the downgraded server -- identical results to before

26. **MultiServerMCPClient subprocess call failed with a TLS certificate error**
    → assumed once the mcp version conflict was fixed, the retrieve node
      would work end-to-end
    → actually the MCP SDK deliberately strips a spawned server subprocess's
      environment down to HOME/PATH/TERM only (confirmed via
      get_default_environment(), not assumed) -- a real, sensible security
      default that prevents secrets leaking from parent to child process.
      The subprocess's embed_query call used REAL FastEmbed, which tried
      reaching huggingface.co; this sandbox's network egress proxy combined
      with the minimal subprocess environment turned the usual "host not
      allowed" 403 into a self-signed-certificate TLS error instead
    → this also surfaced a real, separate architectural gap: since
      mcp_server.py now runs as a genuine OS subprocess when spawned by
      MultiServerMCPClient, monkeypatching embed.embed_query from a parent
      test process (this project's method everywhere else) cannot reach it
    → fixed properly, not just worked around: added EMBEDDINGS_PROVIDER=stub
      as an env-var switch inside embed.py itself, matching the project's
      existing env-driven config pattern (BEDROCK_MODEL_ID, LANGFUSE_HOST).
      Tests pass this through MultiServerMCPClient's connection env dict.
      Verified this actually fixes the root cause (not just masks the
      symptom) by re-running the same failing call with EMBEDDINGS_PROVIDER=
      stub set -- succeeded, and separately confirmed the multi-chunk
      response shape (one MCP content block per chunk, each block's `text`
      a JSON string) by testing with 2 seeded chunks before writing the
      retrieve node's parsing logic

27. **Verified the Postgres checkpointer's actual claimed benefit, not just that it runs**
    → confirmed a checkpoint is genuinely persisted after graph.ainvoke() by
      calling graph.aget_state(config) and comparing to the invocation's
      result -- not just "no exception was raised"
    → then verified the real differentiator over MemorySaver specifically:
      recovered the exact same checkpoint state from a COMPLETELY SEPARATE
      Python process (no shared memory with the process that wrote it,
      thread_id looked up fresh from the checkpoints table) -- proving the
      checkpoint survives process boundaries, which MemorySaver's in-memory
      dict could never do. This is the concrete proof behind the Postgres-
      vs-MemorySaver decision, not just an assumption from the library docs

28. **get_input_schema() was the wrong artifact for checking "is repo_id hidden from the LLM"**
    → assumed a tool's get_input_schema() reflects what the LLM actually
      sees when the tool is bound to a chat model
    → actually it includes InjectedState-annotated params too -- the
      correct artifact is what convert_to_openai_tool() produces, which
      is the literal JSON schema sent to the LLM API. Checked that
      directly: it correctly showed only `query`, confirming repo_id/top_k
      really are invisible to the model, not just intended to be

29. **ToolNode.ainvoke() called standalone (outside a compiled graph) failed on InjectedState**
    → assumed I could unit-test InjectedState injection by calling
      ToolNode.ainvoke(state) directly
    → actually InjectedState injection needs the runtime context a
      compiled StateGraph provides during execution -- calling ToolNode
      standalone raised "Missing required config key" instead of silently
      working
    → fixed by testing inside an actual minimal StateGraph instead, which
      is also the real usage pattern, not a workaround

30. **Duplicate sources when two different search queries retrieved overlapping chunks**
    → not assumed away as fine -- observed directly in a 3-turn loop test
      (two searches against a small 2-chunk test repo both returned both
      chunks), producing a sources list with each citation twice
    → fixed with order-preserving dedup in finalize(), re-verified the
      same test now returns each citation once

## Design decisions -- Step 4 (LangGraph agentic orchestration)

- **mcp version conflict resolved by downgrading mcp_server.py to mcp 1.x's
  FastMCP**, not by avoiding langchain-mcp-adapters. A single venv can only
  have one mcp version; langchain-mcp-adapters hard-pins mcp<2.0.0
  (confirmed via importlib.metadata, not guessed). Re-verified mcp_server.py's
  full Step 1 behavior (tool discovery, repo_id scoping) held after the
  downgrade via the same in-memory protocol test used in Step 1.

- **Real MCP client (MultiServerMCPClient), not a direct function import,**
  for the retrieve/search_codebase tool. The graph is a genuine MCP client
  over the stdio protocol boundary, not just reused Python code -- this is
  what actually closes the MCP-orchestration gap rather than merely reusing
  Step 1's code.

- **EMBEDDINGS_PROVIDER=stub added to embed.py** because mcp_server.py now
  runs as a genuine OS subprocess when spawned by MultiServerMCPClient --
  monkeypatching embed.embed_query from a parent test process (used
  everywhere else in this project) cannot reach a separate process's
  imports. An env-var switch, passed through the subprocess's connection
  config, is the only way to make this testable in CI.

- **Postgres-backed checkpointer (AsyncPostgresSaver), not MemorySaver.**
  Verified the actual differentiator, not just that the checkpointer runs:
  recovered a checkpoint from a completely separate Python process with no
  shared memory. This is required for item 4 (human-in-the-loop) to be
  genuinely resumable across a real gap, not just "resumable as long as the
  process never restarts." Introduces psycopg (v3) as a third Postgres
  driver in this project (alongside psycopg2 for Alembic, asyncpg for the
  app) -- deliberate: each does a job the others can't cleanly do.

- **repo_id/top_k hidden from the LLM's tool-calling schema via InjectedState,**
  not passed through the system prompt and trusted. This keeps Step 1's
  repo_id isolation guarantee structurally true (enforced by code) rather
  than model-behavior-dependent (trusting the LLM to always repeat the
  correct repo_id across a multi-turn reasoning loop, where a slip would be
  a real cross-repo data leak). Verified directly: a fake reasoner that only
  ever provided `query` still resulted in the real, correct repo_id being
  used at execution time.

- **Only one real tool given to the reasoner (search_codebase).** The plan
  said "query_codebase (and any other available action)" -- no second tool
  actually exists in this system yet, so none was invented just to make the
  tool list look fuller than it honestly is.

31. **Recursion limit (item 6): confirmed real behavior, not left theoretical**
    → forced a reasoner that never stops calling tools (recursion_limit
      temporarily set to 6) -- raised langgraph.errors.GraphRecursionError
      with a clear message, exactly as documented
    → real, non-obvious finding: the checkpoint was NOT discarded --
      aget_state() after the error showed all messages/chunks accumulated
      up to that point preserved, with state.next correctly pointing at
      the node that would run next
    → further verified this is genuinely resumable, not just inspectable:
      re-invoking with a higher recursion_limit on the same thread_id
      continued from exactly where it left off (same accumulated chunks
      carried forward), not a restart. Production default set to 25 based
      on walking real traces (a healthy multi-retry run takes ~10-11 hops)

32. **Windows: psycopg_pool.PoolTimeout -- ProactorEventLoop incompatible with async psycopg**
    → found only when Lisha ran the real test suite on her Windows machine
      (my Linux sandbox can't reproduce this -- ProactorEventLoop doesn't
      exist there). All 17 pre-existing tests passed; only the new
      checkpointer-dependent tests failed
    → psycopg's own warning named the exact cause: "Psycopg cannot use the
      'ProactorEventLoop' to run in async mode"
    → fixed by setting asyncio.WindowsSelectorEventLoopPolicy() at the very
      top of both tests/conftest.py and checkpointer.py, before any other
      imports run (must happen before any event loop is created). This is
      a genuine production bug, not just a test artifact -- the same crash
      would happen running the real app on Windows, so the fix went into
      checkpointer.py itself, not just the test config. Re-ran the full
      suite after the fix: all 21 tests passed

33. **Live-only: Groq (openai/gpt-oss-20b) generated malformed tool-call JSON**
    → found on Lisha's first live, unmocked run -- groq.BadRequestError,
      code 'tool_use_failed', failed_generation showed literally invalid
      JSON (stray comma/quote) in the model's own tool-call arguments
    → this is a real, known failure mode of smaller/faster tool-calling
      models, not a bug in this project's code -- but it exposed a real
      gap: the whole request crashed uncaught on a single occurrence
    → fixed with a bounded retry (3 attempts) around the reasoner's LLM
      call, catching groq.BadRequestError specifically. Verified in
      isolation first (reasoner() called directly, mocked to fail twice
      then succeed -- exactly 3 calls, correct result) before trusting it
      in the full graph

34. **Live-only: reasoner got stuck in a genuine repetitive search loop**
    → found on Lisha's second live run -- the real model searched
      "configuration parser" 4 times VERBATIM in a row, then kept
      searching near-identical variants, accumulating 60 chunks across 13
      searches before hitting GraphRecursionError and crashing. This
      config-parser query never should have needed that many searches --
      the repo genuinely may not have had one
    → assumed initially this might be a hypothetical edge case; it was not
    → fixed with a real, code-enforced hard cap (MAX_SEARCHES=5): past this
      many searches, the reasoner is called WITHOUT tool-binding at all --
      the model literally cannot call the tool again, not just asked
      nicely not to. Also tightened the system prompt against repeating
      queries. Verified by mocking a reasoner that never wants to stop --
      confirmed it was forced to a plain-text answer at exactly 5 searches,
      no crash

35. **MAJOR, live-only: the reasoner never actually received retrieved code content**
    → the single most important bug in this entire project. search_codebase
      returned only "Found N result(s) for QUERY" as the ToolMessage
      content -- the real chunk content/citations were stored in state for
      finalize's citation check, but the MODELITSELF was never shown them
    → this was completely invisible to every mocked test in this step,
      because every test scripted the model's final answer directly --
      none of them depended on the model actually reading tool output to
      produce an answer. Only a live model, genuinely trying to answer from
      what it could see, exposed this
    → confirmed by Lisha's live run: the model correctly and honestly said
      "I don't have the contents of preprocess.py available" -- it wasn't
      wrong or hallucinating, it was telling the truth about what it had
      actually been given
    → fixed by building real formatted context (file path, symbol name,
      code content) into the ToolMessage, matching generate.py's existing
      format_context() pattern. Verified directly: captured exactly what
      the reasoner received on its second call and confirmed real code
      (not just a count) was present, before trusting the fix
    → re-verified live after the fix: a real question ("What does
      preprocess.py do?") produced a detailed, accurate, correctly-cited,
      genuinely-grounded answer -- the positive-path confirmation this
      whole step needed

## Design decisions -- Step 4 continued (items 6-8)

- **MAX_SEARCHES hard cap, separate from the graph's recursion_limit.**
  recursion_limit is an infra-level safety net (crashes the whole request
  if exceeded); MAX_SEARCHES is a graceful, code-enforced behavioral limit
  discovered necessary only through live testing -- the model cannot be
  trusted to reliably self-limit tool calls, so the option to call the tool
  again is structurally removed rather than requested via prompt alone.

- **Escalating retry fallback in reasoner(), not a flat retry.** The final
  retry attempt drops tool-binding entirely and tells the model tools are
  unavailable, guaranteeing the function always returns a valid response
  rather than exhausting all attempts identically and crashing regardless.

- **CI tests (item 7) cover forced tool-call, forced interrupt+resume,
  forced critic handoff, and recursion-limit-survives-and-resumes** (one
  addition beyond the three named in the plan, since that safety net is
  exactly the kind of thing that silently breaks later without CI). All
  Groq calls mocked; test.yml's CI env gained EMBEDDINGS_PROVIDER=stub and
  a dummy GROQ_API_KEY so the MCP subprocess and reasoner construction work
  in CI without real network access.

- **Langfuse as_type values chosen precisely, not generically:** reasoner
  as "agent", search_codebase as "tool", critic as "evaluator", finalize as
  "guardrail", ask_agent as the top-level trace. Found and fixed a real bug
  applying this: @observe wrapping an already-@tool-decorated object
  silently destroyed its BaseTool interface (.ainvoke disappeared) --
  fixed by reversing decorator order (@tool outermost), re-verified the
  InjectedState hiding still held afterward.

36.  Hard dependency conflict: groq==1.6.0 vs langchain-groq's groq<1.0.0 requirement
    → this existed from the moment Step 4 added langchain-groq to
      requirements.txt alongside Steps 0/2's groq==1.6.0 pin, but wasn't
      caught until a real pip install -r requirements.txt from a clean
      environment (GitHub Actions, then Render) tried to resolve both
      together -- pip's resolver correctly refused with ResolutionImpossible
    → gap in my own verification: packages were installed incrementally
      across many sandbox sessions rather than one clean install from
      requirements.txt, so this conflict was invisible to me until it hit
      real CI/deployment
    → fixed by downgrading groq to 0.37.1 (latest version satisfying
      langchain-groq's groq>=0.30.0,<1.0.0 across all its versions).
      Verified AsyncGroq, chat.completions.create()'s signature, and
      BadRequestError (used in generate.py and graph.py respectively) are
      all unchanged in 0.37.1 before trusting the downgrade. Confirmed with
      a real pip install -r requirements.txt into a completely fresh venv(matching what CI/Render actually do) -- resolved cleanly, then ran
      the full test suite in that same fresh venv: 20/21 passing, same
      pre-existing unrelated failure as always

37. **`_rrf_merge` had no explicit tiebreak rule -- equal-score chunks silently
    ordered by dict/argument insertion order**
    → not a runtime crash, so nothing "broke" in the traditional sense, but the
      gap was real: two chunks with the exact same RRF score (e.g. one hitting
      only the exact-match list at rank 3 and another hitting only the
      semantic list at rank 3, both scoring 1/(60+3)) were ordered purely by
      which of `remaining_exact`/`semantic_ids` got passed to `_rrf_merge`
      first and which id happened to get inserted into the `scores` dict
      first -- an accident of argument order and Python's stable sort, not a
      designed rule. Swapping the two arguments' order, or reordering how a
      list was built upstream, could silently reorder tied results between
      otherwise-identical requests
    → confirmed this was a real, reachable case (not just theoretical) by
      constructing exact ties directly against `_rrf_merge` with `RRF_K`
      monkeypatched to 0 for clean fractions: two ids at the same score via
      different rank/list combinations, verified the pre-fix code's ordering
      really did depend on insertion order
    → fixed by adding an explicit, three-level deterministic tiebreak used
      only when RRF scores are exactly equal: (1) prefer the chunk appearing
      in MORE ranked lists -- both exact and semantic agreeing on a chunk is
      a stronger signal than either alone, even at equal score; (2) prefer
      the better (lowest) individual rank the chunk achieved in any list it
      appeared in; (3) fall back to `chunk_id` ascending as a pure-determinism
      floor, since two chunks truly tied on every real signal need *some*
      reproducible order that doesn't depend on caller argument order. This
      sits below the existing tier-1 exact-match pinning (bug #5) -- pinning
      still governs which chunks are "hard" winners; this tiebreak only
      resolves ordering *within* the RRF-merged remainder
    → verified with 3 new unit tests directly against `_rrf_merge` (no DB
      needed, since RRF merge is pure Python): multi-list agreement beats a
      better single-list rank, best-rank breaks a tie when appearance counts
      match, and id-ascending breaks a fully-tied case. Also re-ran the full
      existing test suite before and after (via `git stash`) to confirm the
      6 pre-existing failures (`test_graph.py`'s async tests +
      `test_retrieval.py`'s two DB-fixture tests, all a known pytest-asyncio
      cross-module event-loop artifact per bugs #12/#19/#32) are identical on
      both the pre- and post-change code -- this fix introduces zero new
      failures. `validate_citations.py` matches citations by
      `(file_path, start_line, end_line)` set membership, not by result
      order, so the tiebreak reordering the merged list cannot affect
      citation validation.

38. **Asked to "package the existing add-language-support skill into a plugin" — it didn't exist anywhere**
    → this task's own framing assumed prior work existed; treated that as
      a claim to verify, not a given
    → searched everywhere it could plausibly live: this repo (no `.claude/`
      directory at all), `~/.claude/skills/` (only the default bundled
      skills under `synced/`, nothing custom), every other project
      directory on the machine, `~/.claude/plugins/` -- genuinely nothing,
      confirmed via direct filesystem search rather than one quick look
    → asked the user directly rather than fabricating a plausible-looking
      skill and presenting it as "the existing one" -- they confirmed:
      build it fresh, then package it. Proceeded on that explicit basis,
      not a guess.

39. **JS chunker: a top-level JSDoc comment's text was duplicated into the synthetic `<module>` chunk**
    → assumed extracting a `/** ... */` comment as a declaration's
      docstring was the whole fix needed (mirroring how Python's
      `ast.get_docstring()` pulls a docstring that's genuinely *inside*
      the function/class node's own line range)
    → actually a JS doc-comment is a separate SIBLING node in the parse
      tree, sitting just *before* the declaration it documents -- its own
      line range was never added to `claimed_ranges`, so those lines
      stayed "unclaimed" and the exact same text leaked a second time into
      the module chunk's content, alongside being correctly extracted as
      the real chunk's `docstring`
    → caught by actually inspecting the module chunk's `content` against
      a real fixture (`tests/fixtures/sample.js`) before writing any
      tests, not by assuming the extraction was complete once the
      docstring field looked right -- `retry`'s and `Config.load`'s JSDoc
      text were both visibly present in the module chunk's dump
    → fixed by having `_extract_jsdoc()` return the comment node's own
      line range alongside the docstring text, and claiming that range
      wherever it's used (top-level function, top-level `const fn = () =>`,
      and class declarations -- method-level JSDoc needed no such fix
      since it's already inside the class's own claimed range). Added a
      regression test (`test_jsdoc_comment_not_duplicated_into_module_chunk`)
      asserting the specific doc text does NOT appear in the module chunk.

40. **Plugin/marketplace install and post-install skill-invocation verification cannot be done by the agent in this session**
    → the task asked to install the plugin locally via the marketplace
      file and confirm the skill is invokable -- assumed this might be
      scriptable somehow (a settings.json field, a CLI flag) before
      claiming otherwise
    → researched directly (not guessed): confirmed there is no `claude`
      CLI binary on this machine's PATH (`which claude` / `Get-Command
      claude` both fail -- this session runs inside a VSCode-native
      extension, not the standalone terminal CLI), and there is no tool
      available to this agent that invokes interactive slash commands like
      `/plugin marketplace add` or `/plugin install` on the user's behalf.
      The only documented, confirmed install path is the human typing
      those two commands into the chat themselves
    → also empirically confirmed (not just inferred from docs) that a
      skill sitting on disk inside an unregistered plugin directory is
      genuinely not invokable: calling the Skill tool with both
      `add-language-support` and the plugin-namespaced
      `add-language-support:add-language-support` before any install both
      returned `Unknown skill` errors
    → not silently worked around: built and verified everything within
      reach without the install (skill content, plugin.json/marketplace.json
      schema verified against the real docs rather than assumed, the
      underlying language-support feature proven end-to-end against the
      real DB/retrieval/citation path -- see the language-addition entries
      around this one), then handed off the two exact commands to the user
      to run themselves, with exactly what to check afterward, rather than
      claiming "installed and verified" without being able to prove it.

41. **Assumed `AsyncAzureOpenAI` was the right client for the Azure branch -- it wasn't, for the endpoint actually provisioned**
    → assumed the standard, most-documented Azure OpenAI Python pattern
      (`AsyncAzureOpenAI(azure_endpoint=..., api_key=..., api_version=...)`)
      would be correct, since that's the client purpose-built for "Azure
      OpenAI" in most docs/training-era material
    → actually the endpoint already provisioned for this project
      (`https://<resource>.services.ai.azure.com/openai/v1`) is Microsoft's
      newer unified Foundry v1 API surface, which is plain
      OpenAI-API-compatible and explicitly does NOT want `api_version`/
      `azure_endpoint` -- the documented current pattern for it is the
      plain `AsyncOpenAI` class with `base_url` set to that same `.../v1`
      address. Confirmed directly against Microsoft Learn's migration doc
      before writing any code, not carried over from memory
    → separately verified the `openai` Python package itself before
      trusting any of this: `pip install openai` resolves to 3.16.2, a
      major-version jump from the 1.x series most documentation (including
      the docs used to confirm the point above) still describes.
      Confirmed via `inspect.signature()` on the real installed
      `AsyncOpenAI.__init__` and `client.chat.completions.create` that the
      `api_key`/`base_url` constructor kwargs and `model`/`messages`/
      `stream` call kwargs this code depends on are still present in
      3.16.2 (the constructor also gained several new Azure/Foundry-
      specific kwargs like `provider` and `workload_identity` not in the
      1.x series -- unused here, but confirms this really is a materially
      different surface, not just a version-number bump)
    → fixed by using `AsyncOpenAI(api_key=AZURE_OPENAI_API_KEY,
      base_url=AZURE_OPENAI_ENDPOINT)` in `generate.py::_answer_azure`,
      not `AsyncAzureOpenAI`. Verified in two stages before calling this
      done: (1) a mocked-client test confirming request assembly (right
      api_key/base_url on the client, right deployment name as `model`)
      and response parsing + citation validation all work together
      (real citation kept, fabricated one stripped) -- same proof pattern
      as Bedrock's BUGLOG #20; (2) a REAL live call against the actual
      provisioned endpoint and `gpt-4.1-mini` deployment, which succeeded
      on the first attempt and returned a correctly-grounded,
      correctly-cited answer (`[utils.py:10-15]`) that survived real
      (not mocked) citation validation.

## Design decision -- Azure AI Foundry as a third `provider` option

- **Model: `gpt-4.1-mini`**, per explicit direction after reviewing real,
  current (verified against Microsoft's own pricing page and cross-checked
  Microsoft Q&A pricing threads, not assumed) alternatives. `GPT-4o-mini`
  -- the original suggestion -- no longer appears in Azure OpenAI's current
  model catalog at all as of this check; it's been superseded by the
  GPT-5.x line. Of the two current low-cost candidates surfaced
  (`GPT-5-nano` at $0.05/$0.40 per 1M tokens, positioned by Microsoft for
  classification/tagging/routing rather than open-ended generation; and
  `GPT-5-mini` at $0.25/$2.00 per 1M tokens, general-purpose with
  reasoning/tool-calling), `gpt-4.1-mini` was the deployment actually
  provisioned and confirmed live-working.
- **SDK: `openai` package's `AsyncOpenAI` (base_url-pointed), not
  `azure-ai-inference` and not `AsyncAzureOpenAI`.** Same "raw vendor SDK,
  not a framework wrapper" choice already made for Bedrock (`boto3` over
  LangChain) -- `openai` is the vendor-native client for this exact model
  family, same role `boto3` plays for Bedrock. `AsyncAzureOpenAI`
  specifically was considered and rejected once the provisioned endpoint
  turned out to be the newer unified Foundry v1 surface rather than the
  classic versioned Azure OpenAI API -- see bug #41 above for the concrete
  verification trail, not just a stated preference.
- **No env-var toggle, no auto-fallback between providers.** `provider` is
  a per-request argument on `answer_question()`/`/ask`, same as Bedrock.
  `AZURE_OPENAI_API_KEY`/`AZURE_OPENAI_ENDPOINT`/`AZURE_OPENAI_DEPLOYMENT`
  env vars configure *how* to call Azure when `provider="azure"` is
  chosen -- they never cause Azure to be silently selected on their own,
  matching `BEDROCK_MODEL_ID`'s existing role for Bedrock exactly.
- **Non-streaming, matching Groq/Bedrock's actual existing behavior.**
  The task that prompted this referenced a `stream_answer()` function and
  "matching established streaming behavior" -- neither exists. The real
  function is `generate.py::answer_question()`, and Groq/Bedrock both
  make one blocking call and return a complete string; there is no
  streaming anywhere in this pipeline yet. Confirmed this directly against
  the file before writing anything, flagged it, and the explicit decision
  (confirmed) was to keep the Azure branch consistent with the *real*
  existing pattern (one non-streaming call) rather than introduce
  streaming for the first time as a side effect of this change.
- **Billing/credit verification done before any live call, not after --
  a real cost-risk check, not just a technical one.** Confirmed (web
  search against current sources, not assumed) that Azure's $200/30-day
  free-account credit does apply to Azure OpenAI Service usage on native
  models. The specific trap flagged going in -- Azure AI Foundry's unified
  model catalog surfacing third-party/partner models (e.g. Claude,
  Llama) alongside native Azure OpenAI models, with partner-model usage
  not always covered by free credits -- applies to those partner catalog
  models specifically, not to native Azure OpenAI models like
  `gpt-4.1-mini`; picking a native model sidesteps that exact trap.
  Separately, the live call succeeding on the first attempt (bug #41)
  confirmed there was no additional quota/access-approval gate blocking
  this specific account/deployment either -- unlike Bedrock, which is
  still genuinely blocked on pending AWS account verification (BUGLOG
  #21), Azure hit no such blocker here.

42. **Writing CLAUDE.md from the actual code (not the README) surfaced several places the existing docs overstated or mis-described reality**
    → assumed the README's description of the system was accurate enough
      to base repo guidance on -- it reads as a complete, integrated
      pipeline, and has a "LangGraph Agent" section describing the agent as
      built "on top of" the existing RAG pipeline
    → actually checked each claim against the code before writing it down,
      and found real gaps between what the docs imply and what's wired up:
      (1) `graph.py`'s LangGraph agent is NOT reachable from any FastAPI
      route -- `app.py` has exactly three routes (`/`, `/ingest`, `/ask`)
      and never imports `graph.py`; it's only invokable via a direct Python
      call, `tests/test_graph.py` (mocked LLM), or the standalone
      `test_live_agent.py` script. (2) `validate_citations()` (the
      structured valid/invalid report function) is dead code in production
      -- only `strip_invalid_citations()` is ever called by `generate.py`/
      `graph.py`; the report function is exercised solely by its own test.
      (3) The citation "retry loop" the task description asked about only
      exists inside `graph.py`'s `finalize` node -- the live `/ask` path
      (`generate.py`) is single-pass strip-and-return with no retry at all.
      (4) No ANN index (IVFFlat or otherwise) exists on `chunks.embedding` --
      only one Alembic migration exists, whose own comment explicitly
      defers the index; semantic search is an exact sequential scan.
      (5) The local `.env` sets `EMBEDDINGS_PROVIDER=stub`, so local runs of
      the app itself (not just tests) currently use hash-based
      pseudo-embeddings, not real FastEmbed vectors
    → fixed by writing CLAUDE.md around what the code actually does, with an
      explicit "Not implemented / not live" section listing exactly these
      gaps so a future session doesn't assume the docs' framing is the
      wired-up reality. Deliberately did NOT rewrite the README's
      description of these items in the same commit -- fixing docs was out
      of scope for a "write CLAUDE.md" task -- which is why the later docs
      update (README + master project record) applies its own
      strikethrough-and-annotate convention instead of silent rewrites

43. **Master project record: assumed the wrong section numbers for the new Step entries, caught before finalizing**
    → while updating `revision/codebase_qa_complete_master_project_record
      (1).md`, wrote a round of cross-references ("see Section 33/34/35")
      for the new RRF/JS/Azure steps based on a guess about where the
      numbering left off, before ever checking the file's actual heading
      sequence
    → actually the document's top-level sections ran through 30 (Step 4),
      so the next three are 31/32/33 -- my references were off by two in
      every case, and worse, one wrong number (33) collided with what would
      legitimately become a real section number, so a naive find-and-replace
      would have corrupted the correct references too
    → caught by grepping the real `^# [0-9]` headings and grepping my own
      references against them BEFORE appending the new sections, not after.
      Fixed with a two-phase placeholder swap (33->tmp, 34->tmp, 35->tmp,
      then tmp->31/32/33) so no substitution could clobber another's
      output, and re-grepped afterward to confirm every reference resolved
      to a section that actually exists

## Design decision -- documentation update convention (README + master record)

- **Superseded statements are struck through and annotated, never deleted.**
  When new work made an old doc statement false or incomplete ("Python
  only," "supports two LLM providers," "RRF ties broken by insertion
  order"), the old text stays visible as `~~old text~~` with a bold
  "updated (Step N)" note explaining what replaced it and where to read
  more -- rather than being silently rewritten. Chose this after
  confirming with the user that it's the intended convention; it also
  matches a pattern the README's own Future Roadmap section was already
  using (`~~item~~ -- implemented, see X`), extended consistently to every
  section that needed it rather than applied in one place only.
- **Brand-new capabilities are added as fresh content, not struck-through
  placeholders.** Strikethrough is only for statements that WERE true or
  pending and are now superseded -- not a decoration for things that were
  never previously stated (e.g. the RRF tiebreak wasn't previously a
  roadmap item, so it gets a new subsection, not a fabricated "old" line to
  cross out).
- **Original diagrams kept as drawn, annotated below rather than redrawn.**
  The architecture ASCII diagrams (README and master record) predate the
  JS and Azure work; they're still accurate for the default path
  (Python + Groq), so each got an italic note beneath it pointing to what's
  changed, instead of a redraw that would erase the original.