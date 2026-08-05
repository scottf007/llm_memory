# Narrative Section Specs

This document defines the **required fields, carry-forward rules, and validation
checks** for each narrative section. The narrative pipeline (delta-extractor →
merger → renderer) MUST follow these specs when updating a narrative. If
validation fails, the issue must be fixed before storing.

## How to Use This Document

When updating a narrative, the agent follows this process for EACH section:

1. Pull the previous content for this section
2. Extract new information from the transcript
3. Apply the section's **carry-forward rules**
4. Generate the updated section
5. Run the section's **validation checks**
6. If validation fails → fix and re-validate

After all sections are updated, run the **cross-section diff check** (see bottom).

---

## Section 1: The Idea

**Purpose**: What this project is and why it exists.

**Required fields**:
- What the project does (1 sentence)
- What problem it solves or who it's for (1 sentence)

**Carry-forward rule**: ALWAYS carry forward. Only change if the project pivots.
A pivot must be explicitly stated by the user — don't infer one from feature work.

**Validation**:
- [ ] Section exists and is 2-3 sentences
- [ ] If previous narrative had this section, core meaning is preserved unless
      transcript contains an explicit pivot

---

## Section 2: Approach

**Purpose**: Non-obvious decisions with rationale so future sessions don't
reverse them without context.

**Required fields**:
- Table with columns: Decision | Rationale
- Each rationale should include the user's reasoning (quote where possible)

**Carry-forward rule**: Decisions persist unless the transcript shows an explicit
reversal. When a decision is reversed, move it to What We've Learnt with the
reason for the reversal.

**On update**:
- New decisions from the transcript → add rows
- Existing decisions not mentioned → keep as-is
- Existing decisions explicitly reversed → remove row, add lesson to What We've Learnt

**Dissolve rule**: Drop decisions obvious from the code (e.g., "chose Python").
Keep decisions where a future session would be tempted to do it differently.

**Validation**:
- [ ] Every decision from the previous narrative is either: present in the
      updated table, OR moved to What We've Learnt with reversal reason
- [ ] No decision silently disappeared

---

## Section 3: What's Done

**Purpose**: Current state of the build — what exists now.

**Required fields**:
- Description of what's built and working
- 3-5 key file paths (entry points for navigation)

**Carry-forward rule**: Update to reflect current state. Old state descriptions
are replaced, not appended. File paths are updated if files moved/renamed.

**On update**:
- New features/components built → add to description
- State changes (e.g., "API now uses v2 auth") → update in place
- File paths that no longer exist → remove and replace

**Validation**:
- [ ] Section describes current state, not history
- [ ] Contains 3-5 key file paths
- [ ] No "Fixed X" or "Changed from X to Y" phrasing — only "X is Y"

---

## Section 4: Operations

**Purpose**: How to access, deploy, and monitor the project. This is the section
that ensures no session starts without knowing where the project runs.

