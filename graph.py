"""
LangGraph orchestration for the Codebase Q&A pipeline.

Step 4a built a basic linear graph (plan_query -> retrieve -> synthesize)
to verify the MCP-client wiring and checkpointing worked before adding
any real decision-making. This module now replaces that structure
entirely with a real tool-calling (ReAct-style) agent, per the plan:
reasoner <-> tools, looping until the model itself decides it has
enough information to answer, then finalize (citation validation).

The "retrieve again or answer now" decision is made by the LLM via
real function-calling -- not a hand-designed should_retry rule. The
model is bound one real tool (search_codebase); no second tool was
invented just to make "and any other available action" look fuller
than it honestly is right now.

Security-relevant design choice: repo_id and top_k are hidden from the
LLM's tool-calling schema entirely, using LangGraph's InjectedState --
the model never sees or chooses them, they're injected from graph
state at execution time. This keeps Step 1's repo_id isolation
guarantee structurally true (the code enforces it) rather than
model-behavior-dependent (trusting the LLM to always repeat the
correct repo_id correctly across a multi-turn reasoning loop).

mcp_server.py runs on mcp 1.x's FastMCP API (downgraded from Step 1's
mcp 2.x MCPServer) because langchain-mcp-adapters hard-pins mcp<2.0.0.
See BUGLOG / master record Step 4 for the full reasoning.
"""

import sys
import os
import json
import operator
from typing import Annotated, TypedDict

from pydantic import BaseModel, Field
from langchain_core.tools import tool
from langchain_core.tools.base import InjectedToolCallId
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import InjectedState, ToolNode, tools_condition
from langgraph.types import interrupt
from langchain_mcp_adapters.client import MultiServerMCPClient
from langfuse import observe

from validate_citations import strip_invalid_citations
from checkpointer import get_checkpointer

GROQ_MODEL = "openai/gpt-oss-20b"  # confirmed supports native tool/function calling

# Recursion limit for the reasoner<->tools<->critic<->finalize graph.
#
# A healthy run with one critic retry and one citation-validation retry
# plus a couple of tool calls can legitimately take ~10-11 node hops
# before finishing (confirmed by walking through real traces during
# testing). 25 gives real headroom above that without being unbounded.
#
# Tested directly, not left theoretical: forcing a reasoner that never
# stops calling tools (recursion_limit temporarily set to 6 for the
# test) raises langgraph.errors.GraphRecursionError with a clear
# message ("Recursion limit of N reached without hitting a stop
# condition..."). The checkpoint is NOT discarded when this happens --
# aget_state() after the error showed all messages/chunks accumulated
# up to that point were preserved, with state.next correctly pointing
# at the node that would run next. Confirmed this is genuinely
# resumable, not just inspectable: re-invoking with a higher
# recursion_limit on the same thread_id continued from exactly where
# it left off (same accumulated chunks carried forward) rather than
# restarting from scratch.
DEFAULT_RECURSION_LIMIT = 25

REASONER_SYSTEM_PROMPT = """You are a codebase Q&A agent. You have access to a \
search_codebase tool that searches an already-ingested code repository.

You decide, on your own, whether to search, search again with a different \
query, or answer now:
- If you don't have enough information yet, or your first search didn't \
return what you needed, call search_codebase again with a genuinely \
DIFFERENT query -- never repeat the same or a near-identical query twice.
- Once you have enough retrieved code to answer confidently, respond with \
your final answer directly (no more tool calls). Do not keep searching \
"just in case" once you have relevant results -- use what you have.

Rules for your final answer:
- Answer ONLY using information from search_codebase's results. If nothing \
relevant was found after searching, say so explicitly -- do not guess.
- Every factual claim must be immediately followed by a citation in the \
exact format [file_path:start_line-end_line], copied verbatim from a \
result you actually received. Do not invent line numbers.
- Do not cite a file/line range you were not actually given.
"""

