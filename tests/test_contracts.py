"""SG1 shared interface contracts: policy types, Mode.permits/restrict, trust tiers, schemas."""

from __future__ import annotations

import dataclasses
import itertools
import json
from pathlib import Path

import jsonschema
import pytest

from praxis.modes.base import Mode
from praxis.policy import ORIGINS, PolicyContext, ToolCall, Verdict, context_from_env, evaluate
from praxis.queue import Task, TaskStore
from praxis.runtime.base import LegacyToolExecutor, ToolExecutor
from praxis.runtime.enforcement import EnforcementError
from praxis.trust import CHANNELS, Tier, tier_for

SCHEMAS = Path(__file__).parent.parent / "praxis" / "schemas"


# ---------- imports / dataclasses ----------


def test_contract_names_importable():
    assert callable(evaluate) and callable(context_from_env) and callable(tier_for)
    assert ToolExecutor is not LegacyToolExecutor
    assert TaskStore.__name__ == "TaskStore"
    assert ORIGINS == CHANNELS


@pytest.mark.parametrize(
    "obj, field",
    [
        (ToolCall("Read", {}), "name"),
        (PolicyContext(Path("/w"), frozenset(), None), "origin"),
        (Verdict(True, "allow", ""), "rule"),
        (Mode(name="m"), "denied_tools"),
    ],
)
def test_frozen(obj, field):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(obj, field, None)


def test_policy_context_default_origin():
    assert PolicyContext(Path("/w"), frozenset(), None).origin == "cli"


@pytest.mark.parametrize(
    "inp, expected",
    [
        ({"action": "search"}, "search"),
        ({}, None),
        ({"action": ""}, None),
        ({"action": 3}, None),
        ({"action": None}, None),
    ],
)
def test_toolcall_action(inp, expected):
    assert ToolCall("notion", inp).action == expected


# ---------- context_from_env ----------


def test_context_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("PRAXIS_ALLOWED_DOMAINS", " github.com, docs.python.org ,,")
    mode = Mode(name="m")
    ctx = context_from_env(mode=mode, origin="queue")
    assert ctx == PolicyContext(tmp_path.resolve(), frozenset({"github.com", "docs.python.org"}), mode, "queue")


def test_context_from_env_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("PRAXIS_ALLOWED_DOMAINS", raising=False)
    ctx = context_from_env()
    assert (ctx.allowed_domains, ctx.mode, ctx.origin) == (frozenset(), None, "cli")


def test_context_from_env_unset_root_fails_closed(monkeypatch):
    monkeypatch.delenv("PRAXIS_WORKSPACE_ROOT", raising=False)
    with pytest.raises(EnforcementError):
        context_from_env()


def test_context_from_env_rejects_unknown_origin():
    with pytest.raises(ValueError):
        context_from_env(origin="carrier-pigeon")


# ---------- Mode.permits ----------


def test_permits_plain_tool_entries():
    mode = Mode(name="m", denied_tools=frozenset({"Bash"}))
    assert not mode.permits(ToolCall("Bash", {"command": "ls"}))
    assert mode.permits(ToolCall("Read", {"file_path": "a"}))


def test_permits_tool_denies_every_action():
    mode = Mode(name="m", denied_tools=frozenset({"notion"}))
    assert not mode.permits(ToolCall("notion", {"action": "search"}))
    assert not mode.permits(ToolCall("notion", {}))


@pytest.mark.parametrize("entry", ["notion:create_page", "notion.create_page", "Notion:create_page"])
def test_permits_tool_action_entries(entry):
    mode = Mode(name="m", denied_tools=frozenset({entry}))
    assert not mode.permits(ToolCall("notion", {"action": "create_page"}))
    assert mode.permits(ToolCall("notion", {"action": "search"}))
    assert mode.permits(ToolCall("notion", {}))


def test_permits_allow_list():
    mode = Mode(name="m", allowed_tools=frozenset({"Read", "notion:search"}))
    assert mode.permits(ToolCall("Read", {}))
    assert mode.permits(ToolCall("notion", {"action": "search"}))
    assert not mode.permits(ToolCall("notion", {"action": "create_page"}))
    assert not mode.permits(ToolCall("Write", {}))


def test_permits_deny_beats_allow():
    mode = Mode(name="m", allowed_tools=frozenset({"Bash"}), denied_tools=frozenset({"Bash"}))
    assert not mode.permits(ToolCall("Bash", {}))


def test_builtin_plan_denies_integration_writes():
    plan = Mode.load("plan")
    assert not plan.permits(ToolCall("notion", {"action": "create_page"}))
    assert not plan.permits(ToolCall("Write", {"file_path": "a"}))
    assert plan.permits(ToolCall("notion", {"action": "search"}))
    assert plan.permits(ToolCall("Read", {}))


# ---------- Mode.restrict ----------

MODES = [
    Mode.load("build"),
    Mode.load("plan"),
    Mode(name="readonly", allowed_tools=frozenset({"Read", "Grep", "Glob"})),
    Mode(name="notion_read", allowed_tools=frozenset({"Read", "notion:search", "notion:get_page"})),
    Mode(name="notion_all", allowed_tools=frozenset({"notion", "Write"})),
    Mode(name="nobash", denied_tools=frozenset({"Bash", "linear:create_issue"})),
    Mode(name="disjoint", allowed_tools=frozenset({"Agent"})),
]

