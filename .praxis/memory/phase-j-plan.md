# Phase J Plan — Unattended Operation Infrastructure

**Date:** 2026-05-25

---

## J-1: Task Queue

### Format: `.praxis/queue/tasks.jsonl`

Each line is a JSON object:
```json
{"id": "uuid4", "prompt": "do X", "priority": 1, "status": "pending", "created_at": "2026-05-25T03:00:00Z", "started_at": null, "completed_at": null, "result": null, "error": null, "stages": null}
```

Fields:
- `id` — UUID4, unique task identifier
- `prompt` — the user's instruction
- `priority` — integer, lower = higher priority (default 0)
- `status` — one of: pending, running, done, failed
- `created_at` — ISO8601 timestamp
- `started_at`, `completed_at` — timestamps for tracking
- `result` — output string on success
- `error` — error message on failure
- `stages` — optional list of sub-prompts for J-2 checkpointing

### Polling Design

- Poll interval: 2 seconds (configurable via `PRAXIS_QUEUE_POLL_INTERVAL` env var)
- Selection: lowest priority number first, then oldest `created_at`
- Crash safety: on queue startup, any tasks stuck in "running" status are marked "failed" with error "interrupted — process exited before completion"
- Atomic updates: rewrite entire file on each status change (file is small by design)

### Result Delivery

- `result` field in the task line itself
- Human-friendly: write `.praxis/queue/results/{task-id}.txt` with the full output

---

## J-2: Session Continuity (Checkpoints)

### Checkpoint file: `.praxis/queue/checkpoints/{task-id}.json`

```json
{"task_id": "uuid", "stages": ["stage1", "stage2", ...], "completed": [0, 1], "results": {"0": "...", "1": "..."}, "last_updated": "ISO8601"}
```

### How it works

- If a task has `stages` (list of sub-prompts), the queue runner executes each as a separate `orch.run()` call
- After each stage completes, checkpoint is written with the index added to `completed`
- On restart: if checkpoint exists and has incomplete stages, resume from next uncompleted stage
- If no `stages`, the task is atomic — no checkpoint needed (just mark failed on crash)

### Stage results

- Each stage's result is stored in the checkpoint
- Final result = concatenation of all stage results (or just the last stage's result)

---

## J-3: Daemon Entry Point

### Commands

- `python -m praxis --daemon` — fork to background, write PID to `.praxis/praxis.pid`, log to `.praxis/logs/praxis.log`
- `python -m praxis --stop` — read PID file, send SIGTERM, remove PID file
- `python -m praxis --status` — report running/stopped + queue stats

### Implementation

- Use `os.fork()` (Unix) or subprocess for background execution
- PID file at `.praxis/praxis.pid`
- Log file at `.praxis/logs/praxis.log`
- SIGTERM handler: graceful shutdown (finish current task, then exit)
- No log rotation (out of scope per spec)

---

## File Layout (new files)

```
praxis/
  queue.py        # TaskQueue class — CRUD on tasks.jsonl
  checkpoint.py   # Checkpoint read/write/resume logic
  daemon.py       # Daemon start/stop/status
  __main__.py     # Updated: --queue, --daemon, --stop, --status flags
tests/
  test_queue.py       # Queue CRUD + polling + crash recovery
  test_checkpoint.py  # Checkpoint write/resume
  test_daemon.py      # Daemon lifecycle
```
