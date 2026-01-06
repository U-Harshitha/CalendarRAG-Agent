from fastapi.testclient import TestClient
from backend import main

app = main.app
client = TestClient(app)


def test_create_when_mcp_down(monkeypatch):
    # Simulate MCP being down by making call_tool return an error dict
    def fake_call_tool(tool_name, payload):
        return {"error": f"Failed to reach MCP tool {tool_name} at http://localhost:9000."}

    monkeypatch.setattr(main, "call_tool", fake_call_tool)

    payload = {
        "title": "Make demo video",
        "date": "2026-01-06",
        "start_time": "12:34",
    }

    resp = client.post("/create", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("result") == "FAIL"
    assert "Failed to reach MCP tool create_event" in data.get("error", "")
