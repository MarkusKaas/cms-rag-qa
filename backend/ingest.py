"""
ingest.py — Document processing, chunking, TF-IDF indexing, and retrieval.

Uses scikit-learn TfidfVectorizer — no PyTorch, no ONNX, Python 3.13 compatible.
Documents are persisted to docs_store.json in the backend directory.

Concurrency: FastAPI runs sync endpoints in a thread pool, so uploads, deletes and
queries can overlap. All reads and writes of the shared index go through `_lock`
so a query never sees a half-rebuilt vectorizer/matrix pair.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

CHUNK_SIZE    = int(os.getenv("CHUNK_SIZE", "800"))      # words per chunk
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))   # words shared between neighbouring chunks
TOP_K_DEFAULT = int(os.getenv("TOP_K", "4"))
MIN_SCORE     = float(os.getenv("MIN_SCORE", "0.0"))     # drop chunks scoring at/below this

# Resolve relative to this file, not the process CWD — otherwise running uvicorn from
# the repo root vs. backend/ silently reads/writes two different stores.
_BACKEND_DIR = Path(__file__).resolve().parent
STORE_PATH   = Path(os.getenv("STORE_PATH", "docs_store.json"))
if not STORE_PATH.is_absolute():
    STORE_PATH = _BACKEND_DIR / STORE_PATH

if CHUNK_SIZE <= 0 or not 0 <= CHUNK_OVERLAP < CHUNK_SIZE:
    # With overlap >= size the chunker's start index never advances → infinite loop.
    raise ValueError(
        f"Invalid chunk config: CHUNK_SIZE={CHUNK_SIZE}, CHUNK_OVERLAP={CHUNK_OVERLAP}. "
        "Require CHUNK_SIZE > 0 and 0 <= CHUNK_OVERLAP < CHUNK_SIZE."
    )


class RetrievedChunk(TypedDict):
    text: str
    source: str
    doc_id: str
    chunk: int
    heading: str
    score: float


# ── In-memory state ───────────────────────────────────────────────────────────

_lock = threading.RLock()
_docs: list[dict] = []          # flat list of chunk records
_vectorizer: TfidfVectorizer | None = None
_matrix = None                  # sparse TF-IDF matrix (n_chunks × n_features)


# ── Persistence ───────────────────────────────────────────────────────────────

def _load_store() -> None:
    global _docs
    with _lock:
        if not STORE_PATH.exists():
            _docs = []
            return
        try:
            _docs = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Don't crash the app on a corrupt store — keep a copy for inspection and start empty.
            backup = STORE_PATH.with_suffix(".corrupt.json")
            STORE_PATH.replace(backup)
            logger.error("Store at %s was corrupt; moved to %s and starting empty.", STORE_PATH, backup)
            _docs = []


def _save_store() -> None:
    """Write atomically: a crash mid-write leaves the previous store intact."""
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_docs, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STORE_PATH)


def _index_text(record: dict) -> str:
    # Index the heading together with the body, so a question that matches a
    # section title ("What is the Delivery API?") finds that section's chunks.
    heading = record.get("heading") or ""
    return f"{heading}\n{record['text']}" if heading else record["text"]


def _rebuild_index() -> None:
    """Refit the TF-IDF vectorizer on all stored chunks. Caller must hold _lock."""
    global _vectorizer, _matrix
    if not _docs:
        _vectorizer = None
        _matrix = None
        return
    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=1,
        max_features=50_000,
        sublinear_tf=True,        # dampen terms repeated many times in one chunk
        strip_accents="unicode",  # "æble"/"aeble", "café"/"cafe" match each other
    )
    matrix = vectorizer.fit_transform([_index_text(d) for d in _docs])
    _vectorizer, _matrix = vectorizer, matrix


# Load persisted docs and build index on startup
with _lock:
    _load_store()
    _rebuild_index()
logger.info("Index ready: %d chunks from %s", len(_docs), STORE_PATH)


# ── Text extraction ───────────────────────────────────────────────────────────

def _read_pdf(file_path: Path) -> str:
    # pypdf is the maintained successor of PyPDF2 (which is deprecated and gets no
    # fixes). Fall back to PyPDF2 so an existing environment keeps working.
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - depends on installed packages
        from PyPDF2 import PdfReader
    reader = PdfReader(str(file_path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(p for p in pages if p.strip())


def _read_docx(file_path: Path) -> str:
    import docx

    lines: list[str] = []
    for para in docx.Document(str(file_path)).paragraphs:
        text = para.text.strip()
        if not text:
            continue
        # Keep Word headings as markdown headings, so heading-aware chunking
        # works for .docx just like it does for .md.
        m = re.match(r"Heading (\d)", para.style.name if para.style is not None else "")
        lines.append(f"{'#' * int(m.group(1))} {text}" if m else text)
    return "\n\n".join(lines)


def extract_text(file_path: Path) -> str:
    """Extract raw text from a supported file type."""
    suffix = file_path.suffix.lower()

    if suffix in (".txt", ".md"):
        # utf-8-sig strips a BOM that Windows editors like to add.
        return file_path.read_text(encoding="utf-8-sig", errors="replace")

    if suffix == ".pdf":
        try:
            return _read_pdf(file_path)
        except Exception as e:
            raise ValueError(f"Failed to read PDF: {e}") from e

    if suffix == ".docx":
        try:
            return _read_docx(file_path)
        except Exception as e:
            raise ValueError(f"Failed to read DOCX: {e}") from e

    raise ValueError(f"Unsupported file type: {suffix}")


# ── Chunking ──────────────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def split_into_chunks(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Split text into overlapping windows of `size` words.

    Line breaks are preserved inside each chunk (only word count decides where a
    window ends), so code blocks, lists and tables stay readable when a passage is
    shown to the model or in the UI. Runs of blank lines collapse to one.
    """
    # Each word remembers the whitespace that preceded it: "", " " or "\n"/"\n\n".
    words: list[tuple[str, str]] = []
    for m in re.finditer(r"(\s*)(\S+)", text):
        gap = m.group(1)
        sep = "\n\n" if gap.count("\n") >= 2 else "\n" if "\n" in gap else " "
        words.append((sep, m.group(2)))

    def render(window: list[tuple[str, str]]) -> str:
        return "".join((sep if i else "") + w for i, (sep, w) in enumerate(window))

    chunks: list[str] = []
    step = size - overlap
    start = 0
    while start < len(words):
        end = min(start + size, len(words))
        chunks.append(render(words[start:end]))
        if end == len(words):
            break
        start += step
    return chunks


