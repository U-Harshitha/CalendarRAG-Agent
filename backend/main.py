import os
from datetime import date, timedelta, datetime
from typing import Optional

import requests
from fastapi import FastAPI
from pydantic import BaseModel

# Try to import dateparser for robust natural-language datetime parsing.
try:
    import dateparser
    HAS_DATEPARSER = True
except Exception:
    HAS_DATEPARSER = False
from .agent import call_tool, classify_intent, detect_ambiguity
from .evaluator import evaluate_response
from .rag import answer_query
import os
import re

app = FastAPI()

# MCP server URL and a short default timeout for quick health/status checks.
MCP_URL = os.getenv("MCP_URL", "http://localhost:9000")
# Timeout (seconds) used by internal helper when calling MCP for quick endpoints like /auth/status.
# Make it configurable with environment variable MCP_TIMEOUT (default 2s) so the backend can fail fast
# and the frontend will get a fast response instead of waiting for long request timeouts.
try:
    MCP_TIMEOUT = float(os.getenv("MCP_TIMEOUT", "2"))
except Exception:
    MCP_TIMEOUT = 2.0


class QueryRequest(BaseModel):
    query: str


class CreateEventPayload(BaseModel):
    title: str
    date: str
    start_time: str
    end_time: Optional[str] = None
    description: str = ""
    location: str = ""


def _mcp_get(path: str, timeout: float = None):
    """GET helper to call MCP server.

    The default timeout is short (MCP_TIMEOUT) so status/health checks return quickly when the
    MCP server is slow or down. Callers that expect longer operations can pass a timeout value.
    """
    t = timeout if timeout is not None else MCP_TIMEOUT
    response = requests.get(f"{MCP_URL}{path}", timeout=t)
    response.raise_for_status()
    return response.json()


def _mcp_post(path: str, payload=None, timeout: float = 60.0):
    """POST helper to call MCP server. Uses longer default timeout for write/read operations.

    The timeout is configurable per-call; default remains 60s for operations that may take longer.
    """
    response = requests.post(f"{MCP_URL}{path}", json=payload or {}, timeout=timeout)
    response.raise_for_status()
    return response.json()

def _normalize_events(events):
    normalized = []
    if not isinstance(events, list):
        return normalized

    for e in events:
        if not isinstance(e, dict):
            continue
        start = e.get("start", {}) or {}
        end = e.get("end", {}) or {}
        normalized.append(
            {
                "id": e.get("id"),
                "title": e.get("summary") or "(No title)",
                "start": start.get("dateTime") or start.get("date"),
                "end": end.get("dateTime") or end.get("date"),
                "location": e.get("location") or "",
                "link": e.get("htmlLink") or "",
                "status": e.get("status") or "",
            }
        )

    return normalized


@app.get("/calendar/status")
def calendar_status():
    try:
        return _mcp_get("/auth/status")
    except requests.RequestException as e:
        return {
            "has_credentials_json": False,
            "has_token_json": False,
            "error": str(e),
        }


@app.post("/calendar/connect")
def calendar_connect():
    try:
        return _mcp_post("/auth/connect")
    except requests.RequestException as e:
        return {
            "connected": False,
            "error": str(e),
        }


@app.post("/create")
def create_direct(payload: CreateEventPayload):
    """Direct create endpoint that accepts explicit event fields and forwards to the MCP server.

    Returns created event or conflict suggestions in a structured JSON format.
    """
    try:
        # Normalize date formats like 2026/01/06 -> 2026-01-06 to be ISO compatible
        data = payload.dict()
        if isinstance(data.get("date"), str) and "/" in data.get("date"):
            data["date"] = data["date"].replace("/", "-")

        # If end_time is missing, default to one hour after start_time
        if not data.get("end_time") and data.get("start_time"):
            try:
                base = datetime.fromisoformat(f"{data['date']}T{data['start_time']}")
                end_dt = base + timedelta(hours=1)
                data["end_time"] = f"{end_dt.hour:02d}:{end_dt.minute:02d}"
            except Exception:
                # fallback: keep end_time None and let MCP/server handle if necessary
                data["end_time"] = data.get("end_time")

        created = call_tool("create_event", data)
        if isinstance(created, dict) and created.get("error"):
            return {"result": "FAIL", "error": created.get("error")}

        if isinstance(created, dict) and created.get("conflict"):
            return {
                "answer": "Requested time conflicts with existing events.",
                "result": "CONFLICT",
                "conflicts": created.get("conflicts", []),
                "suggestions": created.get("suggestions", []),
            }

        return {"result": "PASS", "created": created}
    except Exception as e:
        return {"result": "FAIL", "error": str(e)}


