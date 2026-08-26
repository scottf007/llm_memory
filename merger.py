"""
Merger — applies a delta JSON (from the delta-extractor agent) into a
project state JSON, assigning stable IDs and updating ledger statuses.

Usage:
    python merger.py [--items-root PATH] <project_json_path> <delta_json_path>

Mutates the project JSON in place. Idempotent per delta file (won't
re-append if the session_id is already present).

The per-item files and the FTS index are derived from where the state JSON
lives, not hardcoded: a state file inside ~/.claude/memory/projects/ uses the
real ~/.claude/memory/items/ + memory.db; a state file anywhere else is
treated as a sandbox and fans out into a sibling items/ directory with its own
memory.db, so dry-running a copy can never mutate the real memory tree.
"""

import errno
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from lib import archive_class
from lib import cascade
from tools.memory_config import memory_root


LEDGER_KEYS = ("decisions", "goals", "suggestions", "learnings", "done")
ID_PREFIXES = {
    "decisions": "dec",
    "goals": "goal",
    "suggestions": "sug",
    "learnings": "lrn",
    "done": "work",
}

ITEMS_ROOT = Path.home() / ".claude" / "memory" / "items"


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.expanduser().resolve() == b.expanduser().resolve()
    except OSError:
        return False


def resolve_paths(project_path: Path,
                  items_root_override: Path | None = None) -> tuple[Path, Path, bool]:
    """Work out where per-item files and the FTS index belong for a state file.

    Returns (items_root, db_path, sandboxed).

    - State file inside ~/.claude/memory/projects/ → the real
      ~/.claude/memory/items/ and ~/.claude/memory/memory.db. This is the
      production pipeline path and its behaviour is unchanged.
    - Anywhere else → a sibling `items/` directory next to the state file plus
      a `memory.db` alongside it, so merging a scratch copy of a project (even
      one kept under its real name) cannot touch the real memory tree.
    - An explicit items_root_override always wins; the DB always sits next to
      the items root, so pointing the override at the canonical items root
      reproduces canonical behaviour exactly.
    """
    root = memory_root()
    canonical_items = root / "items"

    if items_root_override is not None:
        items_root = Path(items_root_override).expanduser()
    elif _same_path(Path(project_path).expanduser().parent, root / "projects"):
        items_root = canonical_items
    else:
        items_root = Path(project_path).expanduser().parent / "items"

    db_path = items_root.parent / "memory.db"
    return items_root, db_path, not _same_path(items_root, canonical_items)


def _new_id(kind: str) -> str:
    """Mint a UUID-suffix item id, e.g. 'dec-a8c3b4f2'."""
    return f"{ID_PREFIXES[kind]}-{secrets.token_hex(4)}"


def _session_ts(state: dict, session_id: str) -> str | None:
    """Return the ended/started timestamp for a session_id, or None."""
    for s in state.get("sessions", []):
        if s.get("session_id") == session_id:
            return s.get("ended") or s.get("started")
    return None


def _backfill_last_touched_at(state: dict) -> None:
    """Populate last_touched_at on any item missing it, using
    last_touched_in → introduced_in → state.last_updated as fallback chain.
    Idempotent — a no-op after the first run."""
    fallback = state.get("last_updated")
    for kind in LEDGER_KEYS:
        for item in state.get(kind, []):
            if item.get("last_touched_at"):
                continue
            ts = (
                _session_ts(state, item.get("last_touched_in", ""))
                or _session_ts(state, item.get("introduced_in", ""))
                or fallback
            )
            if ts:
                item["last_touched_at"] = ts


def _archive_item(state: dict, kind: str, item_id: str, session_id: str, ts: str, reason: str) -> None:
    for item in state.get(kind, []):
        if item.get("id") == item_id:
            item["status"] = "archived"
            item["archived_in"] = session_id
            item["archived_reason"] = reason
            # M1 (§8.2): computed forward at every archive event. Set
            # unconditionally — no guard against an already-set value.
            # Callers that need a value the leading-clause parser cannot
            # produce ("cascade", "lifecycle" — call-site overrides by
            # contract, §4) overwrite it immediately after this returns.
            item["archive_class"] = archive_class.classify_archive_reason(reason)
            item["last_touched_in"] = session_id
            item["last_touched_at"] = ts
            return


LINK_RELATION = "implements_current_claim"


