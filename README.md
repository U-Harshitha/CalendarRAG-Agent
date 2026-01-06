# CalendarRAG-Agent
An Agentic RAG System with MCP Tools and Calendar Integration

Design and implement a containerized, agentic AI system that can answer user queries by
combining Retrieval-Augmented Generation (RAG) with Model Context Protocol (MCP)
tools for Google Calendar access. The system should demonstrate the ability to retrieve
and reason over knowledge base, interact with external tools and evaluate the correctness
of its own outputs.
Instructions:
1. Setup Google Calendar
○ Add 8-10 events to your Google Calendar
○ Set up Google Cloud project and enable Google Calendar API
○ Create OAuth 2.0 credentials and obtain the necessary credentials file
2. Build MCP Server (google_calendar_server.py) Create 4 tools:
○ list_events - Get events between two dates (inputs: start_date, end_date)
○ get_event_details - Get details of a specific event (input: event_id)
○ search_events - Search events by keyword (input: keyword)
○ create_event - Check availability and create event (inputs: title, date,
start_time, end_time, description, location)
Note: You may include additional fields in your tool call definitions as needed
3. When a user asks a question, your system should:
○ Search the knowledge base and retrieve the most relevant document
chunks using semantic similarity
○ Pass the retrieved content to a language model
○ Generate a context-aware answer grounded only in the retrieved documents
The system must not hallucinate or answer beyond the provided context.
4. Build MCP Client (calendar_client.py)
○ Connect to the MCP server

○ Test all 4 tools with different inputs

Notes about this repository changes:
- The MCP server module file name has been standardized to `mcp/google_calendar_server.py` (previously a misspelled duplicate existed).
- A small test client `calendar_client.py` was added at the repo root to exercise the MCP endpoints (safe list/search tests and optional create test via `--create`).
5. Add an Agentic Evaluation Step
○ After generating an answer, evaluate the response automatically
○ Check that the answer is supported by the retrieved knowledge base and/or
Google Calendar data
○ Verify that the correct MCP tools were used
○ Detect any unsupported or made-up information
○ Output a confidence score, short explanation, references, and a pass/fail
result

-  You should feed any publicly available data relevant to the assignment into the RAG knowledge base. The system should answer using a combination of this retrieved RAG context and Google Calendar data accessed via MCP tools. It should not rely on unstated assumptions or knowledge outside the retrieved context.
- Yes, you may assume IST for now, as long as the assumption is clearly stated.
- If a query is ambiguous but can be resolved with additional user input, the system should ask for clarification. An “invalid query” response is acceptable only when mandatory information is missing and clarification is not reasonably possible.