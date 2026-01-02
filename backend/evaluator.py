def evaluate_response(answer, retrieved_docs, tool_used):
    issues = []

    if not retrieved_docs and not tool_used:
        issues.append("No RAG grounding")

    if (
        ("calendar data:" in answer.lower() or "google calendar" in answer.lower())
        and not tool_used
    ):
        issues.append("Calendar data referenced without tool usage")

    confidence = 1.0 - (0.2 * len(issues))

    return {
        "confidence": max(confidence, 0.0),
        "explanation": " | ".join(issues) if issues else "Answer is properly grounded",
        "pass": len(issues) == 0
    }