# Real, code-enforced safety cap on tool calls, distinct from the graph's
# recursion_limit. Confirmed via live testing (not a hypothetical) that
# gpt-oss-20b can get stuck re-issuing the same or near-identical search
# repeatedly (observed: "configuration parser" called 4 times verbatim in
# a row) without ever deciding it had enough to answer -- hitting
# recursion_limit and crashing the whole request rather than degrading
# gracefully. After this many searches, the reasoner is called WITHOUT
# tool-binding at all (the model literally cannot call the tool again),
# forcing a text answer from whatever was actually retrieved.
MAX_SEARCHES = 5


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    repo_id: str
    top_k: int
    chunks: Annotated[list[dict], operator.add]  # accumulated across every tool call
    search_count: Annotated[int, operator.add]
    validation_failure_count: Annotated[int, operator.add]
    needs_retry: bool
    critic_rejection_count: Annotated[int, operator.add]
    needs_critic_retry: bool
    critic_feedback: str | None
    answer: str
    sources: list[str]


_mcp_client: MultiServerMCPClient | None = None
_query_codebase_tool = None


def _mcp_server_connection() -> dict:
    """
    Connection config for the codebase-qa MCP server, spawned as a
    subprocess over stdio. EMBEDDINGS_PROVIDER is forwarded from this
    process's own environment so tests can pass EMBEDDINGS_PROVIDER=stub
    through -- there's no way to monkeypatch a separate OS process's
    imports (see embed.py's Step 4 note).
    """
    env = {}
    if "DATABASE_URL" in os.environ:
        env["DATABASE_URL"] = os.environ["DATABASE_URL"]
    if "EMBEDDINGS_PROVIDER" in os.environ:
        env["EMBEDDINGS_PROVIDER"] = os.environ["EMBEDDINGS_PROVIDER"]
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")],
        "env": env,
    }


async def _get_mcp_query_codebase_tool():
    """Lazy singleton -- one long-lived MCP client/subprocess, reused across calls."""
    global _mcp_client, _query_codebase_tool
    if _query_codebase_tool is None:
        _mcp_client = MultiServerMCPClient({"codebase-qa": _mcp_server_connection()})
        tools = await _mcp_client.get_tools()
        _query_codebase_tool = {t.name: t for t in tools}["query_codebase"]
    return _query_codebase_tool


def _format_chunks_for_agent(chunks: list[dict]) -> str:
    """
    Format retrieved chunks as real, readable content for the reasoner to
    read and cite from -- matches generate.py's format_context() pattern.
    """
    if not chunks:
        return "No results found for this query."
    blocks = []
    for c in chunks:
        blocks.append(f"### {c['citation']} ({c['symbol_name']})\n```python\n{c['content']}\n```")
    return "\n\n".join(blocks)


@tool
@observe(as_type="tool")
async def search_codebase(
    query: str,
    repo_id: Annotated[str, InjectedState("repo_id")],
    top_k: Annotated[int, InjectedState("top_k")],
    tool_call_id: Annotated[str, InjectedToolCallId],
):
    """Search the ingested codebase for code relevant to a query. Call this
    as many times as you need, with different queries, until you have
    enough information to answer."""
    mcp_tool = await _get_mcp_query_codebase_tool()
    result = await mcp_tool.ainvoke({"query": query, "repo_id": repo_id, "top_k": top_k})
    # each MCP content block's `text` field is a JSON string for ONE chunk
    # (confirmed by direct testing in Step 4a, not assumed)
    from langgraph.types import Command
    new_chunks = [json.loads(block["text"]) for block in result]
    # BUG FIX (found via live testing): the ToolMessage previously only
    # said "Found N results" -- the reasoner never actually received the
    # retrieved code, only a count. It was answering blind, which is
    # exactly why it correctly (and honestly) said it didn't have the
    # contents of a file it had genuinely "found" but never been shown.
    summary = f"Found {len(new_chunks)} result(s) for {query!r}:\n\n{_format_chunks_for_agent(new_chunks)}"
    return Command(update={
        "chunks": new_chunks,
        "search_count": 1,
        "messages": [ToolMessage(content=summary, tool_call_id=tool_call_id)],
    })