def split_into_sections(text: str) -> list[tuple[str, str]]:
    """
    Split markdown-style text into (heading_path, body) sections.

    heading_path is the chain of enclosing headings, e.g. "Delivery API › Authentication".
    Text before the first heading gets an empty heading. Headings inside fenced
    code blocks are ignored. Plain text with no headings yields one section.
    """
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []   # (level, title)
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        content = "\n".join(body).strip()
        if content:
            sections.append((" › ".join(t for _, t in stack), content))
        body.clear()

    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        m = None if in_fence else _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2).strip()))
        else:
            body.append(line)
    flush()
    return sections


def chunk_document(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[tuple[str, str]]:
    """
    Heading-aware chunking: split into sections first, then into word windows within
    each section. A chunk therefore never straddles two unrelated sections, and each
    chunk knows which section it came from (shown as a citation in the UI).
    """
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    return [
        (heading, chunk)
        for heading, body in split_into_sections(text)
        for chunk in split_into_chunks(body, size, overlap)
    ]


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_file(file_path: Path, source_name: str) -> dict:
    """
    Extract, chunk, and index a document. Returns a summary dict.

    Re-uploading a file with the same name replaces the previous version instead of
    indexing it twice (duplicates would crowd out other sources in retrieval). An
    identical re-upload is detected by content hash and skips re-indexing entirely.
    """
    global _docs
    text = extract_text(file_path)   # slow I/O happens outside the lock
    if not text.strip():
        raise ValueError("Document is empty or no text could be extracted (scanned PDFs need OCR first).")

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    with _lock:
        existing = [d for d in _docs if d["source"] == source_name]
        if existing and all(d.get("hash") == content_hash for d in existing):
            return {
                "doc_id": existing[0]["doc_id"], "source": source_name,
                "chunks": len(existing), "replaced_chunks": 0, "unchanged": True,
            }

    chunks = chunk_document(text)
    doc_id = str(uuid.uuid4())
    uploaded_at = datetime.now(UTC).isoformat(timespec="seconds")

    new_records = [
        {
            "id":          f"{doc_id}_{i}",
            "doc_id":      doc_id,
            "source":      source_name,
            "chunk":       i,
            "heading":     heading,
            "text":        chunk,
            "hash":        content_hash,
            "uploaded_at": uploaded_at,
        }
        for i, (heading, chunk) in enumerate(chunks)
    ]

    with _lock:
        kept = [d for d in _docs if d["source"] != source_name]
        replaced = len(_docs) - len(kept)
        _docs = kept + new_records
        _save_store()
        _rebuild_index()

    return {"doc_id": doc_id, "source": source_name, "chunks": len(chunks),
            "replaced_chunks": replaced, "unchanged": False}


def list_documents() -> list[dict]:
    """Return one entry per ingested document, with its chunk count and size."""
    with _lock:
        seen: dict[str, dict] = {}
        for d in _docs:
            entry = seen.setdefault(d["doc_id"], {
                "doc_id":      d["doc_id"],
                "source":      d["source"],
                "chunks":      0,
                "words":       0,
                "uploaded_at": d.get("uploaded_at"),   # absent in stores created before this field
            })
            entry["chunks"] += 1
            entry["words"] += len(d["text"].split())
        return sorted(seen.values(), key=lambda e: e["source"].lower())


def delete_document(doc_id: str) -> int:
    """Remove a single document. Returns the number of chunks removed (0 if not found)."""
    global _docs
    with _lock:
        remaining = [d for d in _docs if d["doc_id"] != doc_id]
        removed = len(_docs) - len(remaining)
        if removed:
            _docs = remaining
            _save_store()
            _rebuild_index()
        return removed


def delete_all_documents() -> int:
    """Wipe all stored documents and the index. Returns chunk count removed."""
    global _docs
    with _lock:
        count = len(_docs)
        _docs = []
        STORE_PATH.unlink(missing_ok=True)
        _rebuild_index()
        return count


def chunk_count() -> int:
    with _lock:
        return len(_docs)


def retrieve(query: str, top_k: int = TOP_K_DEFAULT) -> list[RetrievedChunk]:
    """Find the most relevant chunks for a query using TF-IDF cosine similarity."""
    with _lock:
        if not _docs or _vectorizer is None:
            return []
        scores = cosine_similarity(_vectorizer.transform([query]), _matrix)[0]
        docs = _docs   # snapshot the list that matches this matrix

    # argpartition is O(n) vs. a full O(n log n) sort — matters once the store grows.
    k = max(1, min(top_k, len(scores)))
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top])]

    return [
        RetrievedChunk(
            text=docs[i]["text"],
            source=docs[i]["source"],
            doc_id=docs[i]["doc_id"],
            chunk=docs[i]["chunk"],
            heading=docs[i].get("heading", ""),
            score=round(float(scores[i]), 4),
        )
        for i in top
        if scores[i] > MIN_SCORE
    ]


def embed_query(query: str) -> list[float]:
    """Return a TF-IDF vector for the query (kept for API compatibility)."""
    with _lock:
        if _vectorizer is None:
            return []
        return _vectorizer.transform([query]).toarray()[0].tolist()
