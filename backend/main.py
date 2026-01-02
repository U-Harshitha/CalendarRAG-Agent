import os
from datetime import date, timedelta

import requests
from fastapi import FastAPI
from pydantic import BaseModel

from .agent import call_tool, classify_intent, detect_ambiguity
from .evaluator import evaluate_response

app = FastAPI()

MCP_URL = os.getenv("MCP_URL", "http://localhost:9000")


class QueryRequest(BaseModel):
    query: str


def _mcp_get(path: str):
    response = requests.get(f"{MCP_URL}{path}", timeout=15)
    response.raise_for_status()
    return response.json()


def _mcp_post(path: str, payload=None):
    response = requests.post(f"{MCP_URL}{path}", json=payload or {}, timeout=60)
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


@app.post("/query")
def query_agent(req: QueryRequest):
    query = req.query

    start_date = date.today()
    end_date = start_date + timedelta(days=30)

    tool_data = call_tool(
        "list_events",
        {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    )

    if tool_data is None:
        return {
            "answer": "Calendar integration is not available. Connect Google Calendar first (OAuth) and ensure the MCP server is running.",
            "references": [],
            "confidence": 0.0,
            "result": "FAIL",
            "events": [],
            "summary": "",
        }

    events = _normalize_events(tool_data)

    retrieved_docs = []

    if not events:
        return {
            "answer": "I do not have sufficient context to answer this question.",
            "references": [],
            "confidence": 0.0,
            "result": "FAIL",
            "events": [],
            "summary": "",
        }

    # 2️⃣ Ambiguity check
    missing = detect_ambiguity(query)
    if missing:
        return {
            "answer": f"The query is ambiguous. Please provide the following details: {', '.join(missing)}.",
            "references": [],
            "confidence": 0.6,
            "result": "PASS"
        }

    # 4️⃣ Final answer (calendar-first)
    if events:
        summary = f"Found {len(events)} event(s) from {start_date.isoformat()} to {end_date.isoformat()}."
        answer = summary
    else:
        summary = f"No events found from {start_date.isoformat()} to {end_date.isoformat()}."
        answer = summary

    evaluation = evaluate_response(
        answer,
        retrieved_docs,
        True,
    )

    return {
        "answer": answer,
        "summary": summary,
        "events": events,
        "references": (["Google Calendar"] if events else []),
        "confidence": evaluation["confidence"],
        "result": "PASS" if evaluation["pass"] else "FAIL",
    }
