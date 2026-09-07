# CMS RAG Q&A

A local Retrieval-Augmented Generation (RAG) system for querying CMS documentation and content. Upload `.txt`, `.md`, `.pdf`, or `.docx` files, then ask questions and get grounded, cited answers powered by **Claude** and **ChromaDB**.

Built as part of a thesis on AI-in-CMS patterns — with Umbraco as the primary data source.

---

## Stack

| Layer      | Technology                             |
|------------|----------------------------------------|
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (local, free) |
| Vector DB  | ChromaDB (local persistent)            |
| Generation | Claude Haiku (`claude-haiku-4-5-20251001`) via Anthropic API |
| Backend    | FastAPI + uvicorn                      |
| Frontend   | Single-file HTML/CSS/JS (no build step) |

See [`DECISIONS.md`](DECISIONS.md) for why each technology was chosen.

---

## Quick Start

### 1. Clone and enter the project

```bash
git clone https://github.com/<you>/cms-rag-qa.git
cd cms-rag-qa
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> First run downloads the `all-MiniLM-L6-v2` embedding model (~90 MB) and caches it locally.

### 4. Set up your API key

```bash
cp .env.example .env
# Open .env and fill in your ANTHROPIC_API_KEY
```

Get a key at [console.anthropic.com](https://console.anthropic.com).

### 5. Run the server

```bash
cd backend
uvicorn main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) — the UI loads automatically.

---

## Usage

1. **Upload a document** — drag and drop (or browse) a `.txt`, `.md`, `.pdf`, or `.docx` file in the sidebar.
2. **Ask a question** — type in the chat bar and press Enter.
3. **Read the answer** — Claude responds with citations showing which document(s) were used.

### Try it with the sample document

```
sample_docs/umbraco_sample.md
```

Upload it, then try questions like:
- *"What is the Umbraco Delivery API base URL?"*
- *"How do I unsubscribe a member from Mailchimp?"*
- *"What is the difference between Block List and Nested Content?"*

---

## API Reference

The FastAPI server exposes these endpoints (interactive docs at `/docs`):

| Method   | Path          | Description                         |
|----------|---------------|-------------------------------------|
| `POST`   | `/upload`     | Upload and index a document         |
| `POST`   | `/query`      | Ask a question, get a cited answer  |
| `GET`    | `/documents`  | List all indexed documents          |
| `DELETE` | `/documents`  | Wipe all indexed documents          |
| `GET`    | `/health`     | Health check                        |

### Example: query via curl

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the Delivery API base URL?"}'
```

---

## Project Structure

```
cms-rag-qa/
├── backend/
│   ├── main.py         # FastAPI app, routes, static serving
│   ├── ingest.py       # Extract → chunk → embed → store
│   ├── retriever.py    # Semantic search (ChromaDB cosine)
│   └── generator.py    # Claude answer generation
├── frontend/
│   └── index.html      # Single-file dark-theme UI
├── sample_docs/
│   └── umbraco_sample.md  # Test document (Umbraco reference)
├── .env.example
├── .gitignore
├── DECISIONS.md        # Architectural decision log
├── requirements.txt
└── README.md
```

---

## Configuration

Override defaults via `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...   # required
CHUNK_SIZE=800                  # words per chunk
CHUNK_OVERLAP=150               # overlap between chunks
TOP_K=4                         # chunks retrieved per query
```

---

## How It Works

```
Upload document
     │
     ▼
Extract text (.txt/.md/.pdf/.docx)
     │
     ▼
Split into overlapping word chunks
     │
     ▼
Embed chunks (all-MiniLM-L6-v2, local)
     │
     ▼
Store in ChromaDB (cosine similarity index)

─────────────────────────────────────────

User question
     │
     ▼
Embed query (same model)
     │
     ▼
Cosine search → top-k chunks
     │
     ▼
Claude Haiku generates grounded answer
with source citations
     │
     ▼
Display answer + source chips in UI
```

---

## Extending to Umbraco

The Delivery API exports content as JSON. To index a live Umbraco site:

```python
import httpx, json
from pathlib import Path

r = httpx.get("https://your-site.com/umbraco/delivery/api/v2/content?take=100")
pages = r.json()["items"]

for page in pages:
    body = page["properties"].get("bodyText", {}).get("markup", "")
    Path(f"uploads/{page['name']}.txt").write_text(body)
    # then call ingest_file(...)
```

---

## License

MIT
