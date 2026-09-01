# DECISIONS.md

Architecture and technology decisions for `cms-rag-qa`.

---

## What this is

A Retrieval-Augmented Generation (RAG) system designed specifically for CMS content — particularly
Umbraco Delivery API output, documentation, and editorial content. Users upload documents; the
system chunks, embeds, and indexes them; questions are answered by retrieving the most relevant
passages and synthesising a grounded response via the Claude API.

---

## Key decisions

### 1. Claude API for generation (not OpenAI)

**Decision:** Use Anthropic's Claude (`claude-haiku-4-5`) for generation.

**Why:** I already have hands-on Claude API experience from Anthropic courses and use Claude Code
daily. Claude's instruction-following is strong and the haiku model is fast and cheap for RAG — ideal
for a portfolio demo where API costs matter. Using Claude also differentiates this project from the
thousands of identical Python + OpenAI RAG tutorials.

**Trade-off:** Anthropic doesn't offer a native embeddings endpoint, so embeddings use a separate
local model (see below).

---

### 2. Local embeddings with sentence-transformers (`all-MiniLM-L6-v2`)

**Decision:** Embed documents locally with `sentence-transformers`, no external embeddings API.

**Why:** Free, fast, and runs entirely offline after the first model download (~90 MB). For a portfolio
project this removes one API dependency entirely. `all-MiniLM-L6-v2` has excellent performance on
semantic search benchmarks relative to its size.

**Trade-off:** Slightly lower quality than API-based embeddings (e.g. OpenAI `text-embedding-3-small`
or Voyage AI). Acceptable for this use case; swapping the embedding model later is a one-line change.

---

### 3. ChromaDB for vector storage

**Decision:** Use ChromaDB with local persistence.

**Why:** Zero infrastructure — no Docker, no Postgres, no cloud account. ChromaDB persists to a
local `chroma_db/` directory automatically. For a demo system this is the right trade-off: fast to
set up, easy to inspect, simple to wipe and restart.

**Trade-off:** Not suitable for multi-user production at scale. A real deployment would use a managed
vector DB (Pinecone, Weaviate, Qdrant). The interface is abstracted in `retriever.py` so swapping
is straightforward.

---

### 4. Chunk size: 800 tokens, overlap: 150 tokens

**Decision:** Split documents into ~800-token chunks with 150-token overlap.

**Why:** Overlap ensures that context spanning a chunk boundary is not lost. 800 tokens balances
retrieval precision (smaller = more precise matches) against context richness (larger = more
surrounding information per chunk). These values are configurable via `.env`.

---

### 5. FastAPI backend + single-file HTML frontend

**Decision:** FastAPI serves the API; the frontend is a single `index.html` with no build step.

**Why:** FastAPI is fast to write, has automatic OpenAPI docs at `/docs`, and is the standard for
Python AI APIs. A single-file frontend avoids Node.js/npm overhead for a demo — the UI is served
directly by FastAPI's `StaticFiles`. Anyone can open `frontend/index.html` locally or deploy the
whole thing to Railway/Fly.io with one command.

---

### 6. Umbraco as the primary data source

**Decision:** Design the ingestion and system prompt around Umbraco CMS content.

**Why:** This project was built alongside a thesis on AI-in-CMS architectural patterns. The RAG
pattern (semantic retrieval over editorial content) is Pattern #1 in that catalogue. Using Umbraco
content as the data source makes this a concrete thesis artefact, not just a tutorial re-skin.

**In practice:** The system accepts any plain text, Markdown, or PDF. The Umbraco angle is in the
system prompt (framed for editorial/CMS Q&A) and the sample documents.

---

## What I would change for production

- Swap ChromaDB for a managed vector DB with multi-tenancy support
- Add user authentication (the API currently has no auth)
- Add document-level access control (different users see different documents)
- Use a proper task queue (Celery/ARQ) for large file ingestion
- Implement re-ranking (Cohere Rerank or a cross-encoder) before passing to Claude
- Add evaluation: track retrieval hit-rate and answer quality over time
