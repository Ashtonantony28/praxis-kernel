# Unattended Operation Readiness

**Updated:** 2026-05-25 (Phase J complete)

---

## What is now true

- **Task queue exists.** External processes can write tasks to `.praxis/queue/tasks.jsonl` and Praxis will pick them up, execute them, and write results back.
- **Crash-safe.** If the queue processor crashes mid-task, on restart the task is marked "failed" rather than blocking the queue forever.
- **Multi-stage tasks resume.** Tasks with `stages` list get checkpointed after each stage. On restart, execution resumes from the last completed stage.
- **Daemon mode works.** Praxis can run as a background process with PID file and log output. Start/stop/status commands provided.
- **Graceful shutdown.** SIGTERM causes the loop to finish the current task stage, checkpoint, and exit cleanly.
- **All modes coexist.** Interactive (`python -m praxis "prompt"`), foreground queue (`--queue`), and daemon (`--daemon`) are independent paths through the same entry point.

## What remains

- **No file-watching.** Queue polling uses `time.sleep()`. For high-throughput, inotify/fswatch would be better. Low priority — 2s poll is fine for current scale.
- **No task submission CLI.** Tasks must be written to `tasks.jsonl` manually or by an external tool. A `python -m praxis --submit "prompt"` command would be a convenience.
- **No result notification.** Results are written to files but there's no webhook, email, or Slack notification. The caller must poll the result file or task status.
- **Single-worker only.** The queue runner is single-threaded — one task at a time. Concurrent execution would require locking or a proper queue backend.
- **Log rotation out of scope.** Daemon log at `.praxis/logs/praxis.log` grows unbounded.
- **Rate limit gaps still apply.** The Phase H gaps (rate limit budget, retry window) still affect unattended runs against OAuth/API key endpoints. Cloud runtime routing via convergence.yaml can mitigate.
