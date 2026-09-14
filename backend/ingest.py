"""
ingest.py — Document processing, chunking, TF-IDF indexing, and retrieval.

Uses scikit-learn TfidfVectorizer — no PyTorch, no ONNX, Python 3.13 compatible.
Documents are persisted to docs_store.json in the backend directory.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ── Config ────────────────────────────────────────────────────────────────────

CHUNK_SIZE    = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))
TOP_K_DEFAULT = int(os.getenv("TOP_K", "4"))
STORE_PATH    = Path(os.getenv("STORE_PATH", "docs_store.json"))

# ── In-memory state ───────────────────────────────────────────────────────────

_docs: list[dict] = []          # flat list of chunk records
_vectorizer: TfidfVectorizer | None = None
_matrix = None                  # sparse TF-IDF matrix (n_docs × n_features)


# ── Persistence ───────────────────────────────────────────────────────────────

def _load_store() -> None:
    global _docs
    if STORE_PATH.exists():
        _docs = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    else:
        _docs = []


def _save_store() -> None:
    STORE_PATH.write_text(
        json.dumps(_docs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _rebuild_index() -> None:
    """Refit the TF-IDF vectorizer on all stored chunks."""
    global _vectorizer, _matrix
    if not _docs:
        _vectorizer = None
        _matrix = None
        return
    texts = [d["text"] for d in _docs]
    _vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=50_000)
    _matrix = _vectorizer.fit_transform(texts)


# Load persisted docs and build index on startup
_load_store()
_rebuild_index()
print(f"Index ready: {len(_docs)} chunks.")


# ── Text extraction ───────────────────────────────────────────────────────────

def extract_text(file_path: Path) -> str:
    """Extract raw text from a supported file type."""
    suffix = file_path.suffix.lower()

    if suffix in (".txt", ".md"):
        return file_path.read_text(encoding="utf-8", errors="ignore")

    if suffix == ".pdf":
        try:
            import PyPDF2
            parts: list[str] = []
            with open(file_path, "rb") as f:
                for page in PyPDF2.PdfReader(f).pages:
                    parts.append(page.extract_text() or "")
            return "\n".join(parts)
        except Exception as e:
            raise ValueError(f"Failed to read PDF: {e}") from e

    if suffix == ".docx":
        try:
            import docx
            return "\n".join(p.text for p in docx.Document(file_path).paragraphs)
        except Exception as e:
            raise ValueError(f"Failed to read DOCX: {e}") from e

    raise ValueError(f"Unsupported file type: {suffix}")


# ── Chunking ──────────────────────────────────────────────────────────────────

def _split_into_chunks(text: str) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    words = text.split()
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + CHUNK_SIZE, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_file(file_path: Path, source_name: str) -> dict:
    """Extract, chunk, and index a document. Returns summary dict."""
    text = extract_text(file_path)
    if not text.strip():
        raise ValueError("Document is empty or could not be read.")

    chunks = _split_into_chunks(text)
    doc_id = str(uuid.uuid4())

    for i, chunk in enumerate(chunks):
        _docs.append({
            "id":     f"{doc_id}_{i}",
            "doc_id": doc_id,
            "source": source_name,
            "chunk":  i,
            "text":   chunk,
        })

    _save_store()
    _rebuild_index()
    return {"doc_id": doc_id, "source": source_name, "chunks": len(chunks)}


def list_documents() -> list[dict]:
    """Return a deduplicated list of ingested documents."""
    seen: dict[str, dict] = {}
    for d in _docs:
        if d["doc_id"] not in seen:
            seen[d["doc_id"]] = {"doc_id": d["doc_id"], "source": d["source"]}
    return list(seen.values())


def delete_all_documents() -> int:
    """Wipe all stored documents and the index. Returns chunk count removed."""
    global _docs
    count = len(_docs)
    _docs = []
    if STORE_PATH.exists():
        STORE_PATH.unlink()
    _rebuild_index()
    return count


def retrieve(query: str, top_k: int = TOP_K_DEFAULT) -> list[dict]:
    """Find the most relevant chunks for a query using TF-IDF cosine similarity."""
    if not _docs or _vectorizer is None:
        return []

    q_vec  = _vectorizer.transform([query])
    scores = cosine_similarity(q_vec, _matrix)[0]
    top_indices = np.argsort(scores)[::-1][:top_k]

    results: list[dict] = []
    for i in top_indices:
        if scores[i] > 0:
            results.append({
                "text":   _docs[i]["text"],
                "source": _docs[i]["source"],
                "chunk":  _docs[i]["chunk"],
                "score":  round(float(scores[i]), 4),
            })
    return results


def embed_query(query: str) -> list[float]:
    """Return a TF-IDF vector for the query (kept for API compatibility)."""
    if _vectorizer is None:
        return []
    return _vectorizer.transform([query]).toarray()[0].tolist()
