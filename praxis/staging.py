"""Staged external actions: idempotency, HMAC signing and verification.

Entries in .praxis/staging/external_actions.jsonl follow praxis/schemas/staged_action.json.
When PRAXIS_STAGING_HMAC_KEY is set, entries are signed at stage time and every
execute path must call verify() before executing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STAGING_FILENAME = "external_actions.jsonl"
HMAC_ENV = "PRAXIS_STAGING_HMAC_KEY"
_DEDUPE_STATUSES = frozenset({"pending", "approved", "executed"})
_current_task: ContextVar[str | None] = ContextVar("praxis_task_id", default=None)


def current_task_id() -> str | None:
    """Id of the queue task currently running in this context, if any."""
    return _current_task.get()


@contextmanager
def task_scope(task_id: str | None):
    token = _current_task.set(task_id)
    try:
        yield
    finally:
        _current_task.reset(token)


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def idempotency_key(task_id: str | None, provider: str, action: str, params: dict) -> str:
    """Stable key for (task_id, provider, action, sha256(canonical params))."""
    params_hash = hashlib.sha256(_canonical(params).encode()).hexdigest()
    return hashlib.sha256(_canonical([task_id, provider, action, params_hash]).encode()).hexdigest()


def hmac_key() -> str:
    return os.environ.get(HMAC_ENV, "")


def sign(entry: dict, key: str) -> str:
    """Hex HMAC-SHA256 of the canonical JSON of entry without '_hmac'."""
    payload = {k: v for k, v in entry.items() if k != "_hmac"}
    return hmac.new(key.encode(), _canonical(payload).encode(), hashlib.sha256).hexdigest()


def verify(entry: dict, key: str | None = None) -> bool:
    """True if no key is configured, else True only for a matching signature."""
    key = hmac_key() if key is None else key
    if not key:
        return True
    stored = entry.get("_hmac")
    return isinstance(stored, str) and hmac.compare_digest(sign(entry, key), stored)


def resign(entry: dict) -> None:
    """Re-sign an entry after a legitimate status change (no-op without a key)."""
    key = hmac_key()
    if key:
        entry["_hmac"] = sign(entry, key)


def read_entries(staging_file: Path) -> list[dict]:
    if not staging_file.exists():
        return []
    entries = []
    for line in staging_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def write_entries(staging_file: Path, entries: list[dict]) -> None:
    with staging_file.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def stage_action(
    provider: str,
    action: str,
    params: dict,
    workspace_root: Path,
    task_id: str | None = None,
) -> str:
    """Append a staged action to external_actions.jsonl unless already staged."""
    staging_dir = workspace_root / ".praxis" / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_file = staging_dir / STAGING_FILENAME
    key = idempotency_key(task_id, provider, action, params)
    for e in read_entries(staging_file):
        if e.get("idempotency_key") == key and e.get("status") in _DEDUPE_STATUSES:
            return (
                f"Already staged {provider} '{action}' action (id {e.get('id')}, "
                f"status {e.get('status')}). Not staged again."
            )
    entry = {
        "id": str(uuid.uuid4()),
        "provider": provider,
        "action": action,
        "params": params,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "task_id": task_id,
        "idempotency_key": key,
    }
    resign(entry)
    with staging_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return (
        f"Staged {provider} '{action}' action for human review. "
        f"Run 'python -m praxis --approve' to review and execute. "
        f"(Staged to: {staging_file})"
    )
