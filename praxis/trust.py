"""trust.py — per-sender trust tiers for input channels (fail closed).

Resolution: the local CLI is OWNER; otherwise env PRAXIS_OWNER_IDS /
PRAXIS_ADMIN_IDS ("channel:sender" entries, comma-separated), then
convergence.yaml channels.<channel>.trust_ids.{owner,admin,user}, then
channels.<channel>.trusted_contacts (USER). Anything else is BLOCKED.
Email addresses compare case-insensitively. Non-OWNER tasks run in plan mode.
"""

from __future__ import annotations

import os
from enum import IntEnum
from pathlib import Path
from typing import Any

import yaml

CHANNELS: frozenset[str] = frozenset(
    {"cli", "queue", "slack", "email", "webhook", "mcp", "telegram", "whatsapp"}
)


class Tier(IntEnum):
    BLOCKED = 0
    USER = 1
    ADMIN = 2
    OWNER = 3


def _env_ids(name: str) -> set[str]:
    return {e.strip() for e in os.environ.get(name, "").split(",") if e.strip()}


def channel_config(channel: str) -> dict[str, Any]:
    root = os.environ.get("PRAXIS_WORKSPACE_ROOT")
    if not root:
        return {}
    try:
        data = yaml.safe_load((Path(root) / "convergence.yaml").read_text()) or {}
        cfg = (data.get("channels") or {}).get(channel) or {}
    except (OSError, yaml.YAMLError, AttributeError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def tier_for(channel: str, sender_id: str) -> Tier:
    norm = str.lower if channel == "email" else (lambda v: v)
    sender = norm(str(sender_id).strip())
    if channel not in CHANNELS or not sender:
        return Tier.BLOCKED
    if channel == "cli":
        return Tier.OWNER

    def ids(raw: Any) -> set[str]:
        return {norm(str(i).strip()) for i in raw or []}

    key = f"{channel}:{sender}"
    if key in ids(_env_ids("PRAXIS_OWNER_IDS")):
        return Tier.OWNER
    if key in ids(_env_ids("PRAXIS_ADMIN_IDS")):
        return Tier.ADMIN
    cfg = channel_config(channel)
    trust_ids = cfg.get("trust_ids") if isinstance(cfg.get("trust_ids"), dict) else {}
    for tier in (Tier.OWNER, Tier.ADMIN, Tier.USER):
        if sender in ids(trust_ids.get(tier.name.lower())):
            return tier
    if sender in ids(cfg.get("trusted_contacts")):
        return Tier.USER
    return Tier.BLOCKED


def mode_for(tier: Tier) -> str | None:
    """Task mode for a sender tier: only OWNER tasks run unrestricted."""
    return None if tier is Tier.OWNER else "plan"
