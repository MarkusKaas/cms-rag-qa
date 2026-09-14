"""
generator.py — Claude-powered answer generation from retrieved context.

Uses claude-haiku-4-5 for fast, cost-efficient responses.
"""

from __future__ import annotations

import os

import anthropic

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL   = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1024

SYSTEM_PROMPT = """You are a knowledgeable assistant that answers questions about CMS content, \
documentation, and editorial material. You have been given a set of relevant passages retrieved \
from the user's document library.

Rules:
1. Answer ONLY from the provided passages. Do not use outside knowledge.
2. If the passages do not contain enough information to answer the question, say so clearly — \
   do not make things up.
3. Always cite your sources. After each factual claim, add a parenthetical like (Source: filename.md).
4. Keep answers concise and well-structured. Use bullet points or numbered lists when appropriate.
5. If the question is ambiguous, ask a short clarifying question rather than guessing."""


def generate_answer(query: str, chunks: list[dict]) -> dict:
    """
    Generate a grounded answer using Claude and the retrieved chunks.

    Args:
        query:  The user's question.
        chunks: List of retrieved context dicts (text, source, score).

    Returns:
        Dict with keys: answer (str), sources (list[str]).
    """
    if not chunks:
        return {
            "answer":  "No documents have been indexed yet. Please upload a document first.",
            "sources": [],
        }

    # Build the context block
    context_parts: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        context_parts.append(
            f"[Passage {i} — Source: {chunk['source']}]\n{chunk['text']}"
        )
    context_block = "\n\n---\n\n".join(context_parts)

    user_message = f"""Here are the relevant passages from the document library:

{context_block}

---

Question: {query}"""

    message = _client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    answer = message.content[0].text
    sources = sorted({chunk["source"] for chunk in chunks})

    return {"answer": answer, "sources": sources}
