import os

import requests

MCP_URL = os.getenv("MCP_URL", "http://localhost:9000")

def call_tool(tool_name, payload):
    try:
        response = requests.post(
            f"{MCP_URL}/{tool_name}",
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None

def classify_intent(query: str):
    query = query.lower()

    if "create" in query or "schedule" in query:
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

    if "schedule" in query or "create" in query:
        if "am" not in query and "pm" not in query and ":" not in query:
            missing.append("time")
        if "today" not in query and "tomorrow" not in query and "-" not in query:
            missing.append("date")

    return missing
