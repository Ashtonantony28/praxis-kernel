"""Tests for auth rotation hardening (TASK-F01)."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers for crafting fake JWT tokens
# ---------------------------------------------------------------------------


def _make_jwt(payload: dict) -> str:
    """Craft a minimal JWT string with the given payload dict."""
    header_b64 = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).rstrip(b"=").decode()
    # Fake signature
    return f"{header_b64}.{payload_b64}.fakesig"


# ---------------------------------------------------------------------------
# Class 1: parse_jwt_expiry
# ---------------------------------------------------------------------------


class TestParseJwtExpiry:
    """Tests for parse_jwt_expiry()."""

    def test_valid_jwt_with_exp(self):
        """Valid JWT with exp field returns the exp as float."""
        from praxis.runtime.auth import parse_jwt_expiry

        token = _make_jwt({"sub": "user123", "exp": 9999999999})
        result = parse_jwt_expiry(token)
        assert result == 9999999999.0

    def test_opaque_token_returns_none(self):
        """Non-JWT opaque token returns None."""
        from praxis.runtime.auth import parse_jwt_expiry

        result = parse_jwt_expiry("not-a-jwt")
        assert result is None

    def test_jwt_without_exp_returns_none(self):
        """Valid JWT format but payload has no 'exp' field returns None."""
        from praxis.runtime.auth import parse_jwt_expiry

        token = _make_jwt({"sub": "user"})
        result = parse_jwt_expiry(token)
        assert result is None

    def test_malformed_base64_returns_none(self):
        """JWT with invalid base64 in the payload returns None."""
        from praxis.runtime.auth import parse_jwt_expiry

        result = parse_jwt_expiry("a.!!!.b")
        assert result is None

    def test_two_part_token_returns_none(self):
        """Token with only 2 dots returns None."""
        from praxis.runtime.auth import parse_jwt_expiry

        result = parse_jwt_expiry("header.payload")
        assert result is None

    def test_four_part_token_returns_none(self):
        """Token with 4 parts returns None (not a standard JWT)."""
        from praxis.runtime.auth import parse_jwt_expiry

        result = parse_jwt_expiry("a.b.c.d")
        assert result is None


# ---------------------------------------------------------------------------
# Class 2: check_token_expiry
# ---------------------------------------------------------------------------


class TestCheckTokenExpiry:
    """Tests for check_token_expiry()."""

    def test_none_token(self):
        """None token returns configured=False."""
        from praxis.runtime.auth import check_token_expiry

        result = check_token_expiry(None)
        assert result["configured"] is False
        assert result["expires_at"] is None
        assert result["expires_in_hours"] is None
        assert result["near_expiry"] is False

    def test_empty_string(self):
        """Empty string token returns configured=False."""
        from praxis.runtime.auth import check_token_expiry

        result = check_token_expiry("")
        assert result["configured"] is False
        assert result["near_expiry"] is False

    def test_opaque_token(self):
        """Opaque (non-JWT) token returns configured=True with no expiry."""
        from praxis.runtime.auth import check_token_expiry

        result = check_token_expiry("opaque-token-value-not-a-jwt")
        assert result["configured"] is True
        assert result["expires_at"] is None
        assert result["expires_in_hours"] is None
        assert result["near_expiry"] is False

    def test_near_expiry_token(self):
        """JWT expiring in 12h is near_expiry=True with expires_in_hours in (0, 24)."""
        from praxis.runtime.auth import check_token_expiry

        now_ts = datetime.now(timezone.utc).timestamp()
        exp_ts = now_ts + 12 * 3600  # 12 hours from now
        token = _make_jwt({"exp": int(exp_ts)})
        result = check_token_expiry(token, warning_hours=24)
        assert result["configured"] is True
        assert result["near_expiry"] is True
        assert 0 < result["expires_in_hours"] < 24

    def test_far_future_token(self):
        """JWT expiring in 72h is near_expiry=False."""
        from praxis.runtime.auth import check_token_expiry

        now_ts = datetime.now(timezone.utc).timestamp()
        exp_ts = now_ts + 72 * 3600  # 72 hours from now
        token = _make_jwt({"exp": int(exp_ts)})
        result = check_token_expiry(token, warning_hours=24)
        assert result["configured"] is True
        assert result["near_expiry"] is False
        assert result["expires_in_hours"] > 24

    def test_already_expired_token(self):
        """JWT that expired 1h ago has near_expiry=False (already past)."""
        from praxis.runtime.auth import check_token_expiry

        now_ts = datetime.now(timezone.utc).timestamp()
        exp_ts = now_ts - 3600  # 1 hour ago
        token = _make_jwt({"exp": int(exp_ts)})
        result = check_token_expiry(token, warning_hours=24)
        assert result["configured"] is True
        assert result["near_expiry"] is False
        # expires_in_hours is negative (already expired)
        assert result["expires_in_hours"] < 0

    def test_expires_at_is_iso8601(self):
        """expires_at field is an ISO8601 string when exp is present."""
        from praxis.runtime.auth import check_token_expiry

        now_ts = datetime.now(timezone.utc).timestamp()
        token = _make_jwt({"exp": int(now_ts + 3600)})
        result = check_token_expiry(token)
        # Should parse without error as ISO8601
        assert result["expires_at"] is not None
        datetime.fromisoformat(result["expires_at"])


# ---------------------------------------------------------------------------
# Class 3: build_credential_inventory
# ---------------------------------------------------------------------------


class TestBuildCredentialInventory:
    """Tests for build_credential_inventory()."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        """Remove all known credential env vars before each test."""
        from praxis.runtime.auth import KNOWN_CREDENTIALS
        for name, _, _ in KNOWN_CREDENTIALS:
            monkeypatch.delenv(name, raising=False)

    def test_empty_env(self, monkeypatch):
        """All credentials are configured=False when env vars are unset."""
        from praxis.runtime.auth import build_credential_inventory

        inventory = build_credential_inventory()
        assert "credentials" in inventory
        for cred in inventory["credentials"]:
            assert cred["configured"] is False

    def test_configured_credential(self, monkeypatch):
        """Set CLAUDE_CODE_OAUTH_TOKEN → configured=True; value never in output."""
        from praxis.runtime.auth import build_credential_inventory

        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "opaque_oauth_value_xyz")
        inventory = build_credential_inventory()
        oauth_cred = next(
            c for c in inventory["credentials"]
            if c["name"] == "CLAUDE_CODE_OAUTH_TOKEN"
        )
        assert oauth_cred["configured"] is True
        # Value must NOT appear in the output
        dumped = json.dumps(inventory)
        assert "opaque_oauth_value_xyz" not in dumped

    def test_has_generated_at(self):
        """Inventory contains a generated_at key."""
        from praxis.runtime.auth import build_credential_inventory

        inventory = build_credential_inventory()
        assert "generated_at" in inventory
        # Should be parseable as ISO8601
        datetime.fromisoformat(inventory["generated_at"])

    def test_no_credential_values_in_output(self, monkeypatch):
        """Setting ANTHROPIC_API_KEY never leaks the value into the inventory JSON."""
        from praxis.runtime.auth import build_credential_inventory

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value-should-never-appear")
        inventory = build_credential_inventory()
        dumped = json.dumps(inventory)
        assert "sk-secret-value-should-never-appear" not in dumped

    def test_all_known_credentials_represented(self):
        """Every entry in KNOWN_CREDENTIALS appears in the inventory."""
        from praxis.runtime.auth import build_credential_inventory, KNOWN_CREDENTIALS

        inventory = build_credential_inventory()
        names = {c["name"] for c in inventory["credentials"]}
        for env_var, _, _ in KNOWN_CREDENTIALS:
            assert env_var in names

    def test_expiry_check_only_for_flagged_credentials(self, monkeypatch):
        """check_expiry=False credentials never get expiry info even if set."""
        from praxis.runtime.auth import build_credential_inventory

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-a-jwt")
        inventory = build_credential_inventory()
        api_key_cred = next(
            c for c in inventory["credentials"] if c["name"] == "ANTHROPIC_API_KEY"
        )
        # ANTHROPIC_API_KEY has check_expiry=False — no expiry fields
        assert api_key_cred["expires_at"] is None
        assert api_key_cred["expires_in_hours"] is None
        assert api_key_cred["near_expiry"] is False


# ---------------------------------------------------------------------------
# Class 4: write_credential_inventory
# ---------------------------------------------------------------------------


class TestWriteCredentialInventory:
    """Tests for write_credential_inventory()."""

    def test_creates_security_dir(self, tmp_path):
        """Calling write creates .praxis/security/credentials.json."""
        from praxis.runtime.auth import write_credential_inventory

        inventory = {"generated_at": "2026-01-01T00:00:00+00:00", "credentials": []}
        write_credential_inventory(tmp_path, inventory)
        expected = tmp_path / ".praxis" / "security" / "credentials.json"
        assert expected.exists()

    def test_written_json_is_valid(self, tmp_path):
        """Written file is valid JSON."""
        from praxis.runtime.auth import write_credential_inventory

        inventory = {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "credentials": [
                {"name": "TEST_KEY", "description": "test", "configured": False,
                 "expires_at": None, "expires_in_hours": None, "near_expiry": False}
            ],
        }
        write_credential_inventory(tmp_path, inventory)
        written = json.loads((tmp_path / ".praxis" / "security" / "credentials.json").read_text())
        assert written["generated_at"] == inventory["generated_at"]

    def test_no_values_in_file(self, tmp_path, monkeypatch):
        """Written file does not contain credential values."""
        from praxis.runtime.auth import build_credential_inventory, write_credential_inventory

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-in-file")
        inventory = build_credential_inventory()
        write_credential_inventory(tmp_path, inventory)
        content = (tmp_path / ".praxis" / "security" / "credentials.json").read_text()
        assert "sk-should-not-be-in-file" not in content

    def test_creates_parent_dirs(self, tmp_path):
        """Creates nested dirs even if .praxis/ doesn't exist."""
        from praxis.runtime.auth import write_credential_inventory

        workspace = tmp_path / "new_workspace"
        workspace.mkdir()
        inventory = {"generated_at": "2026-01-01T00:00:00+00:00", "credentials": []}
        write_credential_inventory(workspace, inventory)
        assert (workspace / ".praxis" / "security" / "credentials.json").exists()


