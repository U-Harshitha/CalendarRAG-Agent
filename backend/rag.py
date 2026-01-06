import os
import json
import time
from pathlib import Path

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from .embeddings import embed_text

KB_PATH = Path(__file__).resolve().parent / "knowledge_base"
CACHE_DIR = Path(__file__).resolve().parent / "rag_cache"

# filenames for cached metadata and embeddings
KB_CHUNKS_META = CACHE_DIR / "kb_chunks_meta.json"
KB_CHUNKS_EMB = CACHE_DIR / "kb_chunk_embeddings.npy"

# KB documents loaded at startup
documents = []
# chunk-level KB entries: list of dicts {id, file, chunk_index, text}
kb_chunks = []
kb_chunk_embeddings = np.array([])


def _ensure_cache_dir():
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        # best-effort; proceed without cache if cannot create
        pass


def _save_cache():
    """Persist kb_chunks metadata and embeddings to disk (atomic-ish)."""
    _ensure_cache_dir()
    try:
        # write metadata
        tmp_meta = str(KB_CHUNKS_META) + ".tmp"
        with open(tmp_meta, "w", encoding="utf-8") as f:
            json.dump(kb_chunks, f, ensure_ascii=False, indent=2)
        os.replace(tmp_meta, str(KB_CHUNKS_META))
        # write embeddings as float32 to save space
        if kb_chunk_embeddings.size:
            tmp_emb = str(KB_CHUNKS_EMB) + ".tmp.npy"
            np.save(tmp_emb, kb_chunk_embeddings.astype(np.float32))
            os.replace(tmp_emb, str(KB_CHUNKS_EMB))
    except Exception:
        # If cache saving fails, don't crash the import
        return


def _load_cache() -> bool:
    """Load cache if available. Returns True if loaded, False otherwise."""
    global kb_chunks, kb_chunk_embeddings
    if not KB_CHUNKS_META.exists() or not KB_CHUNKS_EMB.exists():
        return False
    try:
        with open(KB_CHUNKS_META, "r", encoding="utf-8") as f:
            kb_chunks = json.load(f)
        # numpy saved with .npy suffix
        kb_chunk_embeddings = np.load(str(KB_CHUNKS_EMB))
        return True
    except Exception:
        return False


def chunk_text(text: str, chunk_size: int = 200, overlap: int = 50):
    """Split text into word-based chunks with overlap.

    chunk_size and overlap are in words. This is a simple, dependency-free chunker.
    """
    words = text.split()
    if not words:
        return []
    chunks = []
    start = 0
    L = len(words)
    while start < L:
        end = min(start + chunk_size, L)
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        if end == L:
            break
        start = end - overlap if end - overlap > start else end
    return chunks


if KB_PATH.exists() and KB_PATH.is_dir():
    # Try to load cached chunk embeddings first
    loaded = _load_cache()
    if not loaded:
        tmp_embs = []
        for filename in os.listdir(KB_PATH):
            file_path = KB_PATH / filename
            if not file_path.is_file():
                continue
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read().strip()
                documents.append({"id": filename, "text": text})
                # chunk file
                chunks = chunk_text(text, chunk_size=200, overlap=50)
                for i, ch in enumerate(chunks):
                    chunk_id = f"{filename}::chunk:{i}"
                    kb_chunks.append({"id": chunk_id, "file": filename, "chunk_index": i, "text": ch})
                    tmp_embs.append(embed_text(ch))
        if tmp_embs:
            kb_chunk_embeddings = np.vstack(tmp_embs)
            # persist cache for faster next startup
            try:
                _save_cache()
            except Exception:
                pass


def _maybe_stack(arrs):
    if not arrs:
        return np.array([])
    return np.vstack(arrs)


def retrieve_kb(query: str, top_k: int = 4, threshold: float = 0.16):
    """Retrieve top-k KB chunks related to query."""
    if kb_chunk_embeddings.size == 0:
        return []
    q_emb = embed_text(query).reshape(1, -1)
    scores = cosine_similarity(q_emb, kb_chunk_embeddings)[0]
    inds = scores.argsort()[-top_k:][::-1]
    results = []
    for i in inds:
        if scores[i] >= threshold:
            chunk = kb_chunks[i]
            results.append({
                "id": chunk["id"],
                "file": chunk["file"],
                "chunk_index": chunk["chunk_index"],
                "text": chunk["text"],
                "score": float(scores[i]),
            })
    return results


def build_event_embeddings(events: list):
    """Given a list of calendar events (dicts), return their embeddings and a list of normalized documents.

    Each event is expected to have keys like 'title', 'start', 'end', 'description', 'location'.
    """
    ev_docs = []
    ev_embs = []
    for ev in events:
        title = ev.get("title") or ev.get("summary") or "(No title)"
        desc = ev.get("description") or ""
        loc = ev.get("location") or ""
        start = ev.get("start") or ev.get("start_date") or ev.get("start_time") or ""
        text = f"Title: {title}. When: {start}. Location: {loc}. Details: {desc}"
        ev_docs.append({"id": ev.get("id"), "text": text, "raw": ev})
        ev_embs.append(embed_text(text))
    return ev_docs, _maybe_stack(ev_embs)


def retrieve_events(query: str, events: list, top_k: int = 5, threshold: float = 0.18, min_rel_score_ratio: float = 0.5):
    """Retrieve top-k calendar events related to query.

    To avoid returning many loosely-related events, require that returned events not only
    exceed an absolute similarity `threshold` but also be within `min_rel_score_ratio` of
    the top-scoring event. This filters out low-relevance tails when the query is unrelated.
    """
    if not events:
        return []
    ev_docs, ev_embs = build_event_embeddings(events)
    if ev_embs.size == 0:
        return []
    q_emb = embed_text(query).reshape(1, -1)
    scores = cosine_similarity(q_emb, ev_embs)[0]
    # If even the best score is below threshold, return no events
    max_score = float(scores.max()) if scores.size else 0.0
    if max_score < threshold:
        return []

    inds = scores.argsort()[-top_k:][::-1]
    results = []
    for i in inds:
        s = float(scores[i])
        # Require both an absolute threshold and a relative cutoff
        if s >= threshold and s >= max_score * min_rel_score_ratio:
            d = ev_docs[i].copy()
            d["score"] = s
            results.append(d)
    return results


def answer_query(query: str, events: list, top_k_kb: int = 3, top_k_events: int = 5):
    """Return a combined RAG result for the query over KB and calendar events.

    Returns a dict { 'query', 'kb_results', 'event_results', 'summary' }.
    The summary is a short, deterministic composition of the retrieved items.
    """
    kb_res = retrieve_kb(query, top_k=top_k_kb)
    ev_res = retrieve_events(query, events, top_k=top_k_events)

    # Compose a short textual summary
    parts = []
    if ev_res:
        parts.append(f"I found {len(ev_res)} calendar event(s) related to your query:")
        for e in ev_res:
            raw = e.get("raw") or {}
            title = raw.get("title") or raw.get("summary") or "(No title)"
            when = raw.get("start") or raw.get("start_date") or ""
            parts.append(f"- {title} at {when}")
    else:
        parts.append("No directly matching calendar events were found.")

    if kb_res:
        parts.append(f"I also found {len(kb_res)} knowledge-base document(s) that may be relevant:")
        for d in kb_res:
            parts.append(f"- {d['id']} (score={d['score']:.2f})")

    summary = "\n".join(parts)

    return {
        "query": query,
        "kb_results": kb_res,
        "event_results": ev_res,
        "summary": summary,
    }


if __name__ == "__main__":
    # quick local smoke test
    print("RAG KB documents:", len(documents))