@observe(as_type="agent")
async def reasoner(state: AgentState) -> dict:
    """
    Groq's model can occasionally emit malformed tool-call JSON -- a real,
    observed failure mode confirmed via live testing (groq.BadRequestError,
    code 'tool_use_failed', e.g. a stray comma/quote in the generated
    arguments), not a hypothetical edge case. This is transient generation
    noise from the model itself, distinct from the citation/critic retry
    loops elsewhere in this graph -- those retry because the ANSWER's
    content had a problem; this retries because the API call itself failed
    to produce parseable output. Bounded to a few attempts, doesn't touch
    the graph's own recursion_limit since it happens inside one node call,
    not as an extra graph step.
    """
    llm_plain = ChatGroq(model=GROQ_MODEL, api_key=os.environ["GROQ_API_KEY"])
    messages = state["messages"]
    if not messages:
        messages = [SystemMessage(content=REASONER_SYSTEM_PROMPT)]

    search_count = state.get("search_count", 0)
    if search_count >= MAX_SEARCHES:
        # Hard stop: the tool is not bound at all here, so the model
        # literally cannot call it again -- this isn't a prompt request
        # it could ignore, it's removed from what's possible.
        llm = llm_plain
        messages = messages + [HumanMessage(content=(
            f"You have already searched {search_count} times. Stop searching now. "
            "Answer using only the code you've already retrieved, even if it feels "
            "incomplete. If you genuinely don't have enough information, say so "
            "explicitly rather than guessing."
        ))]
    else:
        llm = llm_plain.bind_tools([search_codebase])

    from groq import BadRequestError

    max_attempts = 3
    last_error = None
    for attempt in range(max_attempts):
        is_final_attempt = attempt == max_attempts - 1
        # Escalating fallback: if tool-binding itself keeps producing
        # malformed generations (confirmed via live testing -- both plain
        # malformed JSON and "tool choice is none, but model called a
        # tool" have been observed from this model), the FINAL attempt
        # drops tools entirely and tells the model so, guaranteeing this
        # always ends in a valid plain-text response rather than
        # exhausting all retries and crashing the whole request.
        if is_final_attempt and llm is not llm_plain:
            current_llm = llm_plain
            current_messages = messages + [HumanMessage(content=(
                "Tool access is temporarily unavailable. Answer using only "
                "the code you've already been given in this conversation, "
                "even if it's incomplete. If you genuinely don't have enough "
                "information, say so explicitly rather than guessing."
            ))]
        else:
            current_llm = llm
            current_messages = messages
        try:
            response = await current_llm.ainvoke(current_messages)
            return {"messages": [response]}
        except BadRequestError as e:
            last_error = e
            continue
    raise last_error


@observe(as_type="guardrail")
async def finalize(state: AgentState) -> dict:
    """
    Runs once the reasoner decides it's done (no more tool calls). Strips
    any citation the model fabricated that doesn't match a chunk actually
    retrieved across the whole reasoning loop.

    If the answer had an invalid citation, this is a real, escalating
    decision point, not a silent strip-and-return:
    - 1st failure: retry -- loop back to the reasoner with a corrective
      message explaining exactly what was wrong, so it can genuinely try
      again with real information, not just be re-invoked blind.
    - 2nd consecutive failure: pause via interrupt() for human review
      rather than silently returning a twice-flagged answer. Resumes
      from the top of this node when a decision comes back (interrupt()
      re-executes prior logic in the node harmlessly -- stripping
      citations is idempotent -- then returns the resume value instead
      of pausing again).
    """
    last_message = state["messages"][-1]
    raw_answer = last_message.content
    cleaned = strip_invalid_citations(raw_answer, state["chunks"])
    had_invalid_citation = cleaned != raw_answer

    seen = set()
    sources = []
    for c in state["chunks"]:
        if c["citation"] not in seen:
            seen.add(c["citation"])
            sources.append(c["citation"])

    if not had_invalid_citation:
        return {"answer": cleaned, "sources": sources, "needs_retry": False}

    new_failure_count = state.get("validation_failure_count", 0) + 1

    if new_failure_count < 2:
        feedback = HumanMessage(content=(
            "Your previous answer included a citation that doesn't match any "
            "code you actually retrieved. Please provide your final answer "
            "again, using ONLY citations for files/lines you were genuinely "
            "given by search_codebase."
        ))
        return {
            "messages": [feedback],
            "validation_failure_count": 1,
            "answer": cleaned,
            "sources": sources,
            "needs_retry": True,
        }

    decision = interrupt({
        "reason": "Citation validation failed twice in a row for this answer.",
        "flagged_answer": cleaned,
        "sources": sources,
    })
    if decision == "approve":
        return {"answer": cleaned, "sources": sources, "validation_failure_count": 1, "needs_retry": False}
    return {
        "answer": "This answer was flagged for repeated citation issues and was not approved for delivery.",
        "sources": sources,
        "validation_failure_count": 1,
        "needs_retry": False,
    }


class CritiqueResult(BaseModel):
    approved: bool
    feedback: str
    missing_aspects: list[str] = Field(default_factory=list)