def _link_identity(link: dict) -> tuple:
    """Replication identity of a decision_links entry (F-1).

    `scope` is deliberately NOT part of the key. It is the one field a
    legitimate local operation mutates on an existing entry — C5-a's
    in-place promotion of a confirmed U1_PARTIAL review from "partial" to
    "whole" — so keying on it made the same edge unrecognisable across
    machines. Identity is what the edge is ABOUT (which parent, which
    relation); scope is a claim about it, reconciled by _reconcile_link.
    """
    return (link.get("decision_id"), link.get("relation"))


def _reconcile_link(current: dict, incoming: dict) -> None:
    """Fold `incoming` into `current` for one (decision_id, relation) pair.

    "whole" supersedes "partial" and is ABSORBING: once either side has
    established that a claim wholly restates its parent, no later merge can
    walk that back. That is what makes the union order-independent — it is
    commutative and idempotent, so A->B->A and B->A->B converge on the same
    single edge regardless of replication order.

    A same-scope incoming carries no new information: the pre-F-1 key
    already treated it as an exact duplicate (the key excludes
    evidence_source by design, so two same-scope entries are duplicates by
    construction, never a disagreement to arbitrate). It is ignored, which
    keeps a quiet resync genuinely quiet.

    Mutates `current` in place and returns nothing on purpose. The caller
    detects change by comparing the rebuilt list against the original, which
    also catches a collapse that drops a duplicate WITHOUT promoting
    anything — a "did I promote?" boolean would silently miss that and
    report no update on a merge that really did repair the record.
    """
    if incoming.get("scope") != "whole" or current.get("scope") == "whole":
        return
    current["scope"] = "whole"
    # The promoting side's provenance travels with the promotion — the
    # review that established "whole" is the authoritative record of it.
    for field in ("evidence_source", "written_in", "proposed_test"):
        if field in incoming:
            current[field] = incoming[field]