@app.post("/query")
def query_agent(req: QueryRequest):
    query = req.query

    start_date = date.today()
    end_date = start_date + timedelta(days=100)

    # Default: fetch upcoming events to provide context
    tool_data = call_tool(
        "list_events",
        {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat()
        },
    )

    # If the MCP tool call returned an error dict, surface a helpful message
    if tool_data is None or (isinstance(tool_data, dict) and tool_data.get("error")):
        err = tool_data.get("error") if isinstance(tool_data, dict) else "MCP not reachable"
        return {
            "answer": f"Calendar integration is not available: {err}. Connect Google Calendar (OAuth) and ensure the MCP server is running.",
            "references": [],
            "confidence": 0.0,
            "result": "FAIL",
            "events": [],
            "summary": "",
        }

    events = _normalize_events(tool_data)

    # Run RAG retrieval over KB + calendar events to get contextual documents
    rag_result = answer_query(query, events)
    retrieved_docs = rag_result.get("kb_results", [])
    # event results available too
    event_hits = rag_result.get("event_results", [])

    # If the user intends to create an event, try to parse details and call the MCP create tool.
    intent = classify_intent(query)

    if intent == "CREATE_EVENT":
        # Minimal parsing for date, time, title, location
        q = query.lower()

        # Use dateparser when available to robustly parse phrases like
        # "tomorrow at 12:30 PM" or "next Thursday 3pm".
        ev_date = None
        start_time = None

        # date
        if "today" in q:
            ev_date = date.today()
        elif "tomorrow" in q:
            ev_date = date.today() + timedelta(days=1)
        else:
            # try ISO date YYYY-MM-DD
            m_date = re.search(r"(\d{4}-\d{2}-\d{2})", q)
            if m_date:
                try:
                    y,mo,d = m_date.group(1).split("-")
                    ev_date = date(int(y), int(mo), int(d))
                except Exception:
                    ev_date = None
            else:
                ev_date = None

        # If dateparser not available or failed to extract a datetime, fall back to regex-based parsing
        q_lower = q.lower()

        # date (if not already parsed by dateparser)
        if not ev_date:
            if "today" in q_lower:
                ev_date = date.today()
            elif "tomorrow" in q_lower:
                ev_date = date.today() + timedelta(days=1)
            else:
                # try ISO date YYYY-MM-DD or slashed YYYY/MM/DD
                m_date = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})", q)
                if m_date:
                    try:
                        ds = m_date.group(1).replace("/", "-")
                        y, mo, d = ds.split("-")
                        ev_date = date(int(y), int(mo), int(d))
                    except Exception:
                        ev_date = None
                else:
                    ev_date = None
        # time: look for first time token like 5pm, 5:30 pm, 17:00
        time_match = re.search(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)|((?:\d{1,2}:\d{2}))", q)
        time_token = None
        if time_match:
            time_token = time_match.group(0)

        # If dateparser gave us start_time (HH:MM), prefer that; otherwise we'll parse the regex token
        # (parsing function defined below)

        def parse_time(tok: str):
            if not tok:
                return None
            tok = tok.strip()
            ampm = None
            m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", tok)
            if not m:
                return None
            h = int(m.group(1))
            mm = int(m.group(2)) if m.group(2) else 0
            if m.group(3):
                ampm = m.group(3)
            if ampm == 'pm' and h < 12:
                h += 12
            if ampm == 'am' and h == 12:
                h = 0
            return f"{h:02d}:{mm:02d}"

        start_time = parse_time(time_token)
        # default end_time = start + 1 hour
        end_time = None
        if start_time:
            sh, sm = map(int, start_time.split(":"))
            eh = (sh + 1) % 24
            end_time = f"{eh:02d}:{sm:02d}"

        # title: support 'named', 'called', 'titled', or 'title'
        title = None
        m = re.search(r"(?:named|titled|called)\s+([\w\s]+?)(?:\s+at\s+|\s+in\s+|$)", query, flags=re.I)
        if m:
            title = m.group(1).strip()
        else:
            # fallback: look for 'title' keyword
            m = re.search(r"title(?:d)?\s*(?:is|:)??\s*([\w\s]+)$", query, flags=re.I)
            if m:
                title = m.group(1).strip()

        # If still no title, attempt to extract trailing phrase after the time token as the title
        if not title and time_match:
            try:
                rem = query[time_match.end():].strip()
                # remove common filler
                rem = re.sub(r"^(for|on|at)\s+", "", rem, flags=re.I).strip()
                # remove leading words like 'titled' (handle common misspellings loosely)
                rem = re.sub(r"^(titled|titeled|title|named|called)\s*[:\-]?\s*", "", rem, flags=re.I).strip()
                # take up to first 8 words as a reasonable title
                words = rem.split()
                if words:
                    title = " ".join(words[:8]).strip()
            except Exception:
                title = None

        # location
        location = None
        m = re.search(r"\bat\s+([\w\s]+)$", query, flags=re.I)
        if m:
            location = m.group(1).strip()
        else:
            m = re.search(r"in\s+([\w\s]+)$", query, flags=re.I)
            if m:
                location = m.group(1).strip()

        missing = []
        if not ev_date:
            missing.append("date")
        if not start_time:
            missing.append("time")
        if not title:
            missing.append("title")

        if missing:
            # Return structured missing slots and known fields so the UI can perform slot-filling
            known = {
                "title": title,
                "date": ev_date.isoformat() if ev_date else None,
                "start_time": start_time,
                "end_time": end_time,
                "location": location,
            }
            return {
                "answer": f"I need more details to create the event. Missing: {', '.join(missing)}.",
                "references": [],
                "confidence": 0.0,
                "result": "NEEDS_MORE_INFO",
                "missing_slots": missing,
                "known_fields": known,
            }

        payload = {
            "title": title,
            "date": ev_date.isoformat(),
            "start_time": start_time,
            "end_time": end_time,
            "description": "",
            "location": location or "",
        }

        try:
            created = call_tool("create_event", payload)
            # If call_tool returned an error dict, surface it
            if isinstance(created, dict) and created.get("error"):
                return {
                    "answer": f"Failed to reach calendar tool: {created.get('error')}",
                    "references": [],
                    "confidence": 0.0,
                    "result": "FAIL",
                }

            # If MCP returns a conflict object, propagate that structure
            if isinstance(created, dict) and created.get("conflict"):
                return {
                    "answer": "Requested time conflicts with existing events.",
                    "summary": "Conflict",
                    "events": events,
                    "references": ["Google Calendar"],
                    "confidence": 0.0,
                    "result": "CONFLICT",
                    "conflicts": created.get("conflicts", []),
                    "suggestions": created.get("suggestions", []),
                    "attempted_payload": payload,
                }

            # Show newly created event plus current upcoming events
            created_summary = f"Created event '{payload['title']}' on {payload['date']} at {payload['start_time']} (location: {payload['location']})."
            evaluation = evaluate_response(created_summary, retrieved_docs, True)

            return {
                "answer": created_summary,
                "summary": created_summary,
                "events": events,
                "references": ["Google Calendar"],
                "confidence": evaluation["confidence"],
                "result": "PASS" if evaluation["pass"] else "FAIL",
                "created": created,
            }
        except Exception as e:
            return {
                "answer": f"Failed to create event: {e}",
                "references": [],
                "confidence": 0.0,
                "result": "FAIL",
            }
    # 2️⃣ Ambiguity check for non-create intents
    missing = detect_ambiguity(query)
    if missing:
        return {
            "answer": f"The query is ambiguous. Please provide the following details: {', '.join(missing)}.",
            "references": [],
            "confidence": 0.6,
            "result": "PASS"
        }

    # For informational intents, compose an answer using RAG results. Prefer an LLM if available.
    def compose_answer_with_llm(query_text, rag_res):
        # Prefer Groq LLM if available (free API key expected in GROQ_API_KEY).
        # Fall back to OpenAI if GROQ isn't available, then use deterministic RAG summary.
        # We try multiple invocation patterns for ChatGroq to be robust to SDK versions.
        try:
            groq_key = os.getenv("GROQ_API_KEY")
            if groq_key:
                try:
                    from langchain_groq import ChatGroq
                except Exception:
                    ChatGroq = None

                if ChatGroq is not None:
                    # Build prompt similar to before, with truncated snippets and scores
                    def _truncate(s, n=400):
                        return s if len(s) <= n else s[:n].rsplit(' ', 1)[0] + "..."

                    kb_snippets = "\n".join([
                        f"Doc {d['id']} (score={d.get('score', 0):.2f}): {_truncate(d['text'])}"
                        for d in rag_res.get('kb_results', [])
                    ])

                    ev_snippets = "\n".join([
                        f"Event {e.get('id') or ''} (score={e.get('score',0):.2f}): {_truncate(e.get('text') or str(e.get('raw', {})))}"
                        for e in rag_res.get('event_results', [])
                    ])

                    system = (
                        "You are a helpful assistant that answers questions about a user's calendar and a knowledge base. "
                        "Be concise and only use the provided sources. If none of the provided calendar events are clearly relevant, "
                        "do not assume other events exist — say that no relevant calendar items were found. "
                        "When stating facts, cite the source in parentheses, e.g. (Calendar) or (KB: filename)."
                    )

                    user_prompt = (
                        f"Query: {query_text}\n\nIf an event is only marginally related, ignore it. Use only the calendar events and KB passages provided below. "
                        f"If you can answer from the KB without using calendar events, do so.\n\nCalendar events:\n{ev_snippets}\n\nKnowledge base:\n{kb_snippets}\n\nProvide a short, factual answer (2-4 sentences) and list which sources you used. Be explicit about uncertainty if information is incomplete."
                    )

                    # Instantiate ChatGroq. Many SDK versions accept model and api_key.
                    try:
                        llm = ChatGroq(model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"), api_key=groq_key)
                    except Exception:
                        # try without api_key parameter (SDK may use env var)
                        llm = ChatGroq(model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"))

                    # Try common LangChain-like call patterns
                    try:
                        if hasattr(llm, "predict"):
                            return llm.predict(user_prompt)
                        if hasattr(llm, "__call__"):
                            return llm(user_prompt)
                        if hasattr(llm, "generate"):
                            gen = llm.generate([user_prompt])
                            # Attempt to extract text from generation object
                            try:
                                # LangChain-like: gen.generations[0][0].text
                                return gen.generations[0][0].text
                            except Exception:
                                # Fallback: str(gen)
                                return str(gen)
                    except Exception:
                        # If Groq failed, continue to OpenAI fallback
                        pass
        except Exception:
            # Any import/usage error: fall through to OpenAI/deterministic fallback
            pass

        # Try OpenAI if set
        try:
            import openai
            key = os.getenv("OPENAI_API_KEY")
            if key:
                openai.api_key = key
                def _truncate(s, n=400):
                    return s if len(s) <= n else s[:n].rsplit(' ', 1)[0] + '...'

                kb_snippets = "\n".join([
                    f"Doc {d['id']} (score={d.get('score', 0):.2f}): {_truncate(d['text'])}"
                    for d in rag_res.get('kb_results', [])
                ])

                ev_snippets = "\n".join([
                    f"Event {e.get('id') or ''} (score={e.get('score',0):.2f}): {_truncate(e.get('text') or str(e.get('raw', {})))}"
                    for e in rag_res.get('event_results', [])
                ])

                system = "You are a helpful assistant that answers questions about a user's calendar and a knowledge base. Be concise and only use the provided sources."
                user_prompt = f"Query: {query_text}\n\nCalendar events:\n{ev_snippets}\n\nKnowledge base:\n{kb_snippets}\n\nProvide a short, factual answer that cites which source (calendar or KB) each fact came from."
                resp = openai.ChatCompletion.create(
                    model=os.getenv('OPENAI_MODEL', 'gpt-3.5-turbo'),
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user_prompt}],
                    max_tokens=256,
                    temperature=0.0,
                )
                text = resp['choices'][0]['message']['content'].strip()
                return text
        except Exception:
            pass

        # Fallback: use the deterministic RAG summary we computed earlier
        return rag_res.get('summary')

    answer_text = compose_answer_with_llm(query, rag_result)
    evaluation = evaluate_response(answer_text, retrieved_docs, True)

    return {
        "answer": answer_text,
        "summary": rag_result.get("summary"),
        "events": events,
        "references": ["Google Calendar"] if events else [],
        "confidence": evaluation["confidence"],
        "result": "PASS" if evaluation["pass"] else "FAIL",
        "kb_hits": rag_result.get("kb_results"),
        "event_hits": rag_result.get("event_results"),
    }
