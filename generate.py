"""
Send retrieved chunks to an LLM, require citations in file_path:start-end
format. Two providers are supported, chosen per-request via the
`provider` argument:

- "groq" (default): Groq's free tier, openai/gpt-oss-20b, via AsyncGroq.
- "bedrock": AWS Bedrock, via boto3's converse() API. boto3 has no
  official async client, so the sync call is offloaded with
  asyncio.to_thread() rather than awaited directly -- calling it
  straight from an async def would block the event loop the same way a
  stray psycopg2 call would have before Step 0. Verified this
  offloading genuinely doesn't block (see BUGLOG / master record
  Step 2).

Why converse() over invoke_model(): converse() gives one standardized
request/response shape across Bedrock model providers (Anthropic, Meta,
Cohere, etc.) -- switching models later is a config change, not a
rewrite of provider-specific body parsing. invoke_model() would be the
right call only if a target model isn't yet supported by converse(), or
a model-specific parameter isn't exposed by the unified API -- neither
applies here.
"""

import os
import asyncio

import boto3
from groq import AsyncGroq

GROQ_MODEL = "openai/gpt-oss-20b"  # solid general-purpose free-tier model
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")

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


async def answer_question(question: str, chunks: list[dict], provider: str = "groq") -> str:
    context = format_context(chunks)

    if provider == "groq":
        raw_answer = await _answer_groq(question, context)
    elif provider == "bedrock":
        raw_answer = await _answer_bedrock(question, context)
    else:
        raise ValueError(f"Unknown provider: {provider!r}. Expected 'groq' or 'bedrock'.")

    # Strip any citation the model fabricated that doesn't correspond to
    # a chunk we actually retrieved and gave it — see validate_citations.py
    from validate_citations import strip_invalid_citations
    return strip_invalid_citations(raw_answer, chunks)


async def _answer_groq(question: str, context: str) -> str:
    client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    response = await client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=1000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Retrieved code:\n\n{context}\n\nQuestion: {question}",
            },
        ],
    )
    return response.choices[0].message.content


def _bedrock_converse_sync(question: str, context: str) -> str:
    """
    Plain sync boto3 call. boto3 has no official async client, so this
    function must only ever be run via asyncio.to_thread() (see
    _answer_bedrock below) -- calling it directly from an async def
    would block the event loop.
    """
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    response = client.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[
            {
                "role": "user",
                "content": [{"text": f"Retrieved code:\n\n{context}\n\nQuestion: {question}"}],
            }
        ],
        inferenceConfig={"maxTokens": 1000},
    )
    return response["output"]["message"]["content"][0]["text"]


async def _answer_bedrock(question: str, context: str) -> str:
    return await asyncio.to_thread(_bedrock_converse_sync, question, context)