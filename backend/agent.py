import os

import requests

MCP_URL = os.getenv("MCP_URL", "http://localhost:9000")

def call_tool(tool_name, payload):
    url = f"{MCP_URL.rstrip('/')}/{tool_name.lstrip('/')}"
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        # Return structured error with details so callers can surface helpful messages
        msg = str(e)
        # Include response text if available for debugging
        try:
            details = getattr(e, 'response') and e.response.text
            if details:
                msg = msg + f" | response: {details}"
        except Exception:
            pass
        return {"error": f"Failed to reach MCP tool '{tool_name}' at {url}: {msg}"}

def classify_intent(query: str):
    query = query.lower()

    # Recognize more verbs that imply creating/scheduling an event
    if any(w in query for w in ("create", "schedule", "make", "add", "book", "set up", "reserve")):
        return "CREATE_EVENT"
    if "list" in query or "upcoming" in query:
        return "LIST_EVENTS"
    if "calendar" in query or "event" in query or "events" in query:
        return "LIST_EVENTS"
    if "details" in query:
        return "GET_EVENT_DETAILS"
    if "search" in query:
        return "SEARCH_EVENTS"

    return "RAG_ONLY"
def detect_ambiguity(query: str):
    missing = []
    if any(w in query for w in ("schedule", "create", "make", "add", "book")):
        if ("am" not in query and "pm" not in query and ":" not in query and not any(d in query for d in ("today", "tomorrow", "yesterday", "monday","tuesday","wednesday","thursday","friday","saturday","sunday"))):
            missing.append("time")
        if ("today" not in query and "tomorrow" not in query and "-" not in query and not any(day in query for day in ("monday","tuesday","wednesday","thursday","friday","saturday","sunday"))):
            missing.append("date")

    return missing