# ---------------------------------------------------------------------------
# Class 5: warn_near_expiry
# ---------------------------------------------------------------------------


class TestWarnNearExpiry:
    """Tests for warn_near_expiry()."""

    def test_no_warnings_when_empty(self):
        """Empty credentials list returns empty warnings list."""
        from praxis.runtime.auth import warn_near_expiry

        result = warn_near_expiry({"credentials": []})
        assert result == []

    def test_warning_for_near_expiry(self):
        """Credential with near_expiry=True produces a warning with the name."""
        from praxis.runtime.auth import warn_near_expiry

        inventory = {
            "credentials": [
                {
                    "name": "CLAUDE_CODE_OAUTH_TOKEN",
                    "description": "test",
                    "configured": True,
                    "expires_at": "2026-01-02T12:00:00+00:00",
                    "expires_in_hours": 5.5,
                    "near_expiry": True,
                }
            ]
        }
        warnings = warn_near_expiry(inventory)
        assert len(warnings) == 1
        assert "CLAUDE_CODE_OAUTH_TOKEN" in warnings[0]
        assert "5.5h" in warnings[0]

    def test_no_warning_for_far_future(self):
        """Credential with near_expiry=False produces no warning."""
        from praxis.runtime.auth import warn_near_expiry

        inventory = {
            "credentials": [
                {
                    "name": "CLAUDE_CODE_OAUTH_TOKEN",
                    "description": "test",
                    "configured": True,
                    "expires_at": "2026-12-31T00:00:00+00:00",
                    "expires_in_hours": 8000.0,
                    "near_expiry": False,
                }
            ]
        }
        warnings = warn_near_expiry(inventory)
        assert warnings == []

    def test_no_warning_for_unconfigured(self):
        """Unconfigured credential (configured=False) produces no warning."""
        from praxis.runtime.auth import warn_near_expiry

        inventory = {
            "credentials": [
                {
                    "name": "SOME_KEY",
                    "description": "test",
                    "configured": False,
                    "expires_at": None,
                    "expires_in_hours": None,
                    "near_expiry": False,
                }
            ]
        }
        warnings = warn_near_expiry(inventory)
        assert warnings == []

    def test_warning_contains_expires_at(self):
        """Warning message includes the expires_at timestamp."""
        from praxis.runtime.auth import warn_near_expiry

        inventory = {
            "credentials": [
                {
                    "name": "CLAUDE_CODE_OAUTH_TOKEN",
                    "description": "test",
                    "configured": True,
                    "expires_at": "2026-01-02T10:00:00+00:00",
                    "expires_in_hours": 2.0,
                    "near_expiry": True,
                }
            ]
        }
        warnings = warn_near_expiry(inventory)
        assert "2026-01-02T10:00:00+00:00" in warnings[0]


