"""Tests for praxis/api.py — token auth helpers."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers to build fake Starlette Request / WebSocket objects
# ---------------------------------------------------------------------------

def _make_request(auth_header: str = "") -> MagicMock:
    """Return a mock Starlette Request with the given Authorization header."""
    req = MagicMock()
    req.headers = {"Authorization": auth_header} if auth_header else {}
    return req


def _make_websocket(token_param: str = "", auth_header: str = "") -> MagicMock:
    """Return a mock Starlette WebSocket."""
    ws = MagicMock()
    ws.query_params = {"token": token_param} if token_param else {}
    ws.headers = {"Authorization": auth_header} if auth_header else {}
    return ws


# ---------------------------------------------------------------------------
# _check_token — HTTP requests
# ---------------------------------------------------------------------------

class TestCheckToken:
    def test_no_auth_when_token_not_set(self):
        """If PRAXIS_UI_TOKEN is not set, _check_token always returns None."""
        from praxis.api import _check_token

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("PRAXIS_UI_TOKEN", None)
            result = _check_token(_make_request())
        assert result is None

    def test_no_auth_when_token_empty_string(self):
        """If PRAXIS_UI_TOKEN is empty string, auth is disabled."""
        from praxis.api import _check_token

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": ""}):
            result = _check_token(_make_request())
        assert result is None

    def test_401_when_token_wrong(self):
        """If token is configured and header is wrong, return 401."""
        from praxis.api import _check_token

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token(_make_request("Bearer wrongtoken"))
        assert result is not None
        assert result.status_code == 401

    def test_401_when_no_auth_header(self):
        """If token is configured and no header is provided, return 401."""
        from praxis.api import _check_token

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token(_make_request(""))
        assert result is not None
        assert result.status_code == 401

    def test_passes_when_correct_bearer(self):
        """Correct Bearer token returns None (auth passed)."""
        from praxis.api import _check_token

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token(_make_request("Bearer secret123"))
        assert result is None

    def test_401_response_is_json(self):
        """401 response body is JSON with 'error' key."""
        from praxis.api import _check_token
        import json

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token(_make_request("Bearer bad"))
        assert result is not None
        body = json.loads(result.body)
        assert "error" in body

    def test_passes_exactly_right_token(self):
        """Token comparison is exact — no prefix/suffix accepted."""
        from praxis.api import _check_token

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "abc"}):
            # 'abc' token but header has 'abcX'
            result = _check_token(_make_request("Bearer abcX"))
        assert result is not None
        assert result.status_code == 401


# ---------------------------------------------------------------------------
# _check_token_ws — WebSocket handshake
# ---------------------------------------------------------------------------

class TestCheckTokenWs:
    def test_no_auth_when_token_not_set(self):
        """If PRAXIS_UI_TOKEN is not set, _check_token_ws always returns True."""
        from praxis.api import _check_token_ws

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("PRAXIS_UI_TOKEN", None)
            result = _check_token_ws(_make_websocket())
        assert result is True

    def test_returns_false_when_token_wrong(self):
        """Incorrect query param and no header → False."""
        from praxis.api import _check_token_ws

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token_ws(_make_websocket("badtoken"))
        assert result is False

    def test_returns_true_with_correct_query_param(self):
        """Correct token in ?token= query param → True."""
        from praxis.api import _check_token_ws

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token_ws(_make_websocket(token_param="secret123"))
        assert result is True

    def test_returns_true_with_correct_auth_header(self):
        """Correct Bearer in Authorization header → True."""
        from praxis.api import _check_token_ws

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token_ws(
                _make_websocket(auth_header="Bearer secret123")
            )
        assert result is True

    def test_returns_false_no_token_no_header(self):
        """Token configured but neither query param nor header provided → False."""
        from praxis.api import _check_token_ws

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            result = _check_token_ws(_make_websocket())
        assert result is False

    def test_query_param_takes_precedence_over_header(self):
        """If query param is correct, it is accepted even if header is wrong."""
        from praxis.api import _check_token_ws

        with patch.dict(os.environ, {"PRAXIS_UI_TOKEN": "secret123"}):
            ws = _make_websocket(token_param="secret123", auth_header="Bearer bad")
            result = _check_token_ws(ws)
        assert result is True
