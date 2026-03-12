# Agent Memory Design — Making Agents Context-Aware

## Problem

Agents (subagents spawned via the Agent tool) are blind. They:
- Don't get session_start hooks (no narrative loaded)
- Start with a fresh context containing only their prompt + CLAUDE.md
- Have access to MCP tools but don't know to use them
- Don't know project conventions, decisions, or gotchas
- Create work in unpredictable locations (worktrees, temp files)
- Can't signal the parent session when they've updated something

This leads to: low-quality narratives, duplicated decisions, repeated mistakes,
lost work (worktree cleanup), and wasted tokens re-discovering context.

## Solution: Three Layers

### Layer 1: Agent Definitions with Memory Preamble

Create file-based agent definitions in `.claude/agents/` that include
memory-loading instructions in their system prompt.

```
~/.claude/agents/memory-aware.md
```

```yaml
---
name: memory-aware
description: General-purpose agent with project memory loaded
tools: "*"
---

## Memory Protocol
You have access to persistent memory tools via the llm_memory MCP server.
Tool names are prefixed with mcp__llm_memory__ (e.g., mcp__llm_memory__memory_search).

BEFORE starting any work:
1. Call mcp__llm_memory__memory_search with query relevant to your task
   and the project name from your prompt.
2. Call mcp__llm_memory__memory_recent with project filter and type="narrative"
   to load the project narrative.
3. Call mcp__llm_memory__memory_recent with project filter and type="note"
   and limit=10 to load recent important notes.

Use this context to inform your work. Do not repeat mistakes documented
in the narrative's "Gotchas & Lessons" section.

## File Conventions
- Never use worktree isolation unless explicitly told to
- Working files go in the project directory, prefixed with `tmp_`
- Clean up tmp_ files when done
- Write results to the project directory, not /tmp
```

Specialised agents extend this:

```
~/.claude/agents/narrative-updater.md
```

```yaml
---
name: narrative-updater
description: Reads transcripts and updates project narratives
tools: Read, Glob, Grep, Bash, mcp__llm_memory__memory_store,
       mcp__llm_memory__memory_search, mcp__llm_memory__memory_get,
       mcp__llm_memory__memory_recent, mcp__llm_memory__memory_delete
---

## Your Job
You update project narratives from raw JSONL transcripts.

## Process
1. Call memory_recent with project=PROJECT and type="narrative" to get
   the current narrative (if any). Note its UUID.
2. Identify which transcripts are new since the last narrative update.
3. Read the new transcripts from ~/.claude/memory/transcripts/.
   For large files (>1MB), read in chunks using offset/limit.
4. Write the updated narrative using memory_store with type="narrative"
   and project=PROJECT. The server will auto-delete the old narrative.
5. Write a signal file: echo "updated" > PROJECT_DIR/.narrative_updated

## Narrative Format (all sections required)
- **What This Is**: 2-3 sentences
- **Session History**: One line per session
- **Decisions Made**: Table — decision | rationale (user's own words)
- **Gotchas & Lessons**: Bullet points
- **Current State**: What exists, what works
- **Outstanding Items**: Nothing gets lost here
- **Direction**: Numbered next steps
- **Source Transcripts**: List of JSONL files used

## Rules
- Write from raw JSONL transcripts, NEVER from summaries
- Include the user's exact words for key decisions
- Preserve debugging journeys and pivots, not just outcomes
- Keep it under 8000 characters
```

### Layer 2: SubagentStart Hook — Auto-Inject Context

Use the `SubagentStart` hook (fires in parent session when an agent starts)
to prepare context. This hook can't inject into the agent's context directly,
but it can write a context file that the agent's preamble tells it to read.

**Hook: SubagentStart** (in settings.json or hooks config)

