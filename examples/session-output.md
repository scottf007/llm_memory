# Example Session Output

This shows what a typical LLM Memory session looks like from Claude Code's perspective. The annotations explain what's happening at each stage.

---

## 1. Session Starts — Hooks Fire

When you launch `claude` in a project directory, the **SessionStart** hook fires automatically:

```
[SessionStart hook output]

=== LLM Memory Session Context ===
Project: weather-api
Narrative loaded (last updated: 2026-03-08, 2 new session(s) since last update)

--- Project Narrative ---
## What This Is
A FastAPI weather aggregation service that pulls from OpenWeatherMap and
WeatherAPI, normalizes the data, and serves it through a unified REST endpoint.

## Current State
- Core API working with both providers
- Caching layer using Redis with 15-min TTL
- Deployed to staging at weather-staging.internal
- Unit tests passing, integration tests need Redis running

## Outstanding Items
- Rate limiting not implemented yet
- Error responses need standardization (decided on RFC 7807 format)
- Need to add /health endpoint for k8s probes
---

2 new session(s) since last narrative update — consider updating the narrative.
```

> **What happened:** The `session_start.sh` hook called `memory_search` for the current project, found the narrative, and printed it. Claude now has full project context without the user saying anything.

---

## 2. User Asks a Question — Memory Search

The user types:

```
> What format did we decide on for error responses?
```

Claude calls `mcp__llm_memory__memory_search` with query `"error response format"` and project `"weather-api"`:

```
[memory_search result]

Found 1 relevant memory:

  ID: mem_a7f3b2c1
  Type: note
  Project: weather-api
  Content: "Decided on RFC 7807 (Problem Details for HTTP APIs) for all error
           responses. User said 'I want something standard, not another custom
           format that nobody recognizes.' Will use type, title, status, detail
           fields minimum."
  Tags: decision, error-handling, api-design
  Importance: 8
  Created: 2026-03-06
```

Claude responds:

```
You decided on RFC 7807 (Problem Details for HTTP APIs) for error responses,
back on March 6. The minimum fields are type, title, status, and detail.
Your reasoning was that you wanted a recognized standard rather than a custom format.
```

> **What happened:** Instead of saying "I don't have context on that," Claude searched memory and found the exact decision with the user's own rationale.

---

## 3. Work Happens — PostToolUse Monitors

As Claude works (editing files, running tests, etc.), the **PostToolUse** hook runs silently after each tool call. It tracks:

- How many tool calls have happened this session
- Current transcript size
- Time since last memory save

If the transcript gets large, the monitor injects a warning:

```
[PostToolUse hook output — only shown when threshold reached]

WARNING: Transcript is 45KB+ and no memory save in 20+ minutes.
Consider storing important decisions or progress before compaction.
```

> **What happened:** The `session_monitor.sh` hook noticed the session is getting long. Claude can proactively save important context.

---

## 4. Session Ends — Session Log Stored

When the user exits (`/quit` or Ctrl+C), the **SessionEnd** hook fires asynchronously:

```
[SessionEnd hook — runs in background]

Archiving transcript to ~/.claude/memory/transcripts/weather-api_20260311_143022.jsonl
Storing session_log...

  memory_store:
    type: session_log
    project: weather-api
    content: "Implemented RFC 7807 error responses for all API endpoints.
              Added /health endpoint returning 200 with uptime and version.
              Fixed Redis connection pooling bug — was creating new connection
              per request. All unit tests passing."
    tags: error-handling, health-check, redis, bugfix

Session archived successfully.
```

> **What happened:** The `session_end.sh` hook read the raw JSONL transcript, extracted a summary of what happened, and stored it as a `session_log` memory. The raw transcript was also archived for future narrative updates.

---

## Summary of the Flow

```
Session start
  └─ SessionStart hook fires
       └─ Loads narrative → Claude has full context

User works with Claude
  ├─ memory_search finds past decisions when relevant
  ├─ memory_store saves new decisions/corrections
  └─ PostToolUse monitor tracks transcript health

Session end
  └─ SessionEnd hook fires (async)
       ├─ Archives raw transcript
       └─ Stores session_log summary
```

Next session, the narrative gets updated with the new session's work, and the cycle continues.
