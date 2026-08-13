# Comfy Project Instructions

## GavLife Task State

For substantive Comfy project work, use GavLife as the source of task state.

- GavLife database: `/home/gavman/Documents/_ops/data/kanban.sqlite3`
- GavLife CLI: `/home/gavman/Documents/_ops/scripts/kanban.py`
- GavLife browser app: `http://127.0.0.1:8787`
- Before starting work, search for an existing relevant card and use it when one exists.
- During work, update the relevant card when scope, status, blockers, PRs, decisions, review items, or next actions change.
- Before finishing, make sure actionable follow-up is captured in GavLife.
- Prefer updating existing Comfy cards over creating duplicates.
- Use `project: ComfyUI` for Comfy cards unless Gavin specifies a different project area.
- Do not use Linear or Trello unless Gavin explicitly re-enables those connectors first.

Useful commands:

```bash
cd /home/gavman/Documents/_ops
python3 scripts/kanban.py search "Comfy query"
python3 scripts/kanban.py list --current
python3 scripts/kanban.py update CARD --list "In Progress"
python3 scripts/kanban.py comment CARD "Work note, decision, blocker, or next action."
```

## Repository Data Boundaries

Do not commit large Comfy runtime data or generated media by default.

- Keep `basedir/models/`, `basedir/input/`, and `basedir/output/` out of Git unless Gavin explicitly asks otherwise.
- Keep `spider/run/` and `spider/custom_nodes/` out of Git; preserve reproducibility through compose files, startup scripts, docs, and small workflow JSONs.
- Track reusable workflows and helper scripts only when they are small and useful for recreating work.
