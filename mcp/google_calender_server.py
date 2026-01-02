import os

from fastapi import FastAPI, HTTPException

from .auth import get_calendar_service
from .schemas import *

app = FastAPI()

service = None


def _get_service():
    global service
    if service is not None:
        return service

    try:
        service = get_calendar_service()
        return service
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Google Calendar service is not configured or failed to initialize: {e}",
        )


@app.get("/auth/status")
def auth_status():
    return {
        "has_credentials_json": os.path.exists("credentials/credentials.json"),
        "has_token_json": os.path.exists("credentials/token.json"),
    }


@app.post("/auth/connect")
def auth_connect():
    _get_service()
    return {"connected": True}

@app.post("/list_events")
def list_events(req: ListEventsInput):
    svc = _get_service()
    events_result = svc.events().list(
        calendarId="primary",
        timeMin=req.start_date + "T00:00:00Z",
        timeMax=req.end_date + "T23:59:59Z",
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    return events_result.get("items", [])

@app.post("/get_event_details")
def get_event_details(req: GetEventDetailsInput):
    svc = _get_service()
    return svc.events().get(
        calendarId="primary",
        eventId=req.event_id
    ).execute()

@app.post("/search_events")
def search_events(req: SearchEventsInput):
    svc = _get_service()
    events_result = svc.events().list(
        calendarId="primary",
        q=req.keyword,
        singleEvents=True
    ).execute()

    return events_result.get("items", [])

@app.post("/create_event")
def create_event(req: CreateEventInput):
    start = f"{req.date}T{req.start_time}:00"
    end = f"{req.date}T{req.end_time}:00"

    event = {
        "summary": req.title,
        "description": req.description,
        "location": req.location,
        "start": {"dateTime": start, "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": end, "timeZone": "Asia/Kolkata"},
    }

    svc = _get_service()
    return svc.events().insert(
        calendarId="primary", body=event
    ).execute()
