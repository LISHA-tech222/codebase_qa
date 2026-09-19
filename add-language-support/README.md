# add-language-support

A Claude Code plugin for [codebase_assistant](https://github.com/LISHA-tech222/codebase_qa) that packages the `add-language-support` skill: a repeatable, verified procedure for adding a new source-code language to this project's AST-based chunking/ingestion pipeline (`chunker.py`, `run_on_repo.py`, `ingest.py`).

## What's in here

```
add-language-support/
├── .claude-plugin/
│   ├── plugin.json         # plugin manifest
│   └── marketplace.json    # self-hosting local marketplace (source: ".") for local install/testing
├── skills/
│   └── add-language-support/
│       └── SKILL.md
├── commands/
│   └── add-language.md     # /add-language-support:add-language <language>
└── README.md
```

## Why this exists

`chunker.py` was Python-only until JavaScript support (`chunker_js.py`, in the parent repo) was added as the reference implementation this skill is modeled on. Adding a third language by re-deriving the same steps from scratch — pick a parser, inspect its real node types, wire the dispatcher, don't forget the doc-comment-leaking-into-the-module-chunk trap, verify against the real DB and `hybrid_search()`, not just the chunker in isolation — is exactly the kind of repeatable procedure a skill should own instead of being re-discovered (or re-forgotten) each time.

## Installing locally

From inside a Claude Code session in this repository:

```
/plugin marketplace add ./add-language-support
/plugin install add-language-support@add-language-support-local
```

(`add-language-support-local` is this plugin's self-hosting marketplace name, from `.claude-plugin/marketplace.json` — the plugin and its marketplace live in the same directory, which is why `source` in `marketplace.json` is `"."` rather than pointing at a separate plugins subfolder.)

Once installed, the skill is namespaced as `add-language-support:add-language-support`, and the bundled command is `/add-language-support:add-language <language-name>`.

## Usage

Either let the skill auto-trigger from a request like "add Go support to the chunker", or invoke the command directly:

```
/add-language-support:add-language TypeScript
```

See `BUGLOG.md` in the parent repo for the verification trail — both the original (pre-packaging) JavaScript implementation and, once packaging was verified, whatever language was added next through the installed plugin.
