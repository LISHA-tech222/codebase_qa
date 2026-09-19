---
description: Add support for a new source-code language to codebase_assistant's chunking/ingestion pipeline
argument-hint: [language-name]
---

Add end-to-end support for the **$ARGUMENTS** language to this project's
chunking/ingestion pipeline. Follow the `add-language-support` skill's
procedure exactly — do not skip its verification steps (unit tests against
the chunker alone are not sufficient; the skill requires proving the new
language works through a real DB insert, `hybrid_search()`, and citation
validation, and logging the result in `BUGLOG.md`).

If `$ARGUMENTS` is empty, ask which language to add before doing anything
else — do not guess one.
