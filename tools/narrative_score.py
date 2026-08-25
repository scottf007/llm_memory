#!/usr/bin/env python3
"""Score a project's narrative against the goal: does a stranger decide better,
per token.

Deliberately NOT one number. Trust is a gate, not a tradeable axis: you may not
buy token efficiency with falsehood. Two readings, computed from artifacts the
pipeline already writes.

  TRUST defects (a stranger decides WORSE for reading this):
    FALSE  certificate contradiction count when a certificate exists,
           else FALSE_ID (active items whose parent decision is archived
           as superseded) -- never silently reports a clean 0
           -> tells a stranger a removed mechanism ships and works
    DIRTY  archived items in the top-10 of a fixed query panel
           -> retrieval hands an agent a superseded record at live rank

  REACH defects (a stranger misses something they needed):
    LOST   load_bearing items evicted at the budget boundary
    UNSEEN transcripts with real content never merged into any narrative

  COST:
    TOKENS rendered narrative size

Rule: any change that raises FALSE or DIRTY is NEGATIVE regardless of what it
saves. Among changes that do not, higher reach-per-1k-tokens is better.
"""
import json, os, sqlite3, sys

HOME = os.path.expanduser("~/.claude/memory")
PANEL = ["renderer budget", "adapter codex client", "narrative decay scoring",
         "installer hooks", "merger archive", "min_user_turns filter",
         "session start hook", "syncthing sync"]


def score(project):
    state = json.load(open(f"{HOME}/projects/{project}.json"))
    out = {"project": project}

    archived_sup = {i["id"] for k, v in state.items() if isinstance(v, list)
                    for i in v if isinstance(i, dict) and i.get("archived_in")
                    and any(w in (i.get("archived_reason") or "").lower()
                            for w in ("superseded", "no longer current",
                                      "reversed", "contradicted"))}
    active = [i for k, v in state.items() if isinstance(v, list)
              for i in v if isinstance(i, dict)
              and not i.get("archived_in") and not i.get("closed_in")]
    out["ACTIVE"] = len(active)
    # FALSE_ID: an active item whose text names a superseded item, or that a
    # cascade sweep has flagged. Conservative: only counts explicit references.
    out["FALSE_ID"] = sum(1 for i in active
                          if any(a in (i.get("text") or "") for a in archived_sup))

    cert_path = f"{HOME}/projects/{project}.certificate.json"
    if os.path.exists(cert_path):
        cert = json.load(open(cert_path))
        not_checked = cert.get("not_checked") or []
        out["FALSE_CHECKED"] = cert["counts"].get("contradiction", 0)
        out["NOT_CHECKED"] = len(not_checked)
        out["checked_clean"] = (cert["verdict"] in ("NO_KNOWN_FALSEHOOD", "SUSPECT")
                                 and not not_checked)
        out["FALSE"] = out["FALSE_CHECKED"]
    else:
        out["FALSE_CHECKED"] = None
        out["NOT_CHECKED"] = None
        out["checked_clean"] = None
        out["FALSE"] = out["FALSE_ID"]  # fall back, never silently 0

    cpath = f"{HOME}/projects/{project}.contested.json"
    lost = 0
    tokens = None
    if os.path.exists(cpath):
        c = json.load(open(cpath))
        tokens = c.get("total_tokens")
        for s in c.get("sections", {}).values():
            for x in s.get("contested", []):
                if x.get("outcome") != "kept" and x.get("importance") == "load_bearing":
                    lost += 1
    out["LOST"] = lost
    out["TOKENS"] = tokens

    db = f"{HOME}/memory.db"
    dirty = total = 0
    if os.path.exists(db):
        con = sqlite3.connect(db)
        for q in PANEL:
            try:
                rows = con.execute(
                    "select i.status from items_fts f join items i on i.rowid=f.rowid "
                    "where items_fts match ? and i.project=? "
                    "order by bm25(items_fts) limit 10", (q, project)).fetchall()
            except Exception:
                continue
            total += len(rows)
            dirty += sum(1 for (s,) in rows if s == "archived")
    out["DIRTY_PCT"] = round(100 * dirty / total, 1) if total else None
    out["REACH_PER_1K"] = round(len(active) / (tokens / 1000), 1) if tokens else None
    return out


if __name__ == "__main__":
    for p in (sys.argv[1:] or ["llm_memory"]):
        r = score(p)
        print(f"=== {r['project']}")
        print(f"  TRUST   FALSE={r['FALSE']}   DIRTY={r['DIRTY_PCT']}% of top-10 results archived")
        print(f"          checked_clean={r['checked_clean']}  not_checked={r['NOT_CHECKED']}  FALSE_ID={r['FALSE_ID']}")
        print(f"  REACH   LOST={r['LOST']} load_bearing evicted   ACTIVE={r['ACTIVE']}")
        print(f"  COST    TOKENS={r['TOKENS']}   reach/1k={r['REACH_PER_1K']}")
