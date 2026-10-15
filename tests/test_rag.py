"""
Tests for ingestion, retrieval and the API — run with `pytest` from the project root.

Claude is stubbed and the store is redirected to a temp file, so no API key is
needed and your real docs_store.json is never touched.
"""

from __future__ import annotations
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

# Must be set before `ingest` is imported (it loads the store at import time).
_TMP = Path(tempfile.mkdtemp())
os.environ["STORE_PATH"] = str(_TMP / "store.json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import pytest                         # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import generator                      # noqa: E402
import ingest                         # noqa: E402
import main                           # noqa: E402

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def clean_store():
    ingest.delete_all_documents()
    yield
    ingest.delete_all_documents()


@pytest.fixture
def fake_claude(monkeypatch):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="Use the Backoffice. (Source: guide.md)")])

    monkeypatch.setattr(generator, "_get_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    return calls


def _upload(name: str, text: str):
    return client.post("/upload", files={"file": (name, text.encode(), "text/markdown")})


# ── Chunking ──────────────────────────────────────────────────────────────────

def test_chunks_overlap_and_cover_all_words():
    words = [f"w{i}" for i in range(25)]
    chunks = ingest.split_into_chunks(" ".join(words), size=10, overlap=3)
    assert chunks[0].split()[-3:] == chunks[1].split()[:3]
    assert chunks[-1].split()[-1] == "w24"


def test_short_text_is_single_chunk():
    assert ingest.split_into_chunks("just a few words", size=10, overlap=3) == ["just a few words"]


# ── Ingestion & retrieval ────────────────────────────────────────────────────

def test_upload_then_retrieve_ranks_relevant_doc_first():
    _upload("guide.md", "Document types define the structure of content in the Backoffice.")
    _upload("other.md", "Coffee brewing temperatures and grind sizes.")
    results = ingest.retrieve("what are document types", top_k=2)
    assert results and results[0]["source"] == "guide.md"


def test_reupload_replaces_instead_of_duplicating():
    _upload("guide.md", "version one")
    r = _upload("guide.md", "version two")
    assert r.json()["replaced_chunks"] == 1
    docs = client.get("/documents").json()
    assert len(docs) == 1 and docs[0]["chunks"] == 1


def test_delete_single_document():
    doc_id = _upload("a.md", "alpha content").json()["doc_id"]
    _upload("b.md", "beta content")
    assert client.delete(f"/documents/{doc_id}").status_code == 200
    assert [d["source"] for d in client.get("/documents").json()] == ["b.md"]
    assert client.delete(f"/documents/{doc_id}").status_code == 404


def test_store_survives_reload():
    _upload("persist.md", "persisted words here")
    ingest._load_store()
    assert ingest.chunk_count() == 1


def test_concurrent_uploads_lose_nothing():
    threads = [threading.Thread(target=_upload, args=(f"doc{i}.md", f"content number {i}")) for i in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(client.get("/documents").json()) == 8


# ── Validation ────────────────────────────────────────────────────────────────

def test_rejects_unsupported_extension():
    r = client.post("/upload", files={"file": ("evil.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400


def test_rejects_oversized_upload(monkeypatch):
    monkeypatch.setattr(main, "_MAX_UPLOAD_BYTES", 10)
    assert _upload("big.md", "x" * 100).status_code == 413


@pytest.mark.parametrize("payload", [{"question": ""}, {"question": "hi", "top_k": 0}, {"question": "hi", "top_k": 999}])
def test_query_validation(payload):
    assert client.post("/query", json=payload).status_code == 422


# ── Query / generation ───────────────────────────────────────────────────────

def test_query_with_no_documents_skips_claude(fake_claude):
    r = client.post("/query", json={"question": "anything?"})
    assert r.status_code == 200
    assert "upload" in r.json()["answer"].lower()
    assert fake_claude == []


def test_query_with_no_relevant_chunks_skips_claude(fake_claude):
    _upload("guide.md", "Document types in the Backoffice.")
    r = client.post("/query", json={"question": "zebra"})
    assert "couldn't find" in r.json()["answer"]
    assert fake_claude == []


def test_query_returns_grounded_answer(fake_claude):
    _upload("guide.md", "Document types define content structure in the Backoffice.")
    r = client.post("/query", json={"question": "What are document types?"})
    body = r.json()
    assert r.status_code == 200
    assert body["sources"] == ["guide.md"]
    assert "<passage" in fake_claude[0]["messages"][0]["content"]


def test_missing_api_key_returns_502_not_crash(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    generator._get_client.cache_clear()
    _upload("guide.md", "Document types define content structure.")
    r = client.post("/query", json={"question": "document types"})
    assert r.status_code == 502
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]
