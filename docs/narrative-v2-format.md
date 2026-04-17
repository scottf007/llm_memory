# Narrative v2 Format Specification

## Purpose
A project narrative is a **living reference document** loaded into every session. It tells
a future Claude session everything it needs to be productive immediately — what this project
is, how we got here, what works, what doesn't, and where to pick up.

It is NOT a journal. Sessions are not tracked as prose — provenance lives in `transcript_ref`
metadata. Content from old sessions gets **dissolved** into standing sections and the session
entry is dropped.

## Token Budget
Target: **≤5,000 tokens**. This is loaded on every session start, so it must stay lean.
When a narrative approaches the cap, dissolve older content per the rules below.

## Section Specs

Each section has required fields, carry-forward rules, and validation checks.
Full specs: **[narrative-section-specs.md](narrative-section-specs.md)**.

The narrative pipeline (delta-extractor → merger → renderer) MUST produce
sections that satisfy each spec. The renderer enforces the structure; the
delta-extractor must extract enough signal per section to keep nothing silently
dropped across updates. See section specs doc for per-section rules.

## Sections

All sections are required. Order matters — most critical context first.

### 1. The Idea
2-3 sentences. What this project is and what problem it solves. Should be stable — only
changes if the project pivots.

### 2. Approach
Table of **non-obvious decisions** with rationale. Include the user's own words where
they explain *why*.

```
| Decision | Rationale |
|----------|-----------|
| BBB over scorecard | Scorecard batting order unreliable — 7/9 wrong in R15 |
```

**Dissolve rule**: Drop decisions that are obvious from the code (e.g. "chose Python").
Keep decisions where a future session would be tempted to do it differently without
context. If you wouldn't second-guess it by reading the codebase, it doesn't need to
be here.

### 3. What's Done
Current state of the build. Describe **what exists now**, not how it got there.

- Bad: "Fixed from single FK to player_teams association table"
- Good: "Player-team relationship is many-to-many via `player_teams` table"

Include key file paths — the 3-5 entry points a session needs to start navigating:
```
Key files: app.py (FastAPI routes), models.py (SQLAlchemy), rules/nsjca_v14.yaml (compliance config)
```

### 4. Operations
How to access, deploy, and monitor the project. Table format:

```
| Item | Detail |
|------|--------|
| Repo | github.com/user/project |
| Hosting | thor NAS, 192.168.1.50, SSH as scott |
| Deploy | `git pull && docker compose up -d` on thor |
| Monitoring | Grafana at http://192.168.1.50:3000 |
```

Include all that apply: Repo, Hosting, Deploy, Environments, CI/CD, Monitoring,
Credentials (pointers only — never actual secrets), Key URLs, Network.

**NEVER dissolve.** Operational facts only change when infrastructure changes.
This section is exempt from token budget pressure.

### 5. What We've Learnt
Bullet points of **gotchas that could bite a future session**. Things that aren't obvious
from the code, where someone would waste time re-discovering them.

**Dissolve rule**: Drop lessons where the fix is already in the code and the mistake
can't be repeated. "Template paths must be absolute" — fixed, won't recur, drop it.
Keep lessons about external systems, API quirks, domain rules, and non-obvious
constraints.

### 6. What We Want To Do
User-stated goals and intentions. Things the user has explicitly said they want.
Not Claude's suggestions — those go in Suggested Work.

On each update, every existing item must be classified: **still pending**, **completed**
(move to What's Done), or **abandoned** (drop with reason). No item silently disappears.

### 7. Suggested Work
Claude's recommendations — things worth doing that the user hasn't asked for.
Cleanup, refactoring, test gaps, potential improvements. The user can promote these
to "What We Want To Do" or ignore them.

Suggestions dissolve after 3 narrative cycles if not acted on.

### 8. Resuming
The last **piece of work** (not last session — work can span sessions or a session
can have multiple pieces of work). Includes:

- **Status**: `complete` or `interrupted`
- If interrupted: what was in progress, where it stopped, what's left
- If complete: what was finished, ready for new work
- Brief context on what was being done (2-3 sentences max)

This section gets **rewritten** when focus changes, not appended to. If previous
status was `interrupted` and the new transcript doesn't resolve it, mention the
unresolved work.

### 9. Source Transcripts
Two parts:

**Recent** (last 5-10): Annotated lookup table for finding decisions in raw transcripts.
```
| Date | File | What's In It |
|------|------|-------------|
| Mar 10 | 6914c4d2.jsonl | Compliance YAML rules, season summary, code audit |
| Mar 10 | a843ef2d.jsonl (agent) | Compliance module build |
```

**Older**: Dissolved into standing sections, rows dropped. One line pointing to the DB:
```
Full transcript list: use narrative_coverage(project="PROJECT") or memory_get(uuid="NARRATIVE_UUID").
```

## Dissolving Content

The key principle: **information doesn't disappear, it changes form.** When a session's
decisions are captured in Approach, its lessons in What We've Learnt, and its outcomes
in What's Done — the session entry in Source Transcripts can be dropped. The content
lives on in the standing sections.

Priority order when trimming to stay under 5k tokens:
1. Drop Source Transcript rows for fully-dissolved sessions
2. Drop Suggested Work items that are stale (3+ cycles) or irrelevant
3. Drop What We've Learnt items where the fix is in the code
4. Drop Approach decisions that are obvious from the codebase
5. Compress What's Done prose (combine related items)
6. Never trim The Idea, Operations, Resuming, or What We Want To Do — these are always critical

## What Does NOT Go in the Narrative

- **Code patterns, schema, architecture**: Derivable from the codebase. Read the files.
- **Git history**: Use `git log`. The narrative tracks decisions, not commits.
- **Full session chronology**: That's a journal, not a reference document.
- **How the memory system works**: CLAUDE.md and session_start hook handle that.
- **Debugging play-by-play**: Only the lesson survives, not the 20-minute debugging loop.
