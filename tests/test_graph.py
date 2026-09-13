"""
CI tests for the LangGraph agent (Step 4) -- forced tool-call, forced
interrupt, and forced handoff paths. These were originally verified via
one-off scripts during development; per plan item 7, the conditional
branches move into the real suite so they can't silently break later
without CI catching it.

All Groq calls are mocked (langchain_groq.ChatGroq.ainvoke for the
reasoner, ChatGroq.with_structured_output for the critic) -- these
tests verify the graph's control flow and real MCP/DB integration, not
Groq's actual model behavior, which only a live API key can prove
(see BUGLOG for that honestly-flagged boundary).

The MCP server subprocess spawned by graph.py's MultiServerMCPClient
uses EMBEDDINGS_PROVIDER=stub (set in test.yml's CI env and this
project's local .env for testing) since real FastEmbed needs
huggingface.co access CI doesn't have -- and since it's a genuinely
separate OS process, this project's usual monkeypatching approach
can't reach it (see embed.py's Step 4 note).
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uuid
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from tests.test_ingestion import _insert_chunk
from chunker import Chunk
import graph


def _seed_chunk(db_conn, repo_id, file_path, symbol_name, start_line, end_line, docstring, content):
    """Insert one test chunk directly (bypasses real embedding -- retrieval
    falls back to semantic search over the stub vector regardless, and
    exact-match search works on symbol_name text matching either way)."""
    c = Chunk(
        file_path=file_path, symbol_name=symbol_name, symbol_type="method",
        start_line=start_line, end_line=end_line, docstring=docstring, content=content,
    )
    cur = db_conn.cursor()
    _insert_chunk(cur, c, repo_id=repo_id)
    db_conn.commit()


def _fake_critic(result_or_results):
    """A mocked critic client whose .ainvoke() returns the given CritiqueResult(s)."""
    client = MagicMock()
    if isinstance(result_or_results, list):
        client.ainvoke = AsyncMock(side_effect=result_or_results)
    else:
        client.ainvoke = AsyncMock(return_value=result_or_results)
    return client


@pytest.mark.asyncio
async def test_forced_tool_call(db_conn):
    """
    The reasoner decides to call search_codebase (a real MCP call, real
    DB), then answers using the real retrieved chunk's citation.
    """
    _seed_chunk(db_conn, "ci_repo_a", "utils.py", "reset_all", 10, 15,
                "Resets all styling", "def reset_all(self): ...")

    tool_call = AIMessage(content="", tool_calls=[
        {"name": "search_codebase", "args": {"query": "reset_all"}, "id": "c1"}
    ])
    final_answer = AIMessage(content="reset_all resets all styling [utils.py:10-15].", tool_calls=[])

    with patch("langchain_groq.ChatGroq.ainvoke", new=AsyncMock(side_effect=[tool_call, final_answer])), \
         patch("langchain_groq.ChatGroq.with_structured_output",
               return_value=_fake_critic(graph.CritiqueResult(approved=True, feedback="fine"))):
        result = await graph.ask_agent(
            question="what does reset_all do?", repo_id="ci_repo_a", top_k=5, thread_id=str(uuid.uuid4()),
        )

    assert "utils.py:10-15" in result["answer"]
    assert len(result["chunks"]) >= 1
    assert "__interrupt__" not in result


@pytest.mark.asyncio
async def test_forced_interrupt_and_resume(db_conn):
    """
    Two consecutive fabricated citations force finalize's interrupt().
    Verifies both the interrupt payload and that resuming with an
    approval decision correctly delivers the flagged answer.
    """
    _seed_chunk(db_conn, "ci_repo_b", "utils.py", "reset_all", 10, 15,
                "Resets all styling", "def reset_all(self): ...")

    tool_call = AIMessage(content="", tool_calls=[
        {"name": "search_codebase", "args": {"query": "reset_all"}, "id": "c1"}
    ])
    bad_1 = AIMessage(content="does stuff [fake1.py:1-1].", tool_calls=[])
    bad_2 = AIMessage(content="still bad [fake2.py:2-2].", tool_calls=[])

    thread_id = str(uuid.uuid4())
    with patch("langchain_groq.ChatGroq.ainvoke", new=AsyncMock(side_effect=[tool_call, bad_1, bad_2])), \
         patch("langchain_groq.ChatGroq.with_structured_output",
               return_value=_fake_critic(graph.CritiqueResult(approved=True, feedback="fine"))):
        result = await graph.ask_agent(
            question="what does reset_all do?", repo_id="ci_repo_b", top_k=5, thread_id=thread_id,
        )

        assert "__interrupt__" in result
        interrupt_payload = result["__interrupt__"][0].value
        assert "Citation validation failed twice" in interrupt_payload["reason"]

        from langgraph.types import Command
        compiled_graph = await graph.get_graph()
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": graph.DEFAULT_RECURSION_LIMIT}
        resumed = await compiled_graph.ainvoke(Command(resume="approve"), config=config)

    assert "__interrupt__" not in resumed
    assert resumed["answer"]  # delivered, not blocked


@pytest.mark.asyncio
async def test_forced_critic_handoff(db_conn):
    """
    The critic rejects the reasoner's first draft with specific feedback;
    the reasoner's revision (still mocked, but content differs from the
    first draft, demonstrating the handoff informed the next attempt)
    is then approved.
    """
    _seed_chunk(db_conn, "ci_repo_c", "utils.py", "reset_all", 10, 15,
                "Resets all styling", "def reset_all(self): ...")

    tool_call = AIMessage(content="", tool_calls=[
        {"name": "search_codebase", "args": {"query": "reset_all"}, "id": "c1"}
    ])
    vague_answer = AIMessage(content="reset_all does something.", tool_calls=[])
    specific_answer = AIMessage(content="reset_all resets all terminal styling [utils.py:10-15].", tool_calls=[])

    critic_client = _fake_critic([
        graph.CritiqueResult(approved=False, feedback="Too vague.", missing_aspects=["specifics"]),
        graph.CritiqueResult(approved=True, feedback="Now specific."),
    ])

    with patch("langchain_groq.ChatGroq.ainvoke", new=AsyncMock(side_effect=[tool_call, vague_answer, specific_answer])), \
         patch("langchain_groq.ChatGroq.with_structured_output", return_value=critic_client):
        result = await graph.ask_agent(
            question="what does reset_all do?", repo_id="ci_repo_c", top_k=5, thread_id=str(uuid.uuid4()),
        )

    assert "terminal styling" in result["answer"]
    assert result["critic_rejection_count"] == 1


@pytest.mark.asyncio
async def test_recursion_limit_raises_and_checkpoint_survives(db_conn):
    """
    A reasoner that never stops calling tools must hit
    GraphRecursionError -- and the checkpoint must survive it (real
    behavior confirmed during development, not left theoretical; see
    BUGLOG). Uses a low recursion_limit passed directly in this test's
    config, without touching graph.DEFAULT_RECURSION_LIMIT, so this
    doesn't affect any other test's behavior.
    """
    _seed_chunk(db_conn, "ci_repo_d", "utils.py", "reset_all", 10, 15,
                "Resets all styling", "def reset_all(self): ...")

    def infinite_tool_calls():
        i = 0
        while True:
            i += 1
            yield AIMessage(content="", tool_calls=[
                {"name": "search_codebase", "args": {"query": f"q{i}"}, "id": f"c{i}"}
            ])

    thread_id = str(uuid.uuid4())
    with patch("langchain_groq.ChatGroq.ainvoke", new=AsyncMock(side_effect=infinite_tool_calls())), \
         patch("langchain_groq.ChatGroq.with_structured_output",
               return_value=_fake_critic(graph.CritiqueResult(approved=True, feedback="fine"))):
        compiled_graph = await graph.get_graph()
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 4}
        initial_state = {
            "messages": [], "repo_id": "ci_repo_d", "top_k": 5, "chunks": [],
        }
        from langchain_core.messages import SystemMessage, HumanMessage
        initial_state["messages"] = [
            SystemMessage(content=graph.REASONER_SYSTEM_PROMPT),
            HumanMessage(content="what does reset_all do?"),
        ]

        with pytest.raises(GraphRecursionError):
            await compiled_graph.ainvoke(initial_state, config=config)

        state = await compiled_graph.aget_state({"configurable": {"thread_id": thread_id}})
        assert len(state.values["messages"]) > 0
        assert len(state.values["chunks"]) > 0