```bash
#!/bin/bash
# hooks/subagent_start.sh
# Fires when a subagent is spawned. Writes a context file the agent can read.

INPUT=$(cat)
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

DB="$HOME/.claude/memory/memory.db"
[ -f "$DB" ] || exit 0

PROJECT=""
if [ -n "$CWD" ]; then
    PROJECT=$(echo "$CWD" | sed -n 's|.*/projects/\([^/]*\).*|\1|p')
fi
[ -z "$PROJECT" ] && exit 0

# Write context file for the agent to read
CONTEXT_FILE="$CWD/.agent_context_${AGENT_ID}.md"

{
    echo "## Agent Context (auto-generated for agent $AGENT_ID)"
    echo "## Project: $PROJECT"
    echo ""

    # Load narrative
    NARRATIVE=$(sqlite3 "$DB" "SELECT content FROM memories WHERE type='narrative' AND project='$PROJECT' ORDER BY created_at DESC LIMIT 1;" 2>/dev/null)
    if [ -n "$NARRATIVE" ]; then
        echo "## Project Narrative"
        echo "$NARRATIVE"
        echo ""
    fi

    # Load important notes
    NOTES=$(sqlite3 -separator '|' "$DB" "SELECT substr(content, 1, 300) FROM memories WHERE type='note' AND project='$PROJECT' AND importance >= 7 ORDER BY importance DESC, created_at DESC LIMIT 10;" 2>/dev/null)
    if [ -n "$NOTES" ]; then
        echo "## Important Notes"
        echo "$NOTES"
    fi
} > "$CONTEXT_FILE"

# Tell the parent that context was written (output goes to parent)
echo "Agent context written to $CONTEXT_FILE"
```

Then in the agent preamble (Layer 1), add:

```
## Auto-Context Loading
If a file matching .agent_context_*.md exists in the working directory,
read it FIRST before doing anything else. It contains your project context.
Delete it when you're done.
```

### Layer 3: Parent Prompt Enrichment

When the parent session (Claude) spawns an agent, it should include relevant
context in the prompt. This is the most reliable layer because it doesn't
depend on hooks or agent self-loading.

**The parent MUST include in every agent prompt:**
1. The project name
2. Key narrative context (current state, decisions, gotchas)
3. The specific task
4. File conventions
5. How to report results

**Template for parent to follow** (put in CLAUDE.md):

```
## Spawning Agents

When spawning an agent for project work, ALWAYS include in the prompt:

1. "Project: {project_name}"
2. Current state summary from the narrative (2-3 sentences)
3. Relevant decisions and gotchas that affect the task
4. "File conventions: work in {project_dir}, prefix temp files with tmp_,
   clean up when done. Do NOT use worktree isolation."
5. "When done, summarise what you changed and which files were modified."

For narrative update agents specifically:
- Include which transcript files are new
- Include the current narrative UUID if updating
- Say: "Store via mcp__llm_memory__memory_store with type='narrative',
  project='{project}'"
```

## Narrative Update Flow

When the session_start hook detects a stale narrative, this is what should happen:

```
┌─────────────────────────────────────────────────────────┐
│ session_start.sh detects stale narrative                │
│ Output: "AUTOMATIC TASK: 5 new sessions. Update NOW."   │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│ Parent Claude spawns narrative-updater agent             │
│ Prompt includes: project name, current narrative UUID,  │
│ list of new transcript files, narrative format spec      │
│ NO worktree isolation — runs in project directory        │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│ Agent reads current narrative via MCP                    │
│ Agent reads new transcripts from disk                   │
│ Agent stores updated narrative via memory_store          │
│ (server auto-deletes old narrative)                      │
│ Agent writes signal: .narrative_updated                  │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│ Agent returns summary to parent                         │
│ Parent sees "narrative updated" in result               │
│ Parent reloads narrative via memory_get                  │
│ Parent continues with fresh context                     │
└─────────────────────────────────────────────────────────┘
```

## File & Worktree Conventions

### Rules for Agents

| Rule | Rationale |
|------|-----------|
| Default: NO worktree isolation | Worktrees get cleaned up, changes vanish |
| Use worktrees ONLY for risky/exploratory work | And commit to a named branch like `fix/description` |
| Temp files: `tmp_*.md` in project dir | Predictable, findable, cleanable |
| Clean up temp files when done | Don't leave garbage |
| Write results to project dir, not /tmp | /tmp is ephemeral across sessions |
| Name output files descriptively | `tmp_audit_results.md` not `output.txt` |

### Rules for Parent (Claude)

| Rule | Rationale |
|------|-----------|
| Never use `isolation: "worktree"` unless explicitly asked | Three agents lost work this way |
| Include file conventions in every agent prompt | Agents don't read narratives by default |
| After agent completes, verify changes exist | Don't trust "done" — check the files |
| For code changes: tell agent to NOT commit, just modify | Parent controls git |

## Implementation Status

### Phase 1: Agent Definitions — NOT STARTED
- `.claude/agents/memory-aware.md` — not yet created
- `.claude/agents/narrative-updater.md` — not yet created
- CLAUDE.md agent spawning template — not yet added

