"""SG5 — staged action signing, idempotency and approval integrity."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import jsonschema
import pytest

from praxis.staging import idempotency_key, stage_action

_SCHEMA = json.loads(
    (Path(__file__).parent.parent / "praxis" / "schemas" / "staged_action.json").read_text()
)
_KEY = "dummy-test-key"


def _staging_file(root: Path) -> Path:
    return root / ".praxis" / "staging" / "external_actions.jsonl"


def _entries(root: Path) -> list[dict]:
    return [json.loads(l) for l in _staging_file(root).read_text().splitlines() if l.strip()]


def _rewrite(root: Path, entries: list[dict]) -> None:
    _staging_file(root).write_text("".join(json.dumps(e) + "\n" for e in entries))


def _tamper(root: Path) -> None:
    entries = _entries(root)
    entries[0]["params"]["title"] = "evil"
    _rewrite(root, entries)


def _api_approve(root: Path, approval_id: str) -> dict:
    from praxis.api import post_approval_action

    req = MagicMock()
    req.headers = {}
    req.path_params = {"approval_id": approval_id, "action": "approve"}
    with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "", "PRAXIS_WORKSPACE_ROOT": str(root)}):
        resp = asyncio.run(post_approval_action(req))
    return json.loads(resp.body)


@pytest.fixture
def signed(monkeypatch):
    monkeypatch.setenv("PRAXIS_STAGING_HMAC_KEY", _KEY)


@pytest.fixture
def unsigned(monkeypatch):
    monkeypatch.delenv("PRAXIS_STAGING_HMAC_KEY", raising=False)


class TestIdempotencyKey:
    def test_deterministic_and_order_independent(self):
        a = idempotency_key("t1", "linear", "create_issue", {"a": 1, "b": [1, 2]})
        b = idempotency_key("t1", "linear", "create_issue", {"b": [1, 2], "a": 1})
        assert a == b and isinstance(a, str) and a

    def test_varies_with_each_component(self):
        base = idempotency_key("t1", "linear", "create_issue", {"a": 1})
        assert base != idempotency_key("t2", "linear", "create_issue", {"a": 1})
        assert base != idempotency_key(None, "linear", "create_issue", {"a": 1})
        assert base != idempotency_key("t1", "notion", "create_issue", {"a": 1})
        assert base != idempotency_key("t1", "linear", "add_comment", {"a": 1})
        assert base != idempotency_key("t1", "linear", "create_issue", {"a": 2})


class TestStageAction:
    def test_signed_entry_conforms_to_schema(self, tmp_path, signed):
        stage_action("linear", "create_issue", {"title": "x"}, tmp_path, task_id="t1")
        (entry,) = _entries(tmp_path)
        jsonschema.validate(entry, _SCHEMA)
        assert entry["task_id"] == "t1"
        assert entry["idempotency_key"] == idempotency_key("t1", "linear", "create_issue", {"title": "x"})
        assert entry["_hmac"]

    def test_unsigned_without_key(self, tmp_path, unsigned):
        stage_action("notion", "create_page", {"title": "x"}, tmp_path)
        (entry,) = _entries(tmp_path)
        jsonschema.validate(entry, _SCHEMA)
        assert "_hmac" not in entry
        assert entry["task_id"] is None

    def test_duplicate_staging_dedupes(self, tmp_path, unsigned):
        first = stage_action("linear", "create_issue", {"title": "x", "team_id": "a"}, tmp_path, task_id="t1")
        second = stage_action("linear", "create_issue", {"team_id": "a", "title": "x"}, tmp_path, task_id="t1")
        assert len(_entries(tmp_path)) == 1
        assert "Already staged" in second and "Already staged" not in first

    def test_different_task_is_not_deduped(self, tmp_path, unsigned):
        stage_action("linear", "create_issue", {"title": "x"}, tmp_path, task_id="t1")
        stage_action("linear", "create_issue", {"title": "x"}, tmp_path, task_id="t2")
        assert len(_entries(tmp_path)) == 2

    def test_rejected_entry_can_be_restaged(self, tmp_path, unsigned):
        stage_action("linear", "create_issue", {"title": "x"}, tmp_path)
        entries = _entries(tmp_path)
        entries[0]["status"] = "rejected"
        _rewrite(tmp_path, entries)
        stage_action("linear", "create_issue", {"title": "x"}, tmp_path)
        assert [e["status"] for e in _entries(tmp_path)] == ["rejected", "pending"]

    def test_integration_stagers_use_shared_path(self, tmp_path, signed):
        from praxis.integrations import linear, notion, wiki_sync

        linear._stage_action("linear", "create_issue", {"title": "a"}, tmp_path)
        notion._stage_action("notion", "create_page", {"title": "b"}, tmp_path)
        wiki_sync._stage_external("linear", "create_issue", {"title": "c"}, tmp_path)
        for entry in _entries(tmp_path):
            jsonschema.validate(entry, _SCHEMA)
            assert entry["_hmac"] and entry["idempotency_key"]


class TestCliApproveIntegrity:
    def _approve(self, root: Path) -> MagicMock:
        from praxis.__main__ import _run_approve

        with patch("builtins.input", side_effect=["y"]), \
                patch("praxis.__main__._execute_approved_action", return_value="ok") as ex:
            _run_approve(_staging_file(root))
        return ex

    def test_tampered_entry_rejected(self, tmp_path, signed, capsys):
        stage_action("notion", "create_page", {"title": "x"}, tmp_path)
        _tamper(tmp_path)
        ex = self._approve(tmp_path)
        ex.assert_not_called()
        (entry,) = _entries(tmp_path)
        assert entry["status"] == "rejected" and entry["_tamper_detected"] is True
        assert "HMAC" in capsys.readouterr().out

    def test_unsigned_entry_rejected_when_key_set(self, tmp_path, signed):
        _staging_file(tmp_path).parent.mkdir(parents=True)
        _rewrite(tmp_path, [{
            "id": "u1", "provider": "notion", "action": "create_page", "params": {},
            "queued_at": "2026-10-01T00:00:00+00:00", "status": "pending",
        }])
        ex = self._approve(tmp_path)
        ex.assert_not_called()
        (entry,) = _entries(tmp_path)
        assert entry["status"] == "rejected"
        assert "_hmac" not in entry

    def test_valid_signature_executes(self, tmp_path, signed):
        stage_action("notion", "create_page", {"title": "x"}, tmp_path)
        ex = self._approve(tmp_path)
        ex.assert_called_once()
        assert _entries(tmp_path)[0]["status"] == "approved"

    def test_no_key_keeps_unsigned_behaviour(self, tmp_path, unsigned):
        stage_action("notion", "create_page", {"title": "x"}, tmp_path)
        ex = self._approve(tmp_path)
        ex.assert_called_once()

    def test_email_and_calendar_listed_as_manual(self, tmp_path, unsigned, capsys):
        from praxis.__main__ import _run_approve

        staging = tmp_path / ".praxis" / "staging"
        (staging / "drafts").mkdir(parents=True)
        (staging / "events").mkdir(parents=True)
        (staging / "drafts" / "d1.eml").write_text("Subject: hi\n\nbody")
        (staging / "events" / "e1.ics").write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
        _run_approve(_staging_file(tmp_path))
        out = capsys.readouterr().out
        assert "d1.eml" in out and "e1.ics" in out
        assert "manual" in out.lower()
        assert "sent" not in out.lower()


class TestApiApproveIntegrity:
    def test_tampered_entry_rejected(self, tmp_path, signed):
        stage_action("notion", "create_page", {"title": "x"}, tmp_path)
        _tamper(tmp_path)
        entry_id = _entries(tmp_path)[0]["id"]
        with patch("praxis.__main__._execute_approved_action", return_value="ok") as ex:
            body = _api_approve(tmp_path, entry_id)
        ex.assert_not_called()
        assert body["status"] == "rejected" and body["_tamper_detected"] is True
        assert _entries(tmp_path)[0]["status"] == "rejected"

    def test_unsigned_entry_rejected_when_key_set(self, tmp_path, signed, monkeypatch):
        monkeypatch.delenv("PRAXIS_STAGING_HMAC_KEY")
        stage_action("notion", "create_page", {"title": "x"}, tmp_path)
        monkeypatch.setenv("PRAXIS_STAGING_HMAC_KEY", _KEY)
        entry_id = _entries(tmp_path)[0]["id"]
        with patch("praxis.__main__._execute_approved_action", return_value="ok") as ex:
            body = _api_approve(tmp_path, entry_id)
        ex.assert_not_called()
        assert body["status"] == "rejected"

    def test_valid_signature_executes(self, tmp_path, signed):
        stage_action("notion", "create_page", {"title": "x"}, tmp_path)
        entry_id = _entries(tmp_path)[0]["id"]
        with patch("praxis.__main__._execute_approved_action", return_value="ok") as ex:
            body = _api_approve(tmp_path, entry_id)
        ex.assert_called_once()
        assert body["status"] == "approved"

    def test_non_pending_entry_not_reexecuted(self, tmp_path, unsigned):
        stage_action("notion", "create_page", {"title": "x"}, tmp_path)
        entry_id = _entries(tmp_path)[0]["id"]
        with patch("praxis.__main__._execute_approved_action", return_value="ok") as ex:
            _api_approve(tmp_path, entry_id)
            body = _api_approve(tmp_path, entry_id)
        ex.assert_called_once()
        assert body["status"] == "approved"
