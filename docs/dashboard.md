# Web Dashboard

The dashboard is a read-only web interface for browsing and visualizing your memories. It never modifies the database.

## Starting the Dashboard

```bash
# Default: http://localhost:8765
llm-memory-dashboard

# Custom port
llm-memory-dashboard 9000
```

The `llm-memory-dashboard` command is a symlink installed to `~/.local/bin/` during setup. It runs the FastAPI server using uvicorn.

If the command is not found, ensure `~/.local/bin` is in your PATH, or run directly:

```bash
~/.claude/memory/lib/.venv/bin/python3 -m uvicorn dashboard:app --port 8765 \
  --app-dir ~/.claude/memory/lib/
```

## Views

### Timeline View

The default view. Shows all memories in reverse chronological order.

**Filters:**
- **Search** — full-text search across memory content
- **Type** — filter by narrative, note, or session_log
- **Project** — filter by project name

Each memory card shows:
- Type badge (color-coded)
- Project name
- Creation date
- Importance level
- Content preview
- Tags (if any)

### Graph View

A force-directed knowledge graph showing memories as nodes and connections as edges.

- **Node colors** indicate memory type (narrative, note, session_log)
- **Node size** scales with importance
- **Edges** show relationship type (supersedes, related_to)
- Click a node to view the full memory content
- Drag nodes to rearrange the layout

## Technical Details

- Built with FastAPI (Python)
- Templates use Jinja2 (`templates/dashboard.html`)
- Graph visualization uses D3.js (loaded from CDN)
- Database access is read-only (`?mode=ro` SQLite URI)
- CORS is enabled for local development

## Port Configuration

The default port is 8765. Pass a different port as the first argument:

```bash
llm-memory-dashboard 3000
```

If port 8765 is already in use, you will see an "Address already in use" error. Either kill the existing process or use a different port.
