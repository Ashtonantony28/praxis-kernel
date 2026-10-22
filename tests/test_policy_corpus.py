"""SG1 policy fixture corpus: every case in tests/fixtures/policy/*.jsonl through evaluate()."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from praxis.modes.base import Mode
from praxis.policy import PolicyContext, ToolCall, evaluate

FIXTURES = Path(__file__).parent / "fixtures" / "policy"
SCHEMA = json.loads((Path(__file__).parent.parent / "praxis" / "schemas" / "policy_case.json").read_text())
KNOWN_WRONG = set(json.loads((FIXTURES / "known_wrong.json").read_text()))
CASES = [json.loads(line) for f in sorted(FIXTURES.glob("*.jsonl")) for line in f.read_text().splitlines() if line.strip()]


def _sub(value: Any, root: str) -> Any:
    if isinstance(value, str):
        return value.replace("{workspace}", root)
    if isinstance(value, dict):
        return {k: _sub(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_sub(v, root) for v in value]
    return value


def _params():
    for case in CASES:
        marks = [pytest.mark.xfail(strict=True, reason="known_wrong.json")] if case["id"] in KNOWN_WRONG else []
        yield pytest.param(case, id=case["id"], marks=marks)


def test_ids_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))


def test_known_wrong_ids_exist():
    assert KNOWN_WRONG <= {c["id"] for c in CASES}


def test_allow_minimum():
    assert sum(c["expect"] == "allow" for c in CASES) >= 50


# TODO(SG1): block corpus (>= 200 cases) is pending the owner; add a minimum once it lands.


@pytest.mark.parametrize("case", list(_params()))
def test_case(case: dict, tmp_path: Path):
    jsonschema.validate(case, SCHEMA)
    root = tmp_path.resolve()
    ctx = case["ctx"]
    call = ToolCall(case["call"]["name"], _sub(case["call"]["input"], str(root)))
    policy_ctx = PolicyContext(
        workspace_root=root,
        allowed_domains=frozenset(ctx["allowed_domains"]),
        mode=Mode.load(ctx["mode"]) if ctx["mode"] else None,
        origin=ctx["origin"],
    )
    verdict = evaluate(call, policy_ctx)
    assert (verdict.allowed, verdict.rule) == (case["expect"] == "allow", case["rule"]), verdict.reason
