"""
generator.py — Claude-powered answer generation from retrieved context.

Configuration (via environment variables or .env):
  ANTHROPIC_API_KEY   — required
  GENERATOR_MODEL     — Claude model to use (default: claude-haiku-4-5-20251001)
  GENERATOR_MAX_TOKENS — max tokens in the answer (default: 1024)
  MAX_QUESTION_CHARS  — maximum question length (default: 1000)
"""

from __future__ import annotations

import os

import anthropic

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL              = os.getenv("GENERATOR_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS         = int(os.getenv("GENERATOR_MAX_TOKENS", "1024"))
MAX_QUESTION_CHARS = int(os.getenv("MAX_QUESTION_CHARS", "1000"))

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
        query:  The user's question. Truncated to MAX_QUESTION_CHARS if needed.
        chunks: List of retrieved context dicts (text, source, score).

    Returns:
        Dict with keys: answer (str), sources (list[str]).

    Raises:
        ValueError: if query is empty.
        anthropic.APIError: on network or API-level failures.
    """
    if not query.strip():
        raise ValueError("Query cannot be empty.")

    # Truncate excessively long questions before they inflate the prompt
    if len(query) > MAX_QUESTION_CHARS:
        query = query[:MAX_QUESTION_CHARS]

    if not chunks:
        return {
            "answer":  "No documents have been indexed yet. Please upload a document first.",
            "sources": [],
        }

    # Build the context block — numbered passages make citations easy
    context_parts: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        context_parts.append(
            f"[Passage {i} — Source: {chunk['source']}]\n{chunk['text']}"
        )
    context_block = "\n\n---\n\n".join(context_parts)

    user_message = (
        f"Here are the relevant passages from the document library:\n\n"
        f"{context_block}\n\n"
        f"---\n\n"
        f"Question: {query}"
    )

    message = _client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    if not message.content:
        raise ValueError("Model returned an empty response.")

    answer  = message.content[0].text
    sources = sorted({chunk["source"] for chunk in chunks})

    return {"answer": answer, "sources": sources}
