"""
main.py — FastAPI application entry point.

Routes:
  POST /upload          Upload and index a document
  POST /query           Ask a question against indexed documents
  GET  /documents       List all indexed documents
  DELETE /documents     Wipe all indexed documents
  GET  /health          Health check
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from generator import generate_answer
from ingest import delete_all_documents, ingest_file, list_documents
from retriever import retrieve

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CMS RAG Q&A",
    description="Retrieval-Augmented Generation over CMS content, powered by Claude.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}

# ── Request / Response models ──────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str
    top_k: int = 4

class QueryResponse(BaseModel):
    answer: str
    sources: list[str]
    chunks: list[dict]

class UploadResponse(BaseModel):
    message: str
    doc_id: str
    source: str
    chunks: int

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...)) -> UploadResponse:
    """Upload a document and index it into ChromaDB."""
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Write upload to a temp file so ingest can read it
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        result = ingest_file(tmp_path, source_name=file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    return UploadResponse(
        message=f"Indexed '{file.filename}' into {result['chunks']} chunks.",
        **result,
    )


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    """Retrieve relevant passages and generate a grounded answer via Claude."""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    chunks = retrieve(req.question, top_k=req.top_k)
    result = generate_answer(req.question, chunks)

    return QueryResponse(
        answer=result["answer"],
        sources=result["sources"],
        chunks=chunks,
    )


@app.get("/documents")
def documents() -> list[dict]:
    """Return all indexed documents (deduplicated by doc_id)."""
    return list_documents()


@app.delete("/documents")
def clear_documents() -> dict:
    """Delete all indexed documents from ChromaDB."""
    removed = delete_all_documents()
    return {"message": f"Removed {removed} chunks.", "chunks_removed": removed}


# ── Serve frontend ────────────────────────────────────────────────────────────

frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    @app.get("/")
    def root() -> FileResponse:
        return FileResponse(str(frontend_dir / "index.html"))
