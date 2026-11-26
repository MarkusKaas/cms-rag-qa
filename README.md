# CMS RAG Q&A

A local Retrieval-Augmented Generation (RAG) system for querying CMS documentation and content. Upload `.txt`, `.md`, `.pdf`, or `.docx` files, then ask questions and get grounded, cited answers powered by **Claude** and a local **TF-IDF** retrieval index.

Built as part of a thesis on AI-in-CMS patterns — with Umbraco as the primary data source.

---

## Stack

| Layer      | Technology                             |
|------------|----------------------------------------|
| Retrieval  | scikit-learn TF-IDF (1–2-grams) + cosine similarity |
| Storage    | JSON file (`backend/docs_store.json`, atomic writes) |
| Extraction | `pypdf` (PDF), `python-docx` (Word, headings preserved) |
| Generation | Claude Haiku (`claude-haiku-4-5-20251001`) via Anthropic API, streamed |
| Backend    | FastAPI + uvicorn                      |
| Frontend   | Single-file HTML/CSS/JS (no build step) |
| Tests      | pytest, offline (Claude stubbed)       |

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
| `POST`   | `/query`      | Ask a question, get a cited answer as JSON |
| `POST`   | `/query/stream` | Same, streamed as Server-Sent Events (used by the UI) |
| `GET`    | `/documents`  | List all indexed documents          |
| `DELETE` | `/documents`  | Wipe all indexed documents          |
| `DELETE` | `/documents/{doc_id}` | Remove a single document    |
| `GET`    | `/health`     | Health check                        |

### Example: query via curl

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the Delivery API base URL?"}'

# Streamed: prints `event: meta`, then `event: delta` chunks, then `event: done`
curl -N -X POST http://localhost:8000/query/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "How do I authenticate against the Delivery API?"}'
```

Each retrieved chunk in the response includes `source`, `heading` (the section it came from, e.g. `Umbraco Delivery API › Authentication`) and its similarity `score`.

---

## Project Structure

```
cms-rag-qa/
├── backend/
│   ├── main.py         # FastAPI app, routes, static serving
│   ├── ingest.py       # Extract → chunk → TF-IDF index → store (thread-safe)
│   ├── retriever.py    # Retrieval seam: a Retriever protocol, swap point for a vector DB
│   └── generator.py    # Claude answer generation
├── frontend/
│   └── index.html      # Single-file dark-theme UI
├── tests/
│   ├── conftest.py     # Redirects the store to a temp dir, stubs the API key
│   └── test_rag.py     # Offline tests (Claude stubbed)
├── sample_docs/
│   └── umbraco_sample.md  # Test document (Umbraco reference)
├── .env.example
├── .gitignore
├── DECISIONS.md        # Architectural decision log
├── pyproject.toml      # pytest + ruff config
├── requirements.txt
├── requirements-dev.txt
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
MIN_SCORE=0.0                   # drop chunks at/below this similarity
MAX_UPLOAD_MB=20                # reject larger uploads (413)
CORS_ORIGINS=*                  # comma-separated; restrict when deploying
GENERATOR_MODEL=claude-haiku-4-5-20251001
```

`CHUNK_OVERLAP` must be smaller than `CHUNK_SIZE` — the app refuses to start otherwise.

Re-uploading a file with the same name replaces the earlier version rather than indexing it twice. Re-uploading an *identical* file is detected by content hash and skipped.

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest                      # 25 tests
ruff check backend tests    # lint
```

Claude is stubbed and the store is redirected to a temp file, so tests need no API key and never touch your data. CI runs both on Python 3.11 and 3.13.

---

## How It Works

```
Upload document
     │
     ▼
Extract text (.txt/.md/.pdf/.docx)
     │
     ▼
Split into sections by heading (#, ##, … / Word heading styles)
     │
     ▼
Split each section into overlapping word chunks
(line breaks kept; each chunk remembers its heading path)
     │
     ▼
Refit TF-IDF index over all chunks
     │
     ▼
Persist chunks to docs_store.json

─────────────────────────────────────────

User question
     │
     ▼
Vectorise query (same TF-IDF vocabulary)
     │
     ▼
Cosine similarity → top-k chunks
(no relevant chunks → answer without calling Claude)
     │
     ▼
Claude Haiku generates a grounded answer
with source citations, streamed token by token
     │
     ▼
UI renders it live, then shows source chips and
the retrieved passages with their scores
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
