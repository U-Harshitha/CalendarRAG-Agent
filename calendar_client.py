"""Simple MCP client to test the MCP Google Calendar tools.

Usage:
  python calendar_client.py       # runs safe tests (list/search)
  python calendar_client.py --create  # will attempt to create a test event

Set MCP_URL env var if your MCP server is running somewhere else (default: http://localhost:9000)
"""
import os
import argparse
from datetime import date, timedelta
import requests

MCP_URL = os.getenv("MCP_URL", "http://localhost:9000")


def post(path, payload):
    url = f"{MCP_URL}{path}"
    print(f"POST {url} -> {payload}")
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def test_list_events():
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=7)).isoformat()
    try:
        res = post("/list_events", {"start_date": start, "end_date": end})
        print(f"list_events returned {len(res)} events")
    except Exception as e:
        print(f"list_events failed: {e}")


def test_search_events():
    try:
        res = post("/search_events", {"keyword": "meeting"})
        print(f"search_events returned {len(res)} events")
    except Exception as e:
        print(f"search_events failed: {e}")


def test_get_event_details(sample_event_id: str):
    try:
        res = post("/get_event_details", {"event_id": sample_event_id})
        print("get_event_details returned:", res)
    except Exception as e:
        print(f"get_event_details failed: {e}")


def test_create_event():
    # default test event: tomorrow 10:00-11:00
    d = (date.today() + timedelta(days=1)).isoformat()
    payload = {
        "title": "Test Event from calendar_client",
        "date": d,
        "start_time": "10:00",
        "end_time": "11:00",
        "description": "Created by calendar_client for testing",
        "location": "",
    }
    try:
        res = post("/create_event", payload)
        print("create_event succeeded:", res.get("id"))
    except Exception as e:
        print(f"create_event failed: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--create", action="store_true", help="Actually create a test event")
    args = parser.parse_args()

    print("Testing MCP server endpoints at:", MCP_URL)
    try:
        status = requests.get(f"{MCP_URL}/auth/status", timeout=5).json()
        print("auth/status:", status)
    except Exception as e:
        print(f"Failed to reach MCP server auth/status: {e}")
        return

    test_list_events()
    test_search_events()

    if args.create:
        test_create_event()


if __name__ == '__main__':
    main()
