"""
RAG (Retrieval-Augmented Generation) layer for PAIS.

ChromaDB stores embeddings of every vault note chunk.
Ollama (nomic-embed-text) generates embeddings locally — no API key, no cloud.

Usage:
    from tools.rag import search, index_vault, index_file

    context = search("crypto trading risk management", n_results=3)
    index_vault()          # initial full index (slow, run once)
    index_file(path)       # incremental update
"""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

CHROMA_DIR  = Path(__file__).parent.parent / "chroma_db"
META_FILE   = Path(__file__).parent.parent / "chroma_meta.json"  # tracks file hashes for incremental indexing
VAULT       = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"

OLLAMA_URL     = "http://localhost:11434"
EMBED_MODEL    = "nomic-embed-text"
COLLECTION     = "vault"
CHUNK_SIZE     = 600    # chars per chunk (≈150 tokens)
CHUNK_OVERLAP  = 80     # overlap between chunks for context continuity
MAX_CHUNK_DOCS = 8000   # cap to keep ChromaDB responsive

_client     = None
_collection = None


# ── Ollama embedding ──────────────────────────────────────────────────────────

def _embed(texts: list[str]) -> Optional[list[list[float]]]:
    """Call Ollama nomic-embed-text for a batch of texts. Returns list of vectors."""
    try:
        embeddings = []
        for text in texts:
            resp = requests.post(
                f"{OLLAMA_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text},
                timeout=30,
            )
            resp.raise_for_status()
            embeddings.append(resp.json()["embedding"])
        return embeddings
    except requests.exceptions.ConnectionError:
        log.warning("Ollama not running — RAG disabled. Start with: ollama serve")
        return None
    except Exception as e:
        log.warning("Ollama embedding error: %s", e)
        return None


def _ollama_alive() -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


# ── ChromaDB client ───────────────────────────────────────────────────────────

def _get_collection():
    global _client, _collection
    if _collection is not None:
        return _collection
    try:
        import chromadb
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client     = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = _client.get_or_create_collection(
            name=COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        return _collection
    except Exception as e:
        log.error("ChromaDB init error: %s", e)
        return None


# ── Metadata tracking ─────────────────────────────────────────────────────────

def _load_meta() -> dict:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_meta(meta: dict):
    META_FILE.write_text(json.dumps(meta, indent=2))


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def needs_reindex(path: Path) -> bool:
    meta = _load_meta()
    key  = str(path.relative_to(VAULT))
    return meta.get(key) != _file_hash(path)


# ── Chunking ──────────────────────────────────────────────────────────────────

def _chunk_text(text: str, source: str) -> list[dict]:
    """
    Split a markdown note into chunks by ## headings, then by size.
    Returns list of {"id": ..., "text": ..., "source": ...}.
    """
    # Strip frontmatter
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), None)
        if end:
            lines = lines[end + 1:]
    text = "\n".join(lines).strip()

    if not text:
        return []

    # Split by ## headings to keep sections coherent
    sections = []
    current_header = ""
    current_body   = []

    for line in text.split("\n"):
        if line.startswith("## ") or line.startswith("# "):
            if current_body:
                sections.append((current_header, "\n".join(current_body).strip()))
            current_header = line.lstrip("#").strip()
            current_body   = [line]
        else:
            current_body.append(line)

    if current_body:
        sections.append((current_header, "\n".join(current_body).strip()))

    if not sections:
        sections = [("", text)]

    # Further split large sections
    chunks = []
    seq = 0  # global sequence counter for this file — guarantees unique IDs
    for header, body in sections:
        if not body:
            continue
        prefix = f"[{source}] {header}\n" if header else f"[{source}]\n"
        pos = 0
        while pos < len(body):
            end   = min(pos + CHUNK_SIZE, len(body))
            piece = prefix + body[pos:end]
            chunk_id = hashlib.md5(f"{source}:{seq}".encode()).hexdigest()
            chunks.append({"id": chunk_id, "text": piece, "source": source})
            seq += 1
            if end >= len(body):
                break
            pos += CHUNK_SIZE - CHUNK_OVERLAP

    return chunks


# ── Indexing ──────────────────────────────────────────────────────────────────

_SKIP_DIRS = {".obsidian", ".trash"}
# Date-prefixed individual ChatGPT chat logs live in "ChatGPT Conversations/" — skip those
# but keep date-named session notes in "Chats/" (those are valuable vault summaries)
import re as _re
_CHAT_LOG_PAT = _re.compile(r"^\d{4}-\d{2}-\d{2}")
_CHATGPT_CONV_DIR = "ChatGPT Conversations"


def _vault_files() -> list[Path]:
    """Return vault .md files eligible for RAG indexing.

    Indexed:
    - All structured wiki pages
    - Session notes in Chats/ (date-prefixed but valuable)
    - ChatGPT category summaries (non-date-prefixed files in ChatGPT Conversations/)

    Skipped:
    - Individual raw ChatGPT chat logs (date-prefixed files inside ChatGPT Conversations/)
    """
    if not VAULT.exists():
        return []
    files = []
    for f in VAULT.rglob("*.md"):
        if any(skip in f.parts for skip in _SKIP_DIRS):
            continue
        # Only skip date-prefixed files that are inside the ChatGPT Conversations dir
        if _CHAT_LOG_PAT.match(f.name) and _CHATGPT_CONV_DIR in f.parts:
            continue
        files.append(f)
    return sorted(files)


