import streamlit as st
import requests
from datetime import datetime

BACKEND_URL = "http://localhost:8000"

st.set_page_config(page_title="CalendarRAG Agent", layout="wide")
st.title("CalendarRAG Agent")


def _get_calendar_status():
    try:
        # Slightly larger timeout so backend has a bit more time to respond on slower machines
        return requests.get(f"{BACKEND_URL}/calendar/status", timeout=5).json()
    except requests.RequestException as e:
        return {"error": str(e)}


def _connect_calendar():
    try:
        return requests.post(f"{BACKEND_URL}/calendar/connect", timeout=120).json()
    except requests.RequestException as e:
        return {"connected": False, "error": str(e)}


def _post_create(payload: dict):
    try:
        return requests.post(f"{BACKEND_URL}/create", json=payload, timeout=60).json()
    except requests.RequestException as e:
        return {"result": "FAIL", "error": str(e)}


with st.sidebar:
    st.header("Connection")
    status = _get_calendar_status()
    has_token = bool(status.get("has_token_json"))
    has_creds = bool(status.get("has_credentials_json"))

    if status.get("error"):
        st.error(f"Backend not reachable: {status['error']}")
        st.info("Start the backend: python -m uvicorn backend.main:app --port 8000 --reload")
    else:
        st.write(f"Credentials configured: {has_creds}")
        st.write(f"Authorized (token present): {has_token}")

    if not has_token:
        st.info("First-time setup: click Connect to open Google OAuth in your browser.")
        if st.button("Connect Google Calendar"):
            connect_result = _connect_calendar()
            if connect_result.get("connected"):
                st.success("Connected. You can now ask questions.")
            else:
                st.error(f"Connect failed: {connect_result.get('error', connect_result)}")
            st.rerun()


if "messages" not in st.session_state:
    st.session_state.messages = []

if "memory" not in st.session_state:
    st.session_state.memory = {"pending_create": None}


for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"]) 
        if m.get("events"):
            st.subheader("Upcoming events")
            for ev in m["events"]:
                title = ev.get("title", "(No title)")
                start = ev.get("start", "")
                end = ev.get("end", "")
                with st.expander(f"{title} ({start} → {end})"):
                    if ev.get("location"):
                        st.write(f"Location: {ev['location']}")
                    if ev.get("link"):
                        st.markdown(f"[Open in Google Calendar]({ev['link']})")

        # render confidence and tool usage if available
        if m.get("details"):
            details = m.get("details")
            refs = details.get("references", [])
            conf = details.get("confidence")
            if refs:
                st.write(f"Tools: {', '.join(refs)}")
            if conf is not None:
                try:
                    pct = int(conf * 100)
                except Exception:
                    try:
                        pct = int(float(conf))
                    except Exception:
                        pct = 0
                st.progress(pct)