CRITIC_SYSTEM_PROMPT = """You are a critical reviewer for a codebase Q&A agent's \
draft answer. You do not have access to search tools -- you only see the draft \
answer and the actual retrieved code chunks it was based on.

Evaluate whether the draft answer:
- actually addresses the user's question
- is substantively grounded in the retrieved code (not just citation-format
  correct -- does the explanation genuinely follow from what was retrieved)
- doesn't ignore something clearly relevant that was retrieved

Respond with structured feedback: whether you approve, a specific explanation,
and any specific missing aspects the answer should have covered.
"""


@observe(as_type="evaluator")
async def critic(state: AgentState) -> dict:
    """
    A genuinely separate second agent, not a relabeled version of citation
    validation. Different model role (substance review, not format checking),
    no tool access (it critiques, it doesn't retrieve), structured output
    (approved/feedback/missing_aspects), and its feedback is a real hand-off
    that changes the reasoner's next action -- not a pass/fail gate.

    Bounded to one retry: if the critic still isn't satisfied after the
    reasoner's revision, proceed to finalize anyway rather than looping
    indefinitely or adding a second interrupt gate on top of citation
    validation's -- keeps this a small, real addition, not a second
    open-ended escalation path.
    """
    llm = ChatGroq(model=GROQ_MODEL, api_key=os.environ["GROQ_API_KEY"]).with_structured_output(CritiqueResult)
    draft_answer = state["messages"][-1].content
    chunks_summary = "\n".join(f"- {c['citation']}: {c.get('docstring') or ''}" for c in state["chunks"])
    review_prompt = f"Draft answer:\n{draft_answer}\n\nRetrieved code chunks:\n{chunks_summary}"

    result = await llm.ainvoke([
        SystemMessage(content=CRITIC_SYSTEM_PROMPT),
        HumanMessage(content=review_prompt),
    ])

    already_retried_once = state.get("critic_rejection_count", 0) >= 1

    if result.approved or already_retried_once:
        return {
            "critic_rejection_count": 0 if result.approved else 1,
            "critic_feedback": None if result.approved else result.feedback,
            "needs_critic_retry": False,
        }

    feedback_parts = [f"A reviewer found an issue with your draft answer: {result.feedback}"]
    if result.missing_aspects:
        feedback_parts.append(f"Missing: {', '.join(result.missing_aspects)}.")
    feedback_parts.append("Please revise your final answer to address this.")

    return {
        "messages": [HumanMessage(content=" ".join(feedback_parts))],
        "critic_rejection_count": 1,
        "needs_critic_retry": True,
    }


def route_after_critic(state: AgentState) -> str:
    return "reasoner" if state.get("needs_critic_retry") else "finalize"


def route_after_finalize(state: AgentState) -> str:
    """finalize already decided what happened; this just reads the result."""
    return "reasoner" if state.get("needs_retry") else "__end__"


async def build_graph():
    checkpointer = await get_checkpointer()

    graph = StateGraph(AgentState)
    graph.add_node("reasoner", reasoner)
    graph.add_node("tools", ToolNode([search_codebase]))
    graph.add_node("critic", critic)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "reasoner")
    graph.add_conditional_edges("reasoner", tools_condition, {"tools": "tools", "__end__": "critic"})
    graph.add_edge("tools", "reasoner")
    graph.add_conditional_edges("critic", route_after_critic, {"reasoner": "reasoner", "finalize": "finalize"})
    graph.add_conditional_edges("finalize", route_after_finalize, {"reasoner": "reasoner", "__end__": END})

    return graph.compile(checkpointer=checkpointer)


_compiled_graph = None


async def get_graph():
    """Lazy singleton, same pattern as the MCP client/tool above."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = await build_graph()
    return _compiled_graph


@observe()
async def ask_agent(question: str, repo_id: str, top_k: int, thread_id: str) -> dict:
    """
    Entry point: runs the agent for one question, seeded with the user's
    question as the first HumanMessage after the system prompt.
    """
    graph = await get_graph()
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": DEFAULT_RECURSION_LIMIT,
    }
    initial_state = {
        "messages": [SystemMessage(content=REASONER_SYSTEM_PROMPT), HumanMessage(content=question)],
        "repo_id": repo_id,
        "top_k": top_k,
        "chunks": [],
    }
    return await graph.ainvoke(initial_state, config=config)