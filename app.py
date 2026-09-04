"""
Minimal web server wrapping the ingestion + retrieval + generation
pipeline. This is what Render actually runs — without this, there's
nothing for Render to bind to a port and serve.
"""
from fastapi.responses import HTMLResponse
from pathlib import Path


import os
import asyncio
import tempfile
import shutil

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ingest import ingest
from retrieval import hybrid_search
from generate import answer_question
from embed import embed_query

app = FastAPI(title="Codebase Q&A Assistant")


@app.get("/", response_class=HTMLResponse)
def home():
    return Path("templates/index.html").read_text(encoding="utf-8")


class IngestRequest(BaseModel):
    repo_url: str
    repo_id: str


@app.post("/ingest")
async def ingest_repo(req: IngestRequest):
    tmp_dir = tempfile.mkdtemp()
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", req.repo_url, tmp_dir,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(400, "git clone timed out after 120s")

        if proc.returncode != 0:
            raise HTTPException(400, f"git clone failed: {stderr.decode()}")

        await ingest(tmp_dir, req.repo_id)
        return {"status": "ingested", "repo_id": req.repo_id}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


class AskRequest(BaseModel):
    question: str
    top_k: int = 5
    provider: str = "groq"  # "groq" or "bedrock" -- Step 2


@app.post("/ask")
async def ask(req: AskRequest):
    query_embedding = embed_query(req.question)
    results = await hybrid_search(req.question, query_embedding, top_k=req.top_k)

    if not results:
        return {"answer": "No relevant code found for that question.", "sources": []}

    chunks = [
        {
            "file_path": r[1],
            "symbol_name": r[2],
            "start_line": r[4],
            "end_line": r[5],
            "content": r[7],
        }
        for r in results
    ]

    answer = await answer_question(req.question, chunks, provider=req.provider)
    return {
        "answer": answer,
        "sources": [f"{c['file_path']}:{c['start_line']}-{c['end_line']}" for c in chunks],
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)