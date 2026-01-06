import os
import logging

from fastapi import FastAPI, HTTPException, Request

from .auth import get_calendar_service
from .schemas import *

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from googleapiclient.errors import HttpError

app = FastAPI()

# configure logger for this module
logger = logging.getLogger("mcp.google_calendar_server")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

service = None

# Timezone for events; make configurable via CALENDAR_TIMEZONE env var.
TIMEZONE = os.getenv("CALENDAR_TIMEZONE", "Asia/Kolkata")


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
def create_event(req: CreateEventInput, request: Request):
    # Wrap the whole handler to ensure we surface controlled HTTP errors instead of unhandled exceptions
    try:
        # Log incoming payload for debugging
        try:
            logger.info("create_event called with payload: %s", req.dict())
        except Exception:
            logger.info("create_event called (could not serialize payload)")
        # Parse start/end into timezone-aware ISO datetimes (RFC3339) expected by Google API
        try:
            # assume req.start_time and req.end_time are HH:MM strings
            start_naive = datetime.fromisoformat(f"{req.date}T{req.start_time}")
            end_naive = datetime.fromisoformat(f"{req.date}T{req.end_time}") if req.end_time else (start_naive + timedelta(hours=1))
        except Exception:
            # fallback to building strings directly (will likely fail downstream with a clearer error)
            start_naive = None
            end_naive = None

        if start_naive is not None:
            tz = ZoneInfo(TIMEZONE)
            start_dt = start_naive.replace(tzinfo=tz)
            end_dt = end_naive.replace(tzinfo=tz)
            start = start_dt.isoformat()
            end = end_dt.isoformat()
        else:
            # build safe fallback strings; avoid inserting the literal 'None'
            start = f"{req.date}T{req.start_time}:00" if req.start_time else f"{req.date}T00:00:00"
            if req.end_time:
                end = f"{req.date}T{req.end_time}:00"
            else:
                # default to one hour after start if start_time present, otherwise default midnight+1
                try:
                    tmp = datetime.fromisoformat(start)
                    tmp_end = tmp + timedelta(hours=1)
                    end = tmp_end.isoformat()
                except Exception:
                    end = f"{req.date}T01:00:00"

        # Build event body
        event = {
            "summary": req.title,
            "description": req.description,
            "location": req.location,
            "start": {"dateTime": start, "timeZone": TIMEZONE},
            "end": {"dateTime": end, "timeZone": TIMEZONE},
        }

    svc = _get_service()

        # Check for conflicts in the requested time window. Use UTC timestamps for the query
        # to avoid any formatting issues with local offsets.
        try:
            tz = ZoneInfo(TIMEZONE)
            # Reconstruct aware datetimes and convert to UTC for the query
            start_dt = datetime.fromisoformat(f"{req.date}T{req.start_time}").replace(tzinfo=tz)
            if req.end_time:
                end_dt = datetime.fromisoformat(f"{req.date}T{req.end_time}").replace(tzinfo=tz)
            else:
                end_dt = start_dt + timedelta(hours=1)

            # Format UTC times with trailing 'Z' to match RFC3339 (Google accepts offsets but Z is safe)
            start_utc_dt = start_dt.astimezone(ZoneInfo('UTC'))
            end_utc_dt = end_dt.astimezone(ZoneInfo('UTC'))
            start_utc = start_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_utc = end_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            start_utc = start
            end_utc = end

        # Use Freebusy query to check availability (recommended over events.list for availability)
        try:
            fb_req = {
                "timeMin": start_utc,
                "timeMax": end_utc,
                "timeZone": TIMEZONE,
                "items": [{"id": "primary"}],
            }
            fb = svc.freebusy().query(body=fb_req).execute()
        except HttpError as he:
            raise HTTPException(status_code=400, detail=f"Google Calendar API error when freebusy querying: {he}")

        calendars = fb.get("calendars", {})
        primary = calendars.get("primary", {}) if isinstance(calendars, dict) else {}
        busy = primary.get("busy", []) if isinstance(primary, dict) else []

        if busy:
            # There is at least one busy slot overlapping requested time
            conflicts = busy
            # Prepare alternative suggestions by trying the next 5 hourly slots and checking freebusy
            suggestions = []
            base = None
            try:
                base = datetime.fromisoformat(start)
            except Exception:
                base = None

            if base is not None:
                for i in range(1, 6):
                    cand_start = base + timedelta(hours=i)
                    cand_end = cand_start + (datetime.fromisoformat(end) - datetime.fromisoformat(start))
                    cand_start_iso = cand_start.isoformat()
                    cand_end_iso = cand_end.isoformat()
                    fb_req = {"timeMin": cand_start_iso, "timeMax": cand_end_iso, "items": [{"id": "primary"}]}
                    try:
                        cand_fb = svc.freebusy().query(body=fb_req).execute()
                        cand_busy = cand_fb.get("calendars", {}).get("primary", {}).get("busy", [])
                    except Exception:
                        cand_busy = [1]

                    if not cand_busy:
                        suggestions.append({"start": cand_start_iso, "end": cand_end_iso})

                    if len(suggestions) >= 3:
                        break

            logger.info("create_event conflict detected: %s suggestions: %s", conflicts, suggestions)
            return {
                "conflict": True,
                "conflicts": conflicts,
                "suggestions": suggestions,
            }

        # No conflicts -> create the event
        try:
            created = svc.events().insert(calendarId="primary", body=event).execute()
            return created
        except HttpError as he:
            logger.exception("Google Calendar API error when inserting event")
            raise HTTPException(status_code=400, detail=f"Google Calendar API error when inserting event: {he}")
    except HTTPException:
        # re-raise HTTPException as-is
        raise
    except Exception as e:
        logger.exception("create_event unexpected error")
        # Catch-all to ensure we return a useful HTTP error instead of a 500
        raise HTTPException(status_code=400, detail=f"create_event unexpected error: {e}")