prompt = st.chat_input("Ask about your calendar (e.g., 'what are my events next week?')")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                response = requests.post(
                    f"{BACKEND_URL}/query",
                    json={"query": prompt},
                    timeout=60,
                ).json()
            except requests.RequestException as e:
                response = {
                    "answer": f"Request failed: {e}",
                    "summary": "",
                    "events": [],
                    "references": [],
                    "confidence": 0.0,
                    "result": "FAIL",
                }

        # Display assistant answer
        st.markdown(response.get("answer", ""))

        events = response.get("events") or []
        if events:
            st.subheader("Upcoming events")
            for ev in events:
                title = ev.get("title", "(No title)")
                start = ev.get("start", "")
                end = ev.get("end", "")
                with st.expander(f"{title} ({start} → {end})"):
                    if ev.get("location"):
                        st.write(f"Location: {ev['location']}")
                    if ev.get("link"):
                        st.markdown(f"[Open in Google Calendar]({ev['link']})")

        # If backend asks for more info, store pending creation and show clarification inputs
        if response.get("result") == "NEEDS_MORE_INFO":
            st.session_state.memory["pending_create"] = {
                "missing_slots": response.get("missing_slots", []),
                "known_fields": response.get("known_fields", {}),
            }
            st.info("I need a few more details to create your event. Please fill them below (sidebar).")

        # If conflict, show suggestions as buttons
        if response.get("result") == "CONFLICT":
            st.warning("The requested time conflicts with existing events. Here are suggested alternatives:")
            suggestions = response.get("suggestions", [])
            attempted = response.get("attempted_payload") or {}
            for s in suggestions:
                # parse suggestion time string like 2026-01-05T18:00:00
                start_raw = s.get("start")
                end_raw = s.get("end")
                try:
                    date_part, time_part = start_raw.split("T")
                    start_time = ":".join(time_part.split(":")[0:2])
                    _, end_time_full = end_raw.split("T")
                    end_time = ":".join(end_time_full.split(":")[0:2])
                except Exception:
                    date_part = start_raw
                    start_time = start_raw
                    end_time = end_raw

                if st.button(f"Use {date_part} {start_time} - {end_time}"):
                    # build payload from attempted + suggestion
                    payload = {
                        "title": attempted.get("title") or "(No title)",
                        "date": date_part,
                        "start_time": start_time,
                        "end_time": end_time,
                        "description": attempted.get("description", ""),
                        "location": attempted.get("location", ""),
                    }
                    create_res = _post_create(payload)

                    if create_res.get("result") == "PASS":
                        st.success("Event created successfully.")
                    elif create_res.get("result") == "CONFLICT":
                        st.error("Still conflicts. Try another suggestion.")
                    else:
                        st.error(f"Failed to create event: {create_res.get('error')}")

        with st.expander("Details"):
            st.write(f"Result: {response.get('result')}")
            st.write(f"Confidence: {response.get('confidence')}")
            st.write("References:")
            st.write(response.get("references", []))

    # add assistant response to session history with some metadata for display
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": response.get("answer", ""),
            "events": response.get("events") or [],
            "details": {
                "references": response.get("references", []),
                "confidence": response.get("confidence"),
                "result": response.get("result"),
            },
            "raw": response,
        }
    )


# If there's a pending create in memory, render the clarification form in the sidebar
if st.session_state.memory.get("pending_create"):
    pending = st.session_state.memory["pending_create"]
    st.sidebar.header("Provide missing details")
    known = pending.get("known_fields", {})
    missing = pending.get("missing_slots", [])
    filled = {}
    if "title" in missing:
        filled["title"] = st.sidebar.text_input("Title", value=known.get("title") or "")
    if "date" in missing:
        try:
            default_date = known.get("date")
            if default_date:
                default_date_val = datetime.fromisoformat(default_date).date()
            else:
                default_date_val = None
        except Exception:
            default_date_val = None
        filled["date"] = st.sidebar.date_input("Date", value=default_date_val)
    if "time" in missing:
        filled["start_time"] = st.sidebar.text_input("Start time (HH:MM)", value=known.get("start_time") or "")
    if st.sidebar.button("Submit details"):
        # build payload from known + filled
        date_val = filled.get("date")
        if hasattr(date_val, "isoformat"):
            date_str = date_val.isoformat()
        else:
            date_str = date_val or known.get("date")

        payload = {
            "title": filled.get("title") or known.get("title") or "(No title)",
            "date": date_str,
            "start_time": filled.get("start_time") or known.get("start_time"),
            "end_time": known.get("end_time") or None,
            "description": "",
            "location": known.get("location") or "",
        }
        create_res = _post_create(payload)

        if create_res.get("result") == "PASS":
            st.sidebar.success("Event created successfully.")
            st.session_state.memory["pending_create"] = None
        elif create_res.get("result") == "CONFLICT":
            st.sidebar.error("Conflict detected. See main chat for suggestions.")
            st.session_state.messages.append({
                "role": "assistant",
                "content": "Conflict detected when attempting to create event. See suggestions below.",
                "events": [],
                "details": {"references": ["Google Calendar"], "confidence": 0.0, "result": "CONFLICT"},
                "raw": create_res,
            })
        else:
            st.sidebar.error(f"Failed to create event: {create_res.get('error')}")