**Required fields** (include all that apply — omit rows that genuinely don't exist):
- **Repo**: URL and hosting platform (GitHub, GitLab, self-hosted)
- **Hosting**: Where it runs (VPS, NAS, local, cloud), IP/hostname, SSH access
- **Deploy**: How to deploy (script, CI, manual steps)
- **Environments**: dev, staging, prod — addresses and how to access each
- **CI/CD**: Pipeline location and what it runs
- **Monitoring**: Dashboard URLs, alerting, log access
- **Credentials**: Where secrets are stored (not the secrets themselves — e.g.,
  "env vars in .env on prod", "1Password vault X")
- **Key URLs**: Web UI, API endpoints, admin panels — anything a session needs
- **Network**: Ports, firewalls, VPN requirements, DNS

**Carry-forward rule**: **ALWAYS carry forward. NEVER dissolve.** This section is
exempt from token budget pressure. Operational facts only change when
infrastructure changes — they never become "obvious from the code."

**On update**:
- New operational detail mentioned in transcript → add row
- Existing detail changed (e.g., server moved) → update row
- Service decommissioned → mark as "DECOMMISSIONED" with date, keep for 2
  narrative cycles, then remove

**Format**: Table for scanability:
```
| Item | Detail |
|------|--------|
| Repo | github.com/user/project |
| Hosting | thor NAS, 192.168.1.50, SSH as scott |
| Deploy | `git pull && docker compose up -d` on thor |
| Monitoring | Grafana at http://192.168.1.50:3000 |
```

**Validation**:
- [ ] If previous narrative had Operations entries, every entry is present in
      the updated narrative (or marked DECOMMISSIONED)
- [ ] No operational detail silently dropped
- [ ] Contains at least Repo if the project has one
- [ ] No secrets/passwords/tokens — only pointers to where they're stored

---

## Section 5: What We've Learnt

**Purpose**: Gotchas that could bite a future session.

**Required fields**:
- Bullet points, each a self-contained lesson
- Focus on things NOT obvious from the code

**Carry-forward rule**: Lessons persist until the fix is verified in code AND
the mistake can't be repeated. When dropping a lesson, briefly note why in
your internal reasoning (not in the narrative).

**On update**:
- New gotcha from transcript → add bullet
- Existing lesson now fixed in code → check if the mistake can recur. If not,
  drop. If it can (external system, API quirk), keep.
- Decision reversal from Approach → add lesson explaining why it was reversed

**Dissolve rule**: Drop lessons where the fix is in the code and can't recur.
Keep lessons about external systems, API quirks, domain rules, timing issues.

**Validation**:
- [ ] Every lesson from previous narrative is either: present, OR dropped with
      documented reasoning (in agent's thinking, not in the narrative)
- [ ] No lesson silently disappeared
- [ ] New gotchas from the transcript are captured

---

## Section 6: What We Want To Do

**Purpose**: User-stated goals. NOT Claude's suggestions.

**Required fields**:
- Bulleted list of goals the user has explicitly stated
- Each item attributed to the user (not inferred by Claude)

**Carry-forward rule**: Items persist until explicitly completed, abandoned, or
superseded by the user.

**On update — for EACH existing item**:
- Completed in this transcript → move to What's Done (update state description)
- Abandoned by user → remove, add brief note to What We've Learnt if there's
  a lesson
- Still pending → keep as-is
- Partially done → update with progress note

**On update — new items**:
- User states a new goal → add to list
- User implies a goal without stating it → do NOT add (put in Suggested Work)

**Validation**:
- [ ] Every item from previous narrative is accounted for: present, moved to
      What's Done, or explicitly dropped with reason
- [ ] No item silently disappeared
- [ ] No Claude-suggested items — those go in Suggested Work

---

## Section 7: Suggested Work

**Purpose**: Claude's recommendations the user hasn't asked for.

**Required fields**:
- Bulleted list of suggestions with brief rationale

**Carry-forward rule**: Suggestions persist for 3 narrative cycles. After that,
if the user hasn't acted on them, dissolve. If the user explicitly rejects a
suggestion, drop immediately.

**On update**:
- User acts on a suggestion → move to What We Want To Do or What's Done
- User rejects a suggestion → drop
- New recommendation from this transcript → add
- Stale suggestion (3+ cycles) → dissolve

**Validation**:
- [ ] No suggestion duplicates an item in What We Want To Do
- [ ] Suggestions are actionable, not vague

---

## Section 8: Resuming

**Purpose**: Where to pick up. The last piece of WORK, not the last session.

**Required fields**:
- **Status**: `complete` or `interrupted` (required, no other values)
- What was being worked on (2-3 sentences)
- If interrupted: where it stopped, what's left, any blockers
- If complete: what was finished

**Carry-forward rule**: REWRITE on every update. This section reflects only the
most recent work from the transcript being processed.

**On update**:
- If previous status was `interrupted`: check if the transcript resolves it.
  If yes, describe the resolution. If no (transcript is unrelated work),
  note that the interrupted work is still pending and describe the new work.
- Always rewrite to reflect the transcript's last piece of work

**Validation**:
- [ ] Status is exactly `complete` or `interrupted`
- [ ] If previous was `interrupted` and current transcript doesn't resolve it,
      the interrupted work is mentioned (not silently dropped)
- [ ] Section is ≤ 5 sentences

---

## Section 9: Source Transcripts

**Purpose**: Lookup table for finding decisions in raw transcripts.

**Required fields**:
- Recent table (last 5-10 transcripts): Date | File | What's In It
- Older: one-liner pointing to DB for full list

**Carry-forward rule**: Add new transcript. Dissolve oldest rows when content
is fully absorbed into standing sections.

**On update**:
- Add the current transcript as a new row
- Check oldest rows: if all their content is captured in standing sections,
  drop the row
- Keep the DB pointer line

**Validation**:
- [ ] Current transcript appears in the table
- [ ] Table has ≤ 10 rows (dissolve oldest if over)
- [ ] DB pointer line exists

---

## Cross-Section Diff Check (MANDATORY)

After generating the complete updated narrative, the agent MUST perform this
check before storing:

### Step 1: Extract all items from previous narrative
List every: decision, lesson, goal, operational detail, suggestion, file path.

### Step 2: Find each item in the updated narrative
For each item, confirm it is:
- **Present** in the same or different section, OR
- **Moved** to a more appropriate section (e.g., goal → What's Done), OR
- **Dissolved** with documented reason (lesson fixed in code, decision obvious), OR
- **Dropped** because the user explicitly abandoned/reversed it

### Step 3: Flag orphans
Any item that is NONE of the above is an **orphan**. Orphans indicate
information loss. The agent MUST either:
- Restore the item to the appropriate section, OR
- Document why it should be dropped (in the agent's reasoning, not the narrative)

**If any orphan cannot be justified, the narrative update MUST NOT proceed.**

---

## Token Budget

Enforced per section by `renderer.py:SECTION_TOKEN_BUDGETS` (~12,500 tokens
soft across the elastic sections, with a 1.5× overflow ceiling available to
high-scoring items only). Operations is exempt from dissolution pressure —
it's reference data, not prose.

The renderer trims by decay score automatically. The priority order below
still describes what *should* dissolve first, and is what the delta-extractor
and state-auditor grade against:

1. Drop Source Transcript rows for fully-dissolved sessions
2. Drop stale Suggested Work (3+ cycles)
3. Drop What We've Learnt items where fix is in code
4. Drop Approach decisions obvious from codebase
5. Compress What's Done prose
6. NEVER trim: The Idea, Operations, Resuming, What We Want To Do
