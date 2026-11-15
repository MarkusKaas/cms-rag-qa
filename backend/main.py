"""
main.py — FastAPI application entry point.

Routes:
  POST   /upload                Upload and index a document
  POST   /query                 Ask a question, get the full answer as JSON
  POST   /query/stream          Ask a question, get the answer streamed (Server-Sent Events)
  GET    /documents             List all indexed documents
  DELETE /documents             Wipe all indexed documents
  DELETE /documents/{doc_id}    Remove a single document
  GET    /health                Health check
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()   # before importing modules that read env vars at import time

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from generator import (
    MAX_QUESTION_CHARS,
    MODEL,
    GenerationError,
    fallback_answer,
    generate_answer,
    is_configured,
    stream_answer,
    unique_sources,
)
from ingest import chunk_count, delete_all_documents, delete_document, ingest_file, list_documents
from retriever import retrieve

__version__ = "1.2.0"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CMS RAG Q&A",
    description="Retrieval-Augmented Generation over CMS content, powered by Claude.",
    version=__version__,
)

# Comma-separated list, e.g. "http://localhost:3000,https://cms.example.com".
# Defaults to "*" for local dev; lock this down when deploying.
_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}
MAX_UPLOAD_MB      = int(os.getenv("MAX_UPLOAD_MB", "20"))
_MAX_UPLOAD_BYTES  = MAX_UPLOAD_MB * 1024 * 1024


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Tag each request with an ID and log method, path, status and latency."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    if not request.url.path.startswith("/static"):
        logger.info("[%s] %s %s → %d (%.0f ms)", request_id, request.method, request.url.path,
                    response.status_code, (time.perf_counter() - started) * 1000)
    return response


# ── Request / Response models ──────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    top_k: int = Field(default=4, ge=1, le=20)

class QueryResponse(BaseModel):
    answer: str
    sources: list[str]
    chunks: list[dict]

class UploadResponse(BaseModel):
    message: str
    doc_id: str
    source: str
    chunks: int
    replaced_chunks: int = 0
    unchanged: bool = False

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    """Returns service status plus active model and config — useful for verifying
    what's running without digging into environment variables or logs."""
    return {
        "status": "ok" if is_configured() else "degraded",
        "version": __version__,
        "model": MODEL,
        "anthropic_key_set": is_configured(),
        "max_question_chars": MAX_QUESTION_CHARS,
        "max_upload_mb": MAX_UPLOAD_MB,
        "indexed_chunks": chunk_count(),
    }


# Plain `def` (not `async def`): extraction, chunking and TF-IDF fitting are blocking
# CPU/disk work. In an async handler they'd stall the event loop for every other request;
# as a sync handler FastAPI runs them in its thread pool.
@app.post("/upload", response_model=UploadResponse)
def upload(file: UploadFile = File(...)) -> UploadResponse:
    """Upload a document and index it."""
    filename = Path(file.filename or "").name   # strip any client-supplied path
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix or '?'}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Write the upload to a temp file so ingest can read it, enforcing the size cap while streaming.
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        written = 0
        while chunk := file.file.read(1024 * 1024):
            written += len(chunk)
            if written > _MAX_UPLOAD_BYTES:
                tmp.close()
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"File exceeds the {MAX_UPLOAD_MB} MB limit.")
            tmp.write(chunk)

    try:
        result = ingest_file(tmp_path, source_name=filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    if result["unchanged"]:
        message = f"'{filename}' is already indexed and unchanged."
    else:
        verb = "Re-indexed" if result["replaced_chunks"] else "Indexed"
        message = f"{verb} '{filename}' into {result['chunks']} chunks."
    logger.info(message)
    return UploadResponse(message=message, **result)


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    """Retrieve relevant passages and generate a grounded answer via Claude."""
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    chunks = retrieve(question, top_k=req.top_k)
    try:
        result = generate_answer(question, chunks, has_documents=chunk_count() > 0)
    except GenerationError as exc:
        logger.error("Answer generation failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return QueryResponse(answer=result["answer"], sources=result["sources"], chunks=chunks)


def _sse(event: str, data: object) -> str:
    """Format one Server-Sent Event. JSON-encoding keeps newlines in the payload safe."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/query/stream")
def query_stream(req: QueryRequest) -> StreamingResponse:
    """
    Same as /query, but streams the answer as Server-Sent Events so the UI can show
    it while it is being written (the first words appear in well under a second).

    Events, in order:
      meta   {sources, chunks}   — retrieval results, sent before generation starts
      delta  {text}              — a piece of the answer (repeated)
      error  {detail}            — generation failed; the stream then ends
      done   {}                  — the answer is complete
    """
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    chunks = retrieve(question, top_k=req.top_k)
    canned = fallback_answer(chunks, has_documents=chunk_count() > 0)

    def events() -> Iterator[str]:
        yield _sse("meta", {"sources": [] if canned else unique_sources(chunks), "chunks": chunks})
        if canned is not None:
            yield _sse("delta", {"text": canned})
        else:
            try:
                for text in stream_answer(question, chunks):
                    yield _sse("delta", {"text": text})
            except GenerationError as exc:
                logger.error("Streaming generation failed: %s", exc)
                yield _sse("error", {"detail": str(exc)})
                return
        yield _sse("done", {})

    # X-Accel-Buffering stops nginx-style proxies from buffering the whole stream.
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/documents")
def documents() -> list[dict]:
    """Return all indexed documents (one entry per document, sorted by name)."""
    return list_documents()


@app.delete("/documents/{doc_id}")
def remove_document(doc_id: str) -> dict:
    """Delete one document (and all its chunks) by doc_id."""
    removed = delete_document(doc_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"message": f"Removed {removed} chunks.", "chunks_removed": removed}


@app.delete("/documents")
def clear_documents() -> dict:
    """Delete all indexed documents."""
    removed = delete_all_documents()
    return {"message": f"Removed {removed} chunks.", "chunks_removed": removed}


# ── Serve frontend ────────────────────────────────────────────────────────────

frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    @app.get("/", include_in_schema=False)
    def root() -> FileResponse:
        return FileResponse(str(frontend_dir / "index.html"))
