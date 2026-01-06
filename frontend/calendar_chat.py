import os
import re
from typing import List, Tuple

import streamlit as st
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from backend import rag as rag_mod
from backend import embeddings as emb_mod
from backend import agent as agent_mod

st.set_page_config(page_title="Calendar Chat + CiteFix", layout="wide")
st.title("📆 Calendar Chatbot — grounded in your calendar and KB")


def split_into_sentences(text: str) -> List[str]:
    sents = re.split(r'(?<=[\.\?\!])\s+', text.strip())
    return [s.strip() for s in sents if s.strip()]


def sim(a_vec: np.ndarray, b_vecs: np.ndarray) -> List[float]:
    if b_vecs.size == 0:
        return []
    q = a_vec.reshape(1, -1)
    scores = cosine_similarity(q, b_vecs)[0]
    return scores.tolist()


def normalize_events_raw(raw_events: List[dict]) -> List[dict]:
    normalized = []
    for e in raw_events or []:
        if not isinstance(e, dict):
            continue
        start = e.get("start", {}) or {}
        end = e.get("end", {}) or {}
        normalized.append({
            "id": e.get("id"),
            "title": e.get("summary") or e.get("title") or "(No title)",
            "start": start.get("dateTime") or start.get("date"),
            "end": end.get("dateTime") or end.get("date"),
            "description": e.get("description") or "",
            "location": e.get("location") or "",
            "raw": e,
        })
    return normalized


st.sidebar.header("Options")
days = st.sidebar.slider("Look ahead (days)", min_value=1, max_value=365, value=30)
top_k_kb = st.sidebar.slider("KB hits to retrieve", 1, 8, 4)
top_k_events = st.sidebar.slider("Event hits to retrieve", 1, 8, 5)
threshold_kb = st.sidebar.slider("KB similarity threshold (x100)", 1, 50, 16) / 100.0
threshold_ev = st.sidebar.slider("Event similarity threshold (x100)", 1, 50, 18) / 100.0


st.markdown("Enter your question about your calendar or events. The assistant will answer using your calendar events and the project's knowledge base.")
query = st.text_input("Ask about your calendar:")

if st.button("Ask") and query:
    with st.spinner("Fetching calendar events and KB, running retrieval..."):
        # fetch events via MCP agent
        from datetime import date, timedelta

        start_date = date.today().isoformat()
        end_date = (date.today() + timedelta(days=days)).isoformat()
        tool_res = agent_mod.call_tool("list_events", {"start_date": start_date, "end_date": end_date})

        if isinstance(tool_res, dict) and tool_res.get("error"):
            st.error(f"Failed to reach calendar MCP: {tool_res.get('error')}")
        else:
            events = normalize_events_raw(tool_res)

            # run RAG retrieval
            rag_result = rag_mod.answer_query(query, events, top_k_kb=top_k_kb, top_k_events=top_k_events)
            kb_hits = rag_result.get("kb_results", [])
            event_hits = rag_result.get("event_results", [])

            # Compose deterministic summary (from RAG) and then perform CiteFix-style per-sentence citation
            raw_answer = rag_result.get("summary") or ""

            # Build embeddings for candidate KB chunks and events
            embed = emb_mod.embed_text
            q_vec = embed(query)

            kb_texts = [d["text"] for d in kb_hits]
            kb_embs = np.vstack([embed(t) for t in kb_texts]) if kb_texts else np.array([])

            ev_texts = []
            for e in event_hits:
                # e may have 'text' or be an event doc
                ev_texts.append(e.get("text") or ("Title: " + e.get("raw", {}).get("title", "(No title)")))
            ev_embs = np.vstack([embed(t) for t in ev_texts]) if ev_texts else np.array([])

            # annotate each sentence
            sents = split_into_sentences(raw_answer)
            corrected_points = []
            used_sources = []
            for s in sents:
                s_vec = embed(s)
                kb_scores = sim(s_vec, kb_embs)
                ev_scores = sim(s_vec, ev_embs)

                best_kb_idx = int(np.argmax(kb_scores)) if kb_scores else None
                best_ev_idx = int(np.argmax(ev_scores)) if ev_scores else None
                best_kb_score = kb_scores[best_kb_idx] if kb_scores else 0.0
                best_ev_score = ev_scores[best_ev_idx] if ev_scores else 0.0

                # Decide source: prefer event if score higher and above threshold_ev; else KB if above threshold_kb
                citation = None
                if best_ev_idx is not None and best_ev_score >= threshold_ev and best_ev_score >= best_kb_score:
                    ev = event_hits[best_ev_idx]
                    citation = f"(Calendar: {ev.get('title')})"
                    used_sources.append(("calendar", ev))
                elif best_kb_idx is not None and best_kb_score >= threshold_kb:
                    kb = kb_hits[best_kb_idx]
                    citation = f"(KB: {kb.get('file')}#chunk{kb.get('chunk_index')})"
                    used_sources.append(("kb", kb))
                else:
                    citation = "(no supporting source found)"

                corrected_points.append(f"{s} {citation}")

            corrected_answer = " ".join(corrected_points)

            # Display results
            st.subheader("Answer (with citations)")
            st.write(corrected_answer)

            st.markdown("---")
            st.subheader("Sources used")
            if not used_sources:
                st.info("No strong supporting KB passages or calendar events were found. The assistant may be guessing.")
            else:
                for typ, item in used_sources:
                    if typ == "calendar":
                        st.markdown(f"- Calendar event: **{item.get('title')}** on {item.get('start')} — {item.get('description')[:200]}")
                    else:
                        st.markdown(f"- KB: {item.get('file')} chunk {item.get('chunk_index')} (score={item.get('score',0):.2f}) — {item.get('text')[:200]}...")

            st.markdown("---")
            st.subheader("Top candidate events retrieved")
            if event_hits:
                for e in event_hits:
                    st.markdown(f"- **{e.get('title') or e.get('raw',{}).get('title','(No title)')}** — {e.get('start')} (score={e.get('score',0):.2f})")
            else:
                st.info("No calendar events matched your query closely.")

            st.subheader("Top KB hits")
            if kb_hits:
                for d in kb_hits:
                    st.markdown(f"- {d.get('file')}#chunk{d.get('chunk_index')} (score={d.get('score',0):.2f}) — {d.get('text')[:200]}...")
            else:
                st.info("No KB passages matched your query closely.")
