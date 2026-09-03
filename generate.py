"""
Send retrieved chunks to an LLM, require citations in file_path:start-end
format. This is the real code you keep — run it with your own Groq API
key (export GROQ_API_KEY=...). Groq's free tier requires no credit card
and is OpenAI-API-compatible, so this uses the `groq` package's
Anthropic-adjacent chat completions interface.
"""

import os
from groq import AsyncGroq

MODEL = "openai/gpt-oss-20b"  # solid general-purpose free-tier model

SYSTEM_PROMPT = """You are a codebase Q&A assistant. You will be given a \
question and a set of code chunks retrieved from the repository, each \
labeled with its file path and line range.

Rules:
- Answer ONLY using the provided chunks. If the chunks don't contain \
enough information to answer, say so explicitly — do not guess.
- Every factual claim about the code must be immediately followed by a \
citation in the exact format [file_path:start_line-end_line], copied \
verbatim from the chunk label it came from. Do not invent line numbers.
- If a claim draws on multiple chunks, cite all of them: \
[file_a.py:1-5][file_b.py:10-20]
- Do not cite a file/line range that wasn't given to you.
"""


def format_context(chunks: list[dict]) -> str:
    """
    chunks: list of dicts with file_path, symbol_name, start_line,
    end_line, content (as returned by hybrid_search, reshaped into dicts).
    """
    blocks = []
    for c in chunks:
        label = f"{c['file_path']}:{c['start_line']}-{c['end_line']}"
        blocks.append(
            f"### {label} ({c['symbol_name']})\n```python\n{c['content']}\n```"
        )
    return "\n\n".join(blocks)


async def answer_question(question: str, chunks: list[dict]) -> str:
    client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    context = format_context(chunks)

    response = await client.chat.completions.create(
        model=MODEL,
        max_tokens=1000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Retrieved code:\n\n{context}\n\nQuestion: {question}",
            },
        ],
    )
    raw_answer = response.choices[0].message.content

    # Strip any citation the model fabricated that doesn't correspond to
    # a chunk we actually retrieved and gave it — see validate_citations.py
    from validate_citations import strip_invalid_citations
    return strip_invalid_citations(raw_answer, chunks)