"""
Live, unmocked test of the LangGraph agent -- real Groq API, real
tool-calling decisions, real critic review.
"""
import asyncio
import uuid
import graph
from langgraph.errors import GraphRecursionError


async def main():
    repo_id = "nlp-ml-pipeline"  # keep whatever you used before
    question = "What does preprocess.py do?"
    thread_id = str(uuid.uuid4())
    print("thread_id:", thread_id)

    try:
        result = await graph.ask_agent(
            question=question, repo_id=repo_id, top_k=5, thread_id=thread_id,
        )
    except GraphRecursionError:
        print()
        print("Hit the recursion limit -- inspecting what actually happened...")
        compiled_graph = await graph.get_graph()
        state = await compiled_graph.aget_state({"configurable": {"thread_id": thread_id}})

        print()
        print("=== FULL MESSAGE TRACE UP TO THE LIMIT ===")
        for i, m in enumerate(state.values["messages"]):
            role = type(m).__name__
            tool_calls = getattr(m, "tool_calls", None)
            print(f"[{i}] {role}: {str(m.content)[:200]}")
            if tool_calls:
                print(f"     -> tool_calls: {tool_calls}")

        print()
        print("chunks accumulated:", len(state.values.get("chunks", [])))
        print("critic_rejection_count:", state.values.get("critic_rejection_count"))
        print("validation_failure_count:", state.values.get("validation_failure_count"))
        print("next node that would run:", state.next)
        return

    print("=== FINAL ANSWER ===")
    print(result.get("answer"))
    print("sources:", result.get("sources"))


asyncio.run(main())