def index_file(path: Path, force: bool = False) -> int:
    """Index a single vault file. Returns number of chunks added."""
    col = _get_collection()
    if col is None or not _ollama_alive():
        return 0

    if not force and not needs_reindex(path):
        return 0

    try:
        text   = path.read_text(encoding="utf-8", errors="ignore")
        source = str(path.relative_to(VAULT))
        chunks = _chunk_text(text, source)
        if not chunks:
            return 0

        # Remove old chunks for this file
        try:
            col.delete(where={"source": source})
        except Exception:
            pass

        # Embed + upsert in batches of 32
        batch_size = 32
        added = 0
        complete = True
        for i in range(0, len(chunks), batch_size):
            batch  = chunks[i:i + batch_size]
            texts  = [c["text"] for c in batch]
            embeds = _embed(texts)
            if embeds is None:
                complete = False
                break
            col.upsert(
                ids        = [c["id"]    for c in batch],
                documents  = texts,
                embeddings = embeds,
                metadatas  = [{"source": c["source"]} for c in batch],
            )
            added += len(batch)

        # Only record the hash if EVERY chunk made it in. A partial index
        # (Ollama dropped mid-run) must stay dirty so the next pass retries it
        # — otherwise the file is silently half-searchable forever.
        if complete:
            meta = _load_meta()
            meta[source] = _file_hash(path)
            _save_meta(meta)

        log.debug("Indexed %s → %d chunks", source, added)
        return added

    except Exception as e:
        log.warning("index_file error for %s: %s", path, e)
        return 0


def index_vault(force: bool = False) -> dict:
    """
    Full vault index. Skips unchanged files unless force=True.
    Returns {"files": n, "chunks": n, "skipped": n}.
    """
    if not _ollama_alive():
        return {"error": "Ollama not running. Start with: ollama serve && ollama pull nomic-embed-text"}

    files   = _vault_files()
    total_chunks = 0
    skipped = 0
    t0 = time.time()

    for f in files:
        if not force and not needs_reindex(f):
            skipped += 1
            continue
        n = index_file(f, force=force)
        total_chunks += n

    elapsed = round(time.time() - t0, 1)
    log.info("Vault index complete: %d files, %d chunks, %d skipped in %ss",
             len(files), total_chunks, skipped, elapsed)
    return {"files": len(files) - skipped, "chunks": total_chunks,
            "skipped": skipped, "elapsed_s": elapsed}


def index_changed_files() -> int:
    """Index only vault files that have changed since last index. Returns count."""
    if not _ollama_alive():
        return 0
    files   = _vault_files()
    changed = [f for f in files if needs_reindex(f)]
    total   = sum(index_file(f) for f in changed)
    if total:
        log.info("RAG: re-indexed %d changed files (%d new chunks)", len(changed), total)
    return total


# ── Search ────────────────────────────────────────────────────────────────────

def search(query: str, n_results: int = 4) -> str:
    """
    Semantic search across the vault. Returns formatted context string
    ready to prepend to an agent prompt.
    Returns empty string if RAG is unavailable.
    """
    col = _get_collection()
    if col is None or not _ollama_alive():
        return ""

    try:
        if col.count() == 0:
            return ""

        query_embed = _embed([query])
        if not query_embed:
            return ""

        results = col.query(
            query_embeddings=query_embed,
            n_results=min(n_results, col.count()),
            include=["documents", "metadatas", "distances"],
        )

        docs      = results.get("documents", [[]])[0]
        metas     = results.get("metadatas", [[]])[0]
        distances = results.get("distances",  [[]])[0]

        if not docs:
            return ""

        # Filter out low-relevance results (cosine distance > 0.45 = not meaningfully similar)
        relevant = [
            (doc, meta["source"], dist)
            for doc, meta, dist in zip(docs, metas, distances)
            if dist < 0.45
        ]

        if not relevant:
            return ""

        lines = ["## Relevant Vault Context (semantic search)"]
        seen_sources = set()
        for doc, source, dist in relevant:
            if source not in seen_sources:
                lines.append(f"\n### [{source}]")
                seen_sources.add(source)
            lines.append(doc[:400])  # cap each chunk in prompt

        return "\n".join(lines) + "\n"

    except Exception as e:
        log.warning("RAG search error: %s", e)
        return ""


def get_stats() -> dict:
    col = _get_collection()
    if col is None:
        return {"status": "unavailable"}
    try:
        meta  = _load_meta()
        alive = _ollama_alive()
        return {
            "status":        "ready" if alive else "ollama_offline",
            "chunks":        col.count(),
            "files_indexed": len(meta),
            "vault_files":   len(_vault_files()),
            "ollama_alive":  alive,
            "embed_model":   EMBED_MODEL,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