def inbox_merge(state: dict, project: str, items_root: Path = ITEMS_ROOT) -> int:
    """Reconcile incoming per-item file changes into state.

    Walks ~/.claude/memory/items/{project}/{kind}/*.json and for each file:
      - If the item isn't in state[kind], append it.
      - If the item exists and the file's status is archived and state's
        isn't, prefer archived (archive supersedes regardless of timestamp).
      - Else if the file's last_touched_at is newer, replace
        text/rationale/quote/status/importance/last_touched_at fields.
      - Regardless of which branch above fired (or neither): harmonize
        archive_class (disposition C1) and union decision_links
        (disposition #24) — neither is a status/timestamp field, so both
        run unconditionally rather than being gated by the archived-wins/
        newer-wins outcome (VERDICT §2.2: an equal- or older-timestamp
        incoming item with additive evidence must still reach these two
        checks).

    Leaves state["sessions"], state["summary"], state["operations"] alone —
    those are written only via deltas.

    Returns the number of items added or updated.
    """
    proj_dir = items_root / project
    if not proj_dir.exists():
        return 0

    updates = 0
    for kind in LEDGER_KEYS:
        kind_dir = proj_dir / kind
        if not kind_dir.exists():
            continue
        existing = {item.get("id"): item for item in state.setdefault(kind, [])}
        for item_file in kind_dir.glob("*.json"):
            try:
                incoming = json.loads(item_file.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            iid = incoming.get("id")
            if not iid:
                continue
            current = existing.get(iid)
            if current is None:
                state[kind].append({k: v for k, v in incoming.items()
                                    if k not in ("kind", "project")})
                updates += 1
                continue
            changed = False
            # Archived-wins rule (either direction).
            cur_archived = current.get("status") == "archived"
            inc_archived = incoming.get("status") == "archived"
            if inc_archived and not cur_archived:
                current["status"] = "archived"
                current["archived_in"] = incoming.get("archived_in") or current.get("archived_in")
                current["archived_reason"] = incoming.get("archived_reason") or current.get("archived_reason")
                current["last_touched_at"] = incoming.get("last_touched_at") or current.get("last_touched_at")
                changed = True
            else:
                # Newer-wins on text fields.
                inc_ts = incoming.get("last_touched_at") or ""
                cur_ts = current.get("last_touched_at") or ""
                if inc_ts and inc_ts > cur_ts:
                    for field in ("text", "rationale", "quote", "importance",
                                  "last_touched_in", "last_touched_at"):
                        if field in incoming:
                            current[field] = incoming[field]
                    changed = True

            # --- disposition C1 — runs for every item pair, regardless of
            # which branch above fired. archive_class is never blindly
            # imported: "regrade"/"unclassified" ARE classify_archive_
            # reason's own parser outputs and are always safe to recompute
            # from the merged archived_reason text; "cascade"/"lifecycle"
            # are call-site overrides the parser cannot reconstruct (§4
            # returns "unclassified" for all three generated-reason shapes)
            # and must be preserved whenever either side already carries
            # one. The two override values cannot legitimately conflict
            # between peers for the same item (a "cascade" child and a
            # "lifecycle" closure are structurally different code paths on
            # different kinds), so incoming winning when it disagrees with a
            # non-override current value is safe; an incoming non-override
            # value never overwrites a current override.
            if current.get("status") == "archived" or incoming.get("status") == "archived":
                reason = current.get("archived_reason") or incoming.get("archived_reason")
                cur_cls = current.get("archive_class")
                inc_cls = incoming.get("archive_class")
                if inc_cls in ("cascade", "lifecycle"):
                    if cur_cls != inc_cls:
                        current["archive_class"] = inc_cls
                        changed = True
                elif cur_cls in ("cascade", "lifecycle"):
                    pass  # preserve the local call-site override
                else:
                    recomputed = archive_class.classify_archive_reason(reason)
                    if cur_cls != recomputed:
                        current["archive_class"] = recomputed
                        changed = True

            # decision_links unions by (decision_id, relation) identity,
            # RECONCILING scope (F-1) — genuinely additive evidence, not
            # derivable from anything else (disposition #24). A stale
            # incoming file carrying no decision_links removes nothing.
            #
            # F-1: scope was previously part of the identity key. But a C5-a
            # promotion MUTATES scope on an existing entry, so a promoted
            # edge arriving from another machine looked like a key the
            # receiver had never seen and was APPENDED beside the stale
            # partial it was meant to replace — leaving the record saying
            # both "partial restatement" and "whole restatement" for the same
            # (child, parent), permanently. Worse, `cascade._write_decision_
            # link` then resolved the pair by first match, promoting the
            # stale one too and minting the duplicate whole entry C5-a exists
            # to prevent. scope is reconciled instead: see _reconcile_link.
            cur_links = current.get("decision_links") or []
            inc_links = incoming.get("decision_links") or []
            if cur_links or inc_links:
                merged, order = {}, []
                for link in cur_links:
                    key = _link_identity(link)
                    if key in merged:
                        # A contradictory pair left behind by the pre-F-1
                        # union. Collapse it, so the invariant is
                        # unconditional rather than true-going-forward.
                        _reconcile_link(merged[key], link)
                        continue
                    merged[key] = dict(link)
                    order.append(key)
                for link in inc_links:
                    if (not link.get("decision_id")
                            or link.get("relation") != LINK_RELATION):
                        continue  # validate enum/target before importing —
                                  # never trust a peer blindly
                    key = _link_identity(link)
                    if key in merged:
                        _reconcile_link(merged[key], link)
                    else:
                        merged[key] = dict(link)
                        order.append(key)
                new_links = [merged[k] for k in order]
                if new_links != cur_links:
                    current["decision_links"] = new_links
                    changed = True
            # ------------------------------------------------------------------

            if changed:
                updates += 1
    return updates


def _norm_text(value: str | None) -> str:
    """Whitespace/case-normalised item text, for cross-run identity."""
    return " ".join((value or "").split()).casefold()


def apply_delta(state: dict, delta: dict, rerun: bool = False) -> dict:
    session_id = delta["session_id"]
    session_ts = delta.get("ended") or delta.get("started") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Idempotency — skip if already merged. A session that kept running after
    # its first merge is the exception: re-extracting it produces a delta
    # covering the whole session, and `rerun` lets that supersede the first
    # pass instead of being silently discarded.
    already_merged = any(s.get("session_id") == session_id for s in state.get("sessions", []))
    if already_merged and not rerun:
        return state

    # Backfill last_touched_at on any existing items missing it (migration).
    _backfill_last_touched_at(state)

    # Apply summary_delta — shallow merge into state.summary.
    # Present keys overwrite; absent keys preserve existing values.
    summary_delta = delta.get("summary_delta") or {}
    if summary_delta:
        state.setdefault("summary", {})
        for k, v in summary_delta.items():
            if v:  # only overwrite if non-empty
                state["summary"][k] = v

    # Apply operations_delta — upsert by item name.
    # If an item already exists, update its detail. If not, append.
    ops_delta = delta.get("operations_delta") or []
    if ops_delta:
        existing_ops = state.setdefault("operations", [])
        existing_by_item = {row.get("item"): row for row in existing_ops if isinstance(row, dict)}
        for new_row in ops_delta:
            if not isinstance(new_row, dict):
                continue
            item = new_row.get("item")
            if not item:
                continue
            if item in existing_by_item:
                existing_by_item[item]["detail"] = new_row.get("detail", existing_by_item[item].get("detail", ""))
            else:
                existing_ops.append(dict(new_row))

    # Introduced items — mint new IDs, append.
    introduced = delta.get("ledger_delta", {}).get("introduced", {}) or {}
    intro_ids = {k: [] for k in LEDGER_KEYS}
    # On a rerun the extractor re-reads the whole session, so it re-emits the
    # items the first pass already merged. IDs are minted here rather than by
    # the extractor, so cross-run identity has to fall back to item text.
    seen_text: dict[str, set[str]] = {}
    if already_merged:
        for kind in LEDGER_KEYS:
            seen_text[kind] = {_norm_text(i.get("text")) for i in state.get(kind, [])}
    for kind in LEDGER_KEYS:
        state.setdefault(kind, [])
        for new_item in introduced.get(kind, []) or []:
            if already_merged:
                key = _norm_text(new_item.get("text"))
                if key and key in seen_text[kind]:
                    continue
                seen_text[kind].add(key)
            item = dict(new_item)
            item["id"] = _new_id(kind)
            item["status"] = "active"
            item["introduced_in"] = session_id
            item["last_touched_in"] = session_id
            item["last_touched_at"] = session_ts
            item["archived_in"] = None
            item["archived_reason"] = None
            state[kind].append(item)
            intro_ids[kind].append(item["id"])

    # M2 referential integrity (§8.1, §12). A NEWLY-INTRODUCED `done` item
    # whose decision_links target is absent from state after introduction
    # rejects the WHOLE delta, before any write — a dangling edge is worse
    # than a missing one, because certification would read it as evidence
    # that a claim is still implemented. Checked after introduction, not
    # before, so an edge may legitimately point at a decision introduced by
    # this same delta. Tolerant reader (step 4): a legacy delta with no
    # decision_links field merges normally, and pre-existing dangling
    # references already in state are not retroactively rejected — only
    # what this delta newly asserts.
    new_done_ids = set(intro_ids.get("done", []))
    if new_done_ids:
        known_ids = {item.get("id")
                     for k in LEDGER_KEYS for item in state.get(k, [])}
        for item in state.get("done", []):
            if item.get("id") not in new_done_ids:
                continue
            for link in item.get("decision_links") or []:
                target = link.get("decision_id")
                if target not in known_ids:
                    raise ValueError(
                        f"merger: delta {session_id!r} introduces done item "
                        f"{item.get('id')!r} with a decision_links reference to "
                        f"{target!r}, which is not present in state after "
                        f"introduction — rejecting the whole delta")

    # Resolutions — update existing items. Falls back to text-matching
    # against just-introduced items when the agent referenced a synthetic
    # id (e.g., same-session rejection of a newly-introduced suggestion).
    resolutions = delta.get("ledger_delta", {}).get("resolutions", {}) or {}
    res_summary = {"closed": [], "archived": [], "rejected": [], "contradictions": [],
                   "drift": [], "revalued": [],
                   # §8.1, disposition #17 extended per C4 — additive, default [].
                   # `cascade_invalidated` is what keeps a fingerprint-drifted
                   # review in the session audit trail instead of vanishing.
                   "cascade_confirm": [], "cascade_reject": [], "cascaded": [],
                   "cascade_invalidated": []}

    def _resolve_id(item_id: str, text_hint: str, kinds_to_search: tuple) -> str | None:
        """Return a real item id. If item_id already matches a prefix/number
        pattern and exists, use it. Otherwise text-match against items just
        introduced this session."""
        if not item_id:
            item_id = ""
        # Direct id lookup first.
        for kind in kinds_to_search:
            for item in state.get(kind, []):
                if item.get("id") == item_id:
                    return item_id
        # Fallback: text-match against just-introduced items of these kinds.
        for kind in kinds_to_search:
            for new_id in intro_ids.get(kind, []):
                for item in state.get(kind, []):
                    if item.get("id") == new_id:
                        if text_hint and text_hint.lower() in item.get("text", "").lower():
                            return new_id
                        # Or synthetic id that contains part of the text:
                        if item_id and any(tok in item.get("text", "").lower() for tok in item_id.lower().split("-") if len(tok) > 3):
                            return new_id
        return None

    for closed in resolutions.get("closed", []) or []:
        resolved = _resolve_id(closed.get("id", ""), closed.get("text", ""), ("goals", "suggestions"))
        if resolved:
            for kind in ("goals", "suggestions"):
                for item in state.get(kind, []):
                    if item.get("id") == resolved:
                        item["status"] = "archived"
                        # §8.2, disposition #3 — call-site override. This
                        # loop archives inline and never calls
                        # _archive_item, so without this line a goal/
                        # suggestion closure is the one archive path in the
                        # system that never gets an archive_class. The
                        # parser cannot supply it: a "closed: ..." reason
                        # carries no cascade/regrade vocabulary term and
                        # would fall through to "unclassified", which is
                        # reserved for decisions the parser couldn't read —
                        # not for another kind's own correct lifecycle
                        # prefix.
                        item["archive_class"] = "lifecycle"
                        item["archived_in"] = session_id
                        item["archived_reason"] = f"closed: {closed.get('evidence', '')}"
                        item["last_touched_in"] = session_id
                        item["last_touched_at"] = session_ts
                        res_summary["closed"].append(resolved)

    for archived in resolutions.get("archived", []) or []:
        resolved = _resolve_id(archived.get("id", ""), archived.get("text", ""), LEDGER_KEYS)
        if resolved:
            for kind in LEDGER_KEYS:
                _archive_item(state, kind, resolved, session_id, session_ts, archived.get("reason", ""))
            res_summary["archived"].append(resolved)

    for rejected in resolutions.get("rejected", []) or []:
        resolved = _resolve_id(rejected.get("id", ""), rejected.get("text", ""), ("suggestions", "goals"))
        if resolved:
            for kind in ("suggestions", "goals"):
                _archive_item(state, kind, resolved, session_id, session_ts, f"rejected: {rejected.get('reason', '')}")
                # §8.2, disposition #3 — call-site override, same rule as
                # the `closed` loop above. Written after _archive_item
                # rather than instead of it (the §7.1 post-hoc-overwrite
                # shape): a "rejected: ..." reason has no vocabulary term,
                # so the value _archive_item just computed is
                # "unclassified" and must be replaced.
                for item in state.get(kind, []):
                    if item.get("id") == resolved:
                        item["archive_class"] = "lifecycle"
            res_summary["rejected"].append(resolved)

    for contradiction in resolutions.get("contradictions", []) or []:
        resolved = _resolve_id(contradiction.get("id", ""), contradiction.get("text", ""), LEDGER_KEYS)
        if resolved:
            for kind in LEDGER_KEYS:
                _archive_item(state, kind, resolved, session_id, session_ts, f"contradicted by new decision: {contradiction.get('by_decision_text', '')}")
            res_summary["contradictions"].append(resolved)

    for drift in resolutions.get("drift", []) or []:
        note = drift.get("note", "") if isinstance(drift, dict) else str(drift)
        res_summary["drift"].append(note)

    # --- review resolution, then cascade (§8.1) ---------------------------
    # Order is load-bearing: resolution writes the edge, cascade.apply's step 1
    # sees it and archives in this same merge, and only then may revaluations
    # run — so a re-grade never lands on an item whose cascade outcome has not
    # been applied yet. M1's backfill goes first because cascade cannot build
    # its parent pool without archive_class; it is idempotent, so calling it on
    # every merge is safe.
    archive_class.backfill(state)
    review_result = cascade.apply_review_resolutions(
        state, delta.get("resolutions", {}) or {}, session_id, session_ts)
    res_summary["cascade_confirm"] = review_result["confirmed"]
    res_summary["cascade_reject"] = review_result["rejected"]
    res_summary["cascade_invalidated"] = review_result["invalidated"]
    cascade_result = cascade.apply(state, session_id, session_ts)
    res_summary["cascaded"] = [a["child"] for a in cascade_result["archived"]]
    # ----------------------------------------------------------------------

    # Re-valuations — re-grade existing items the renderer flagged as
    # contested (near the budget cut line). Deliberately does NOT touch
    # last_touched_at: re-grading is not activity on the item, and treating it
    # as such would hand every contested item a recency boost, so it would
    # survive the next cut and never be reconsidered again.
    for reval in delta.get("ledger_delta", {}).get("revaluations", []) or []:
        item_id = reval.get("id")
        if not item_id:
            continue
        for kind in LEDGER_KEYS:
            for item in state.get(kind, []):
                if item.get("id") != item_id:
                    continue
                if isinstance(reval.get("value"), (int, float)) and not isinstance(reval["value"], bool):
                    item["value"] = min(1.0, max(0.0, float(reval["value"])))
                if reval.get("importance") in ("load_bearing", "standard", "minor"):
                    item["importance"] = reval["importance"]
                item["revalued_in"] = session_id
                # Wall-clock, deliberately not `last_touched_at` (see above).
                # Several sessions in one sweep can re-grade the same contested
                # item; this records which pass actually settled it.
                item["revalued_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                res_summary["revalued"].append(item_id)

    # Append session record.
    session_record = {
        "session_id": session_id,
        "started": delta.get("started"),
        "ended": delta.get("ended"),
        "topic": delta.get("topic", ""),
        "jsonl": f"~/.claude/memory/transcripts/{session_id}.jsonl",
        "conversation_md": f"~/.claude/memory/conversations/{session_id}.md",
        "status": "active",
        "closure_status": delta.get("closure_status"),
        "journal": delta.get("journal", ""),
        "resume_excerpt_lines": delta.get("resume_excerpt_lines", 0),
        "ledger_delta_applied": {
            "introduced": intro_ids,
            "resolutions": res_summary,
        },
    }
    existing_record = next(
        (s for s in state.get("sessions", []) if s.get("session_id") == session_id), None)
    if existing_record is None:
        state.setdefault("sessions", []).append(session_record)
    else:
        # Refresh the watermark `narrative_coverage` compares against, so a
        # re-extracted session stops reporting as stale, and keep the newest
        # journal/topic. Prior passes are retained under `reruns`.
        for field in ("ended", "closure_status", "resume_excerpt_lines"):
            existing_record[field] = session_record[field]
        for field in ("topic", "journal"):
            if session_record[field]:
                existing_record[field] = session_record[field]
        existing_record.setdefault("reruns", []).append({
            "merged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ledger_delta_applied": session_record["ledger_delta_applied"],
        })

    # Clear resume_excerpt on older sessions (only newest active session keeps it).
    for s in state["sessions"][:-1]:
        if "resume_excerpt" in s:
            s.pop("resume_excerpt", None)

    state["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return state


def _atomic_write_json(path: Path, value: dict) -> None:
    """Write `value` to `path` atomically (§8.4).

    Crash windows, deliberate and unchanged from the design:
      - Before os.replace: nothing durable; a re-run is idempotent by
        session_id.
      - After os.replace, before fan_out_items: the state JSON is new and
        item files may be stale. This is ACCEPTABLE — inbox_merge's
        archived-wins rule is monotone and its newer-wins field whitelist
        excludes `status`, so a stale active item file can never resurrect
        an archived row, and the next merge's fan-out/index rebuild
        repairs the drift. The fan-out is deliberately NOT transactional.
      - After fan-out, before the FTS rebuild: the index is derived; the
        next merge rebuilds it.
    """
    data = json.dumps(value, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    tmp_path = Path(tmp_name)
    try:
        if path.exists():
            os.chmod(tmp_path, path.stat().st_mode & 0o777)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        try:
            dirfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        except OSError as e:
            # §8.4 AMENDED. These constants live in `errno`, not `os`; read
            # off `os` every getattr returned None, the tuple resolved to
            # (None, None, None) and EVERY OSError re-raised — the inverse of
            # the guard's intent. On a filesystem with no directory-fsync
            # support that reported an ALREADY-SUCCEEDED os.replace as a
            # failed merge. The guard stays narrow: a genuine I/O error is
            # still an error.
            if e.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                raise
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def fan_out_items(state: dict, project: str, items_root: Path = ITEMS_ROOT) -> int:
    """Write every ledger item to ~/.claude/memory/items/{project}/{kind}/{id}.json.

    Idempotent — each write overwrites the file in place. Returns the count
    of files written. Archived items are fanned out too (their status field
    carries the archive state).
    """
    proj_dir = items_root / project
    proj_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for kind in LEDGER_KEYS:
        kind_dir = proj_dir / kind
        kind_dir.mkdir(exist_ok=True)
        for item in state.get(kind, []) or []:
            item_id = item.get("id")
            if not item_id:
                continue
            payload = dict(item)
            payload["kind"] = kind
            payload["project"] = project
            (kind_dir / f"{item_id}.json").write_text(json.dumps(payload, indent=2))
            written += 1
    return written


def _rebuild_items_index(db_path: Path, items_root: Path | None = None) -> None:
    """Invoke the indexer to refresh the FTS5 table. Best-effort — if the
    indexer isn't available (tests, broken install), skip silently.

    items_root must be passed alongside db_path: indexing defaults to the real
    items tree, so a sandbox DB built without it would be populated from the
    user's production items.
    """
    try:
        import indexer
    except ImportError:
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            import indexer
        except ImportError:
            return
    try:
        indexer.rebuild_items_index(items_root=items_root, db_path=db_path)
    except Exception:
        pass


USAGE = "Usage: merger.py [--items-root PATH] <project_json_path> <delta_json_path>"


def _parse_args(argv: list[str]) -> tuple[Path, Path, Path | None, bool]:
    """Positional CLI, unchanged, plus --items-root and --rerun."""
    items_root_override: Path | None = None
    rerun = False
    positional: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--rerun":
            rerun = True
            i += 1
            continue
        if arg == "--items-root":
            if i + 1 >= len(argv):
                print("merger.py: --items-root needs a PATH", file=sys.stderr)
                print(USAGE, file=sys.stderr)
                sys.exit(1)
            items_root_override = Path(argv[i + 1])
            i += 2
            continue
        if arg.startswith("--items-root="):
            items_root_override = Path(arg.split("=", 1)[1])
            i += 1
            continue
        positional.append(arg)
        i += 1

    if len(positional) != 2:
        print(USAGE, file=sys.stderr)
        sys.exit(1)

    return Path(positional[0]), Path(positional[1]), items_root_override, rerun


def main(argv: list[str] | None = None) -> None:
    project_path, delta_path, items_root_override, rerun = _parse_args(
        list(sys.argv[1:] if argv is None else argv))

    items_root, db_path, sandboxed = resolve_paths(project_path, items_root_override)
    if sandboxed:
        print(f"merger.py: sandbox mode — {project_path} is outside "
              f"{memory_root() / 'projects'}; items → {items_root}, "
              f"index → {db_path} (real memory tree untouched)", file=sys.stderr)

    state = json.loads(project_path.read_text()) if project_path.exists() else {}
    delta = json.loads(delta_path.read_text())

    project = project_path.stem
    if state.get("project") and state["project"] != project:
        print(f"merger.py: warning — state['project'] is {state['project']!r} but "
              f"the filename says {project!r}; using {project!r}", file=sys.stderr)

    # Reconcile any per-item file changes that Syncthing brought in before
    # applying this session's delta, so the delta merges against the newest
    # view of the ledger (not just what the local JSON last knew).
    inbox = inbox_merge(state, project, items_root)

    session_id = delta["session_id"]
    already_merged = any(
        s.get("session_id") == session_id for s in state.get("sessions", []))
    skipped = already_merged and not rerun

    state = apply_delta(state, delta, rerun=rerun)
    state["last_rebuilt_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _atomic_write_json(project_path, state)

    fanned = fan_out_items(state, project, items_root)
    _rebuild_items_index(db_path, items_root)

    if skipped:
        # The fan-out below still runs and still rewrites the file, so this
        # path used to print an unqualified "Merged ..." for what was a no-op.
        print(f"merger.py: {session_id} is already in sessions[] — delta NOT "
              f"applied. Re-run with --rerun if the session grew after its "
              f"first merge.", file=sys.stderr)
        print(f"Skipped {session_id} (already merged); rebuilt {project_path} "
              f"({inbox} inbox, {fanned} fanned out)")
        return

    print(f"{'Re-merged' if already_merged else 'Merged'} {session_id} into "
          f"{project_path} ({inbox} inbox, {fanned} fanned out)")


if __name__ == "__main__":
    main()