CALLS = [
    ToolCall(name, {"action": action} if action else {})
    for name, action in [
        ("Read", None), ("Grep", None), ("Glob", None), ("Write", None), ("Edit", None),
        ("Bash", None), ("Agent", None), ("WebFetch", None), ("notion", "search"),
        ("notion", "get_page"), ("notion", "create_page"), ("notion", None),
        ("linear", "create_issue"), ("linear", "list_issues"), ("Wiki", "ingest"),
    ]
]


@pytest.mark.parametrize(
    "parent, child", list(itertools.product(MODES, MODES + [None])), ids=lambda m: getattr(m, "name", "none")
)
def test_restrict_never_wider(parent, child):
    eff = parent.restrict(child)
    assert eff.denied_tools >= parent.denied_tools
    assert eff.requires_confirmation >= parent.requires_confirmation
    for call in CALLS:
        if eff.permits(call):
            assert parent.permits(call), (parent.name, getattr(child, "name", None), call)
            assert child is None or child.permits(call), (parent.name, child.name, call)


def test_restrict_none_is_self():
    plan = Mode.load("plan")
    assert plan.restrict(None) is plan


def test_restrict_disjoint_allows_nothing():
    eff = MODES[2].restrict(MODES[-1])
    assert not any(eff.permits(c) for c in CALLS)


def test_restrict_plan_child_of_build():
    eff = Mode.load("build").restrict(Mode.load("plan"))
    assert not eff.permits(ToolCall("Write", {}))
    assert eff.requires_confirmation


# ---------- trust tiers ----------


def test_tier_order():
    assert Tier.BLOCKED < Tier.USER < Tier.ADMIN < Tier.OWNER
    assert int(Tier.BLOCKED) == 0 and int(Tier.OWNER) == 3


def test_cli_is_owner():
    assert tier_for("cli", "anyone") is Tier.OWNER


@pytest.mark.parametrize(
    "channel, sender",
    [("slack", "U123"), ("email", "a@b.c"), ("webhook", "hook"), ("telegram", "42"), ("whatsapp", "+1"),
     ("mcp", "client"), ("queue", "x"), ("irc", "nick"), ("slack", ""), ("cli", "  ")],
)
def test_fail_closed_defaults(monkeypatch, channel, sender):
    monkeypatch.delenv("PRAXIS_OWNER_IDS", raising=False)
    monkeypatch.delenv("PRAXIS_ADMIN_IDS", raising=False)
    assert tier_for(channel, sender) is Tier.BLOCKED


def test_env_ids(monkeypatch):
    monkeypatch.setenv("PRAXIS_OWNER_IDS", "slack:U1, telegram:42")
    monkeypatch.setenv("PRAXIS_ADMIN_IDS", "slack:U2")
    assert tier_for("slack", "U1") is Tier.OWNER
    assert tier_for("telegram", "42") is Tier.OWNER
    assert tier_for("slack", "U2") is Tier.ADMIN
    assert tier_for("email", "U1") is Tier.BLOCKED


def test_convergence_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "convergence.yaml").write_text(
        "channels:\n"
        "  slack:\n"
        "    trust_ids: {admin: [U9], user: [U8]}\n"
        "    trusted_contacts: [U7]\n"
    )
    assert tier_for("slack", "U9") is Tier.ADMIN
    assert tier_for("slack", "U8") is Tier.USER
    assert tier_for("slack", "U7") is Tier.USER
    assert tier_for("slack", "U6") is Tier.BLOCKED


def test_malformed_convergence_yaml_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "convergence.yaml").write_text("channels: [not, a, mapping\n")
    assert tier_for("slack", "U1") is Tier.BLOCKED


# ---------- schemas ----------

GOOD_BAD = {
    "event.json": (
        {"ts": "2026-10-22T00:00:00Z", "type": "TASK_COMPLETED", "data": {"id": "t1"}},
        {"ts": "2026-10-22T00:00:00Z", "type": "", "data": {}},
    ),
    "policy_case.json": (
        {"id": "read-001", "call": {"name": "Read", "input": {"file_path": "a"}},
         "ctx": {"mode": None, "origin": "cli", "allowed_domains": []}, "expect": "allow", "rule": "allow"},
        {"id": "read-001", "call": {"name": "Read", "input": {}},
         "ctx": {"mode": None, "origin": "cli", "allowed_domains": []}, "expect": "allow", "rule": "write.outside_workspace"},
    ),
    "staged_action.json": (
        {"id": "s1", "provider": "notion", "action": "create_page", "params": {},
         "queued_at": "2026-10-22T00:00:00Z", "status": "pending", "task_id": None},
        {"id": "s1", "provider": "notion", "action": "create_page", "params": {},
         "queued_at": "2026-10-22T00:00:00Z", "status": "sent"},
    ),
    "task.json": (
        {**Task.create("hello").to_dict(), "origin": "queue", "mode": "plan", "cost_budget_usd": 1.5},
        {**Task.create("hello").to_dict(), "priority": "high"},
    ),
}


def test_every_schema_has_examples():
    assert {p.name for p in SCHEMAS.glob("*.json")} == set(GOOD_BAD)


@pytest.mark.parametrize("name", sorted(GOOD_BAD))
def test_schema(name):
    schema = json.loads((SCHEMAS / name).read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    good, bad = GOOD_BAD[name]
    jsonschema.validate(good, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