# ---------------------------------------------------------------------------
# Class 6: graceful_auth_error_message
# ---------------------------------------------------------------------------


class TestGracefulAuthError:
    """Tests for graceful_auth_error_message()."""

    def test_oauth_message_mentions_oauth_token(self):
        """OAuth error message mentions CLAUDE_CODE_OAUTH_TOKEN."""
        from praxis.runtime.auth import graceful_auth_error_message

        msg = graceful_auth_error_message("oauth")
        assert "CLAUDE_CODE_OAUTH_TOKEN" in msg

    def test_oauth_message_mentions_refresh(self):
        """OAuth error message provides refresh guidance."""
        from praxis.runtime.auth import graceful_auth_error_message

        msg = graceful_auth_error_message("oauth")
        # Should mention re-auth steps
        assert "claude" in msg.lower() or "login" in msg.lower()

    def test_api_key_message_mentions_api_key(self):
        """Default (api_key) error message mentions ANTHROPIC_API_KEY."""
        from praxis.runtime.auth import graceful_auth_error_message

        msg = graceful_auth_error_message("api_key")
        assert "ANTHROPIC_API_KEY" in msg

    def test_api_key_message_mentions_console(self):
        """Default error message mentions the Anthropic console URL."""
        from praxis.runtime.auth import graceful_auth_error_message

        msg = graceful_auth_error_message()
        assert "console.anthropic.com" in msg

    def test_cloud_message_mentions_cloud_api_key(self):
        """Cloud error message mentions PRAXIS_CLOUD_API_KEY."""
        from praxis.runtime.auth import graceful_auth_error_message

        msg = graceful_auth_error_message("cloud")
        assert "PRAXIS_CLOUD_API_KEY" in msg

    def test_local_message_mentions_local(self):
        """Local error message mentions local model server."""
        from praxis.runtime.auth import graceful_auth_error_message

        msg = graceful_auth_error_message("local")
        assert "local" in msg.lower()

    def test_unknown_method_falls_back_to_api_key(self):
        """Unknown auth_method falls back to the api_key message."""
        from praxis.runtime.auth import graceful_auth_error_message

        msg = graceful_auth_error_message("unknown_method")
        # Falls back to default api_key branch
        assert "ANTHROPIC_API_KEY" in msg

    def test_all_messages_start_with_praxis_fatal(self):
        """All error messages start with the [praxis] fatal: prefix."""
        from praxis.runtime.auth import graceful_auth_error_message

        for method in ("oauth", "cloud", "local", "api_key"):
            msg = graceful_auth_error_message(method)
            assert msg.startswith("[praxis] fatal:"), (
                f"Message for auth_method={method!r} does not start with [praxis] fatal:"
            )