### Phase 2: SubagentStart/Stop Hooks — DONE
- `hooks/subagent_start.sh` — reads narrative + important notes (importance>=7) from DB,
  outputs JSON with `hookSpecificOutput.additionalContext` for injection into agent context.
  Uses `jq` for proper JSON escaping. Falls through silently if no DB or no project.
- `hooks/subagent_stop.sh` — checks for `.narrative_updated` signal file when agent_type
  contains "narrative". Outputs reload message and cleans up signal file.
- **16 tests in test_agent_memory.py — all passing.**

### Phase 3: Parent Prompt Protocol — NOT STARTED
- CLAUDE.md "Spawning Agents" section not yet added
- File convention rules not yet in CLAUDE.md

### Phase 4: Narrative Auto-Update — PARTIALLY DONE
- session_start.sh already outputs "AUTOMATIC TASK: You MUST update" for stale/missing narratives
- session_start.sh cross-project check now detects stale narratives (not just missing)
- pre_compact.sh now says "Update the narrative NOW" (not deferred)
- **But**: No agent definitions exist to actually automate this. Claude must manually follow instructions.

### Phase 5: Subagent Transcript Processing — DONE
- `process_transcripts.py` now scans `*/subagents/*.jsonl` via `find_transcripts()`
- Deduplication via `seen_sessions` set
- `extract_session_data()` extracts `parentSessionId` from subagent transcripts
- Session logs tagged with "agent" tag and reference parent session in content
- **5 tests in test_agent_memory.py — all passing.**

### Phase 6: Server Narrative Uniqueness — DONE
- `server.py` enforces one narrative per project — auto-deletes old before storing new
- Supersedes connection recorded in JSON file
- Rejects narratives with null/empty project
- `full_rebuild` deduplicates narratives per project after import
- **8 tests in test_server.py — all passing.**

### Phase 7: Hook Staleness Detection — DONE
- Post-compaction path checks narrative staleness (was silent before)
- Cross-project query uses UNION for stale AND missing narratives
- **5 tests in test_hooks.py — all passing.**

### Phase 8: Project Derivation — DONE
- Non-`/projects/` folders use last path component (4+ components, not home dir)
- Content-based derivation from repeated bigrams/keywords when cwd doesn't map
- **4 tests in test_hooks.py — all passing.**

## Remaining Work

1. **Agent definitions** (Phase 1): Create `.claude/agents/memory-aware.md` and
   `narrative-updater.md` with YAML frontmatter. These define agent system prompts.

2. **CLAUDE.md agent protocol** (Phase 3): Add "Spawning Agents" section with
   prompt template, file conventions, and post-agent verification checklist.

3. **Hook registration**: Register SubagentStart and SubagentStop hooks in
   Claude Code settings.json.

4. **End-to-end test**: Spawn a real agent and verify it receives narrative
   via additionalContext injection.

## Audit Findings (2026-03-12)

### Narrative Generation is 100% Manual
There is NO code that generates narratives. The entire pipeline is:
1. Hooks output instructions ("AUTOMATIC TASK: You MUST update...")
2. Claude reads the instructions
3. Claude must manually follow them (read transcripts, write narrative, store via MCP)
4. If Claude doesn't follow through, nothing happens

The agent definitions (Phase 1) and CLAUDE.md protocol (Phase 3) are needed to
close this gap — they tell Claude exactly how to spawn a narrative-updater agent.

### 467+ Unprocessed Subagent Transcripts (NOW FIXED)
Before this session, `find_transcripts()` only scanned top-level `*.jsonl` files.
Subagent transcripts at `~/.claude/projects/{project}/{session}/subagents/agent-{id}.jsonl`
were completely invisible. Now fixed — process_transcripts.py discovers and processes them.

### SubagentStart Hook Uses additionalContext (Not File-Based)
The original design proposed writing a `.agent_context_{id}.md` file for agents to read.
The actual implementation uses Claude Code's native `hookSpecificOutput.additionalContext`
field, which is injected directly into the agent's context. This is cleaner — no file
cleanup needed, no race conditions, no self-loading required.

## Open Questions

1. **Large transcript handling**: The 42MB finance_nexus transcript can't be
   read in one go. The narrative-updater agent needs chunked reading strategy.

2. **Agent teams vs subagents**: For narrative updates, a simple subagent is
   sufficient. Agent teams would be useful for large multi-file refactors.

3. **Hook registration mechanism**: Need to verify the exact settings.json
   format for SubagentStart and SubagentStop hooks. May need `hookEventName`
   field or separate hook configuration.
