import streamlit as st
import requests

BACKEND_URL = "http://localhost:8000"

st.set_page_config(page_title="CalendarRAG Agent", layout="wide")
st.title("CalendarRAG Agent")


def _get_calendar_status():
    try:
        return requests.get(f"{BACKEND_URL}/calendar/status", timeout=2).json()
    except requests.RequestException as e:
        return {"error": str(e)}


def _connect_calendar():
    try:
        return requests.post(f"{BACKEND_URL}/calendar/connect", timeout=120).json()
    except requests.RequestException as e:
        return {"connected": False, "error": str(e)}


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

        with st.expander("Details"):
            st.write(f"Result: {response.get('result')}")
            st.write(f"Confidence: {response.get('confidence')}")
            st.write("References:")
            st.write(response.get("references", []))

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": response.get("answer", ""),
            "events": response.get("events") or [],
        }
    )
