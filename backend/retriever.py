"""
retriever.py — The retrieval seam between the API and the index.

main.py only depends on `retrieve()` and the `RetrievedChunk` shape defined here,
never on how the index works. Swapping TF-IDF for embeddings or a hosted vector
database (Qdrant, pgvector, Azure AI Search, …) means providing another function
that satisfies `Retriever` and pointing `retrieve` at it — the API, generator and
frontend stay untouched.
"""

from __future__ import annotations

from typing import Protocol

from ingest import TOP_K_DEFAULT, RetrievedChunk
from ingest import retrieve as _tfidf_retrieve

__all__ = ["Retriever", "RetrievedChunk", "retrieve"]


class Retriever(Protocol):
    """Anything that maps a question to its most relevant chunks, best first."""

    def __call__(self, query: str, top_k: int = TOP_K_DEFAULT) -> list[RetrievedChunk]: ...


# The active implementation. Replace this assignment to change retrieval backends.
retrieve: Retriever = _tfidf_retrieve
