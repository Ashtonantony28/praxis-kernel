"""Tests for praxis/setup_wizard.py and the --setup flag in praxis/__main__.py.

All tests mock I/O — no real keyboard input, no real file writes except to tmp_path.
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from praxis.setup_wizard import _append_allowed_domain, _read_env, _write_env, run_wizard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_input(*answers):
    """Return a callable that returns answers in order (for visible prompts)."""
    it = iter(answers)

    def _input(prompt=""):
        return next(it, "")

    return _input


def make_getpass(*answers):
    """Return a callable that returns answers in order (for hidden inputs)."""
    it = iter(answers)

    def _gp(prompt=""):
        return next(it, "")

    return _gp


def _skip_all_inputs(workspace_root: Path):
    """
    Minimal input sequence to complete the wizard with all optionals skipped.

    Steps and prompts:
      1) runtime choice              → "1"  (Claude OAuth)
      getpass: CLAUDE_CODE_OAUTH_TOKEN → "tok"
      2) workspace correct?          → "y"
      3) slack?                      → "n"
      4) github?                     → "n"
      5a) linear?                    → "n"
      5b) notion?                    → "n"
      5c) calendar?                  → "n"
      8) web search?                 → "n"
      9) email?                      → "n"
      10) cost cap                   → ""   (default 2.00)
      11) morning briefing?          → "n"
      12) wiki seed?                 → "n"
    """
    _inp = make_input("1", "y", "n", "n", "n", "n", "n", "n", "n", "", "n", "n")
    _gp = make_getpass("tok")
    return _inp, _gp


# ===========================================================================
# TestReadWriteEnv
# ===========================================================================

class TestReadWriteEnv:
    def test_read_env_empty_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        result = _read_env(env_file)
        assert result == {}

    def test_read_env_ignores_comments_and_blanks(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# this is a comment\n\nKEY=value\n\n# another comment\n", encoding="utf-8")
        result = _read_env(env_file)
        assert result == {"KEY": "value"}

    def test_read_env_parses_key_value(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
        result = _read_env(env_file)
        assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_write_env_overwrite(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OLD_KEY=old_value\n", encoding="utf-8")
        _write_env(env_file, {"NEW_KEY": "new_value"}, "overwrite")
        result = _read_env(env_file)
        assert result == {"NEW_KEY": "new_value"}
        assert "OLD_KEY" not in result

    def test_write_env_merge_only_adds_missing(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=old\n", encoding="utf-8")
        _write_env(env_file, {"EXISTING": "new", "EXTRA": "added"}, "merge")
        result = _read_env(env_file)
        # Existing key must NOT be overwritten in merge mode
        assert result["EXISTING"] == "old"
        # New key must be added
        assert result["EXTRA"] == "added"


# ===========================================================================
# TestRuntimeChoice
# ===========================================================================

class TestRuntimeChoice:
    def test_runtime_choice_1_claude_oauth(self, tmp_path):
        _inp = make_input("1", "y", "n", "n", "n", "n", "n", "n", "n", "", "n", "n")
        _gp = make_getpass("my_oauth_token")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("CLAUDE_CODE_OAUTH_TOKEN") == "my_oauth_token"
        assert env_data.get("PRAXIS_RUNTIME") == "claude"

    def test_runtime_choice_2_gemini(self, tmp_path):
        _inp = make_input("2", "y", "n", "n", "n", "n", "n", "n", "n", "", "n", "n")
        _gp = make_getpass("gemini_key_123")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("PRAXIS_RUNTIME") == "cloud"
        assert env_data.get("PRAXIS_CLOUD_API_KEY") == "gemini_key_123"
        assert "generativelanguage" in env_data.get("PRAXIS_CLOUD_BASE_URL", "")
        assert env_data.get("PRAXIS_CLOUD_MODEL") == "gemini-2.0-flash"

    def test_runtime_choice_3_local(self, tmp_path):
        # choice 3: local; provide base_url, model; then skip optionals
        _inp = make_input(
            "3",                        # choice
            "http://localhost:11434",   # base_url
            "llama3.2",                 # model
            "y",                        # workspace correct
            "n", "n",                   # slack, github
            "n", "n", "n",             # linear, notion, calendar
            "n", "n",                   # web, email
            "",                         # cost cap
            "n", "n",                  # briefing/wiki
        )
        _gp = make_getpass()
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("PRAXIS_RUNTIME") == "local"
        assert env_data.get("PRAXIS_LOCAL_BASE_URL") == "http://localhost:11434"
        assert env_data.get("PRAXIS_LOCAL_MODEL") == "llama3.2"

    def test_runtime_choice_4_api_key(self, tmp_path):
        _inp = make_input("4", "y", "n", "n", "n", "n", "n", "n", "n", "", "n", "n")
        _gp = make_getpass("sk-ant-api03-xyz")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("ANTHROPIC_API_KEY") == "sk-ant-api03-xyz"
        assert env_data.get("PRAXIS_RUNTIME") == "claude"


# ===========================================================================
# TestInvalidRuntimeChoice
# ===========================================================================

class TestInvalidRuntimeChoice:
    def test_invalid_choice_reprompts_once(self, tmp_path, capsys):
        """Two invalid choices → wizard aborts without writing env."""
        _inp = make_input("9", "9")  # both invalid
        _gp = make_getpass()

        written = []

        def capture_write(env_file, data, mode):
            written.append(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        out = capsys.readouterr().out
        assert "Error: invalid runtime choice" in out
        # _write_env should not have been called
        assert written == []

    def test_invalid_choice_accepts_on_second_try(self, tmp_path, capsys):
        """First input invalid, second input valid → continues and completes."""
        _inp = make_input("9", "1", "y", "n", "n", "n", "n", "n", "n", "n", "", "n", "n")
        _gp = make_getpass("tok")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        out = capsys.readouterr().out
        # Should have completed setup
        assert "Setup complete!" in out
        assert env_data.get("PRAXIS_RUNTIME") == "claude"


# ===========================================================================
# TestMergeModeDoesNotOverwrite
# ===========================================================================

class TestMergeModeDoesNotOverwrite:
    def test_merge_does_not_overwrite_existing_key(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING_KEY=original_value\n", encoding="utf-8")
        _write_env(env_file, {"EXISTING_KEY": "new_value"}, "merge")
        result = _read_env(env_file)
        assert result["EXISTING_KEY"] == "original_value"

    def test_merge_adds_missing_key(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING_KEY=original_value\n", encoding="utf-8")
        _write_env(env_file, {"EXISTING_KEY": "new_value", "NEW_KEY": "added"}, "merge")
        result = _read_env(env_file)
        assert result["EXISTING_KEY"] == "original_value"
        assert result["NEW_KEY"] == "added"

    def test_overwrite_mode_replaces_all(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OLD_KEY=old\n", encoding="utf-8")
        _write_env(env_file, {"NEW_KEY": "new"}, "overwrite")
        result = _read_env(env_file)
        assert result == {"NEW_KEY": "new"}
        assert "OLD_KEY" not in result


# ===========================================================================
# TestGetpassUsedForCredentials
# ===========================================================================

class TestGetpassUsedForCredentials:
    def test_oauth_token_uses_getpass(self, tmp_path):
        """Runtime choice 1 — verify _getpass callback is called for the token."""
        getpass_mock = MagicMock(return_value="oauth_tok")
        _inp = make_input("1", "y", "n", "n", "n", "n", "n", "n", "n", "", "n", "n")

        with patch("praxis.setup_wizard._write_env"):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=getpass_mock)

        getpass_mock.assert_called()

    def test_github_token_uses_getpass(self, tmp_path):
        """github choice 'y' — verify _getpass called for token."""
        getpass_mock = MagicMock(return_value="ghp_token123")
        # choice 1, workspace y, slack n, github y, linear n, notion n, calendar n, web n, email n, cost "", briefing n, wiki n
        _inp = make_input("1", "y", "n", "y", "n", "n", "n", "n", "n", "", "n", "n")

        with patch("praxis.setup_wizard._write_env"):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=getpass_mock)

        # getpass was called at least twice: once for oauth token + once for github token
        assert getpass_mock.call_count >= 2

    def test_email_password_uses_getpass(self, tmp_path):
        """email choice 'y' — verify _getpass called for password."""
        getpass_mock = MagicMock(return_value="app_password_xyz")
        # choice 1, workspace y, slack n, github n, linear n, notion n, calendar n, web n, email y (+ host, user), cost "", briefing n, wiki n
        _inp = make_input("1", "y", "n", "n", "n", "n", "n", "n", "y", "imap.gmail.com", "user@example.com", "", "n", "n")

        with patch("praxis.setup_wizard._write_env"):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=getpass_mock)

        # getpass must have been called at least twice (oauth + email password)
        assert getpass_mock.call_count >= 2


# ===========================================================================
# TestOptionalSteps
# ===========================================================================

class TestOptionalSteps:
    def test_slack_skipped_when_n(self, tmp_path):
        _inp, _gp = _skip_all_inputs(tmp_path)
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert "PRAXIS_SLACK_WEBHOOK_URL" not in env_data

    def test_slack_sets_keys_when_y(self, tmp_path):
        # choice 1, workspace y, slack y → webhook_url, bot_token (getpass), app_token, then rest n
        slack_webhook = "https://hooks.slack.com/services/T/B/X"
        slack_app_token = "xapp-1-token"
        _inp = make_input(
            "1",             # runtime
            "y",             # workspace
            "y",             # slack enable
            slack_webhook,   # webhook_url (visible input)
            slack_app_token, # app_token (visible input)
            "n",             # github
            "n",             # linear
            "n",             # notion
            "n",             # calendar
            "n",             # web
            "n",             # email
            "",              # cost cap
            "n",             # briefing
            "n",             # wiki
        )
        _gp = make_getpass("tok", "xoxb-bot-token")  # oauth token, bot token
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("PRAXIS_SLACK_WEBHOOK_URL") == slack_webhook
        # PRAXIS_SLACK_BOT_TOKEN is set via getpass
        assert env_data.get("PRAXIS_SLACK_BOT_TOKEN") == "xoxb-bot-token"
        # PRAXIS_ALLOWED_DOMAINS should include Slack domains
        domains = env_data.get("PRAXIS_ALLOWED_DOMAINS", "")
        assert "hooks.slack.com" in domains

    def test_github_sets_key_when_y(self, tmp_path):
        _inp = make_input("1", "y", "n", "y", "n", "n", "n", "n", "n", "", "n", "n")
        _gp = make_getpass("oauth_tok", "ghp_github_token")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("GITHUB_TOKEN") == "ghp_github_token"

    def test_email_sets_keys_when_y(self, tmp_path):
        _inp = make_input("1", "y", "n", "n", "n", "n", "n", "n", "y", "imap.gmail.com", "me@example.com", "", "n", "n")
        _gp = make_getpass("oauth_tok", "app_pass")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("PRAXIS_EMAIL_IMAP_HOST") == "imap.gmail.com"
        assert env_data.get("PRAXIS_EMAIL_USER") == "me@example.com"
        assert env_data.get("PRAXIS_EMAIL_PASSWORD") == "app_pass"

    def test_web_search_sets_default_domain(self, tmp_path):
        # web "y", empty domain input → default api.search.brave.com
        _inp = make_input("1", "y", "n", "n", "n", "n", "n", "y", "", "n", "", "n", "n")
        _gp = make_getpass("oauth_tok", "brave_key")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        domains = env_data.get("PRAXIS_ALLOWED_DOMAINS", "")
        assert "api.search.brave.com" in domains


# ===========================================================================
# TestCostCircuitBreaker
# ===========================================================================

class TestCostCircuitBreaker:
    def test_default_cost_cap(self, tmp_path):
        _inp, _gp = _skip_all_inputs(tmp_path)
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("PRAXIS_MAX_SESSION_COST") == "2.00"

    def test_custom_cost_cap(self, tmp_path):
        # provide "5.00" as cost cap answer
        _inp = make_input("1", "y", "n", "n", "n", "n", "n", "n", "n", "5.00", "n", "n")
        _gp = make_getpass("tok")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("PRAXIS_MAX_SESSION_COST") == "5.00"


# ===========================================================================
# TestWikiSeed
# ===========================================================================

class TestWikiSeed:
    def test_wiki_seed_skipped_when_n(self, tmp_path):
        _inp, _gp = _skip_all_inputs(tmp_path)

        with patch("praxis.setup_wizard._write_env"):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        wiki_raw = tmp_path / "wiki" / "raw"
        assert not wiki_raw.exists()

    def test_wiki_seed_copies_file(self, tmp_path):
        # Create a source .md file
        source_file = tmp_path / "my_notes.md"
        source_file.write_text("# My notes\nHello world\n", encoding="utf-8")

        # wiki "y", then path to source_file
        _inp = make_input("1", "y", "n", "n", "n", "n", "n", "n", "n", "", "n", "y", str(source_file))
        _gp = make_getpass("tok")

        with patch("praxis.setup_wizard._write_env"):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        dest = tmp_path / "wiki" / "raw" / "my_notes.md"
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == "# My notes\nHello world\n"


# ===========================================================================
# TestSummaryOutput
# ===========================================================================

class TestSummaryOutput:
    def test_summary_prints_configured_items(self, tmp_path, capsys):
        _inp, _gp = _skip_all_inputs(tmp_path)

        with patch("praxis.setup_wizard._write_env"):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        out = capsys.readouterr().out
        assert "Setup complete!" in out
        assert "Runtime:" in out
        assert "Workspace:" in out
        assert "Cost cap:" in out
        assert "Next steps:" in out

    def test_summary_warns_if_env_not_in_gitignore(self, tmp_path, capsys):
        """When no .gitignore exists, output should warn about .env not being ignored."""
        _inp, _gp = _skip_all_inputs(tmp_path)

        # Ensure no .gitignore in tmp_path
        gitignore = tmp_path / ".gitignore"
        if gitignore.exists():
            gitignore.unlink()

        with patch("praxis.setup_wizard._write_env"):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        out = capsys.readouterr().out
        assert "WARNING: .env is NOT in .gitignore" in out


# ===========================================================================
# TestLinearStep
# ===========================================================================

class TestLinearStep:
    def test_linear_skipped_writes_nothing(self, tmp_path):
        """Skipping Linear writes no Linear keys."""
        _inp, _gp = _skip_all_inputs(tmp_path)
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert "PRAXIS_LINEAR_API_KEY" not in env_data

    def test_linear_enabled_writes_key_and_domain(self, tmp_path):
        """Linear y → PRAXIS_LINEAR_API_KEY written; api.linear.app appended to domains."""
        # 1=runtime, y=workspace, n=slack, n=github, y=linear, n=notion, n=calendar,
        # n=web, n=email, ""=cost, n=briefing, n=wiki
        _inp = make_input("1", "y", "n", "n", "y", "n", "n", "n", "n", "", "n", "n")
        _gp = make_getpass("oauth_tok", "linear_key_abc")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("PRAXIS_LINEAR_API_KEY") == "linear_key_abc"
        assert "api.linear.app" in env_data.get("PRAXIS_ALLOWED_DOMAINS", "")

    def test_linear_step_yes_writes_key_and_domain(self, tmp_path):
        """Feature test: Linear 'y' → PRAXIS_LINEAR_API_KEY written; api.linear.app in domains."""
        _inp = make_input("1", "y", "n", "n", "y", "n", "n", "n", "n", "", "n", "n")
        _gp = make_getpass("oauth_tok", "linear_key_feature")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("PRAXIS_LINEAR_API_KEY") == "linear_key_feature"
        assert "api.linear.app" in env_data.get("PRAXIS_ALLOWED_DOMAINS", "")

    def test_linear_step_no_writes_nothing(self, tmp_path):
        """Feature test: Linear 'n' → no PRAXIS_LINEAR_API_KEY, no api.linear.app domain."""
        _inp, _gp = _skip_all_inputs(tmp_path)
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert "PRAXIS_LINEAR_API_KEY" not in env_data
        domains = env_data.get("PRAXIS_ALLOWED_DOMAINS", "")
        assert "api.linear.app" not in domains

    def test_linear_domain_not_duplicated(self, tmp_path):
        """If api.linear.app already in PRAXIS_ALLOWED_DOMAINS, it is not duplicated."""
        # Pre-seed .env with api.linear.app already present
        env_file = tmp_path / ".env"
        env_file.write_text("PRAXIS_ALLOWED_DOMAINS=api.linear.app\n", encoding="utf-8")

        _inp = make_input("1", "y", "n", "n", "y", "n", "n", "n", "n", "", "n", "n")
        _gp = make_getpass("oauth_tok", "linear_key_abc")

        run_wizard(tmp_path, env_file=env_file, _input=_inp, _getpass=_gp)

        result = _read_env(env_file)
        domains = result.get("PRAXIS_ALLOWED_DOMAINS", "")
        # Should appear exactly once
        assert domains.count("api.linear.app") == 1


# ===========================================================================
# TestNotionStep
# ===========================================================================

class TestNotionStep:
    def test_notion_skipped_writes_nothing(self, tmp_path):
        """Skipping Notion writes no Notion keys."""
        _inp, _gp = _skip_all_inputs(tmp_path)
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert "PRAXIS_NOTION_TOKEN" not in env_data

    def test_notion_enabled_writes_token_and_domain(self, tmp_path):
        """Notion y → PRAXIS_NOTION_TOKEN written; api.notion.com appended to domains."""
        _inp = make_input("1", "y", "n", "n", "n", "y", "n", "n", "n", "", "n", "n")
        _gp = make_getpass("oauth_tok", "notion_secret_xyz")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("PRAXIS_NOTION_TOKEN") == "notion_secret_xyz"
        assert "api.notion.com" in env_data.get("PRAXIS_ALLOWED_DOMAINS", "")

    def test_notion_step_yes_writes_token_and_domain(self, tmp_path):
        """Feature test: Notion 'y' → PRAXIS_NOTION_TOKEN written; api.notion.com in domains."""
        _inp = make_input("1", "y", "n", "n", "n", "y", "n", "n", "n", "", "n", "n")
        _gp = make_getpass("oauth_tok", "notion_secret_feature")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("PRAXIS_NOTION_TOKEN") == "notion_secret_feature"
        assert "api.notion.com" in env_data.get("PRAXIS_ALLOWED_DOMAINS", "")

    def test_notion_domain_not_duplicated(self, tmp_path):
        """If api.notion.com already in PRAXIS_ALLOWED_DOMAINS, it is not duplicated."""
        env_file = tmp_path / ".env"
        env_file.write_text("PRAXIS_ALLOWED_DOMAINS=api.notion.com\n", encoding="utf-8")

        _inp = make_input("1", "y", "n", "n", "n", "y", "n", "n", "n", "", "n", "n")
        _gp = make_getpass("oauth_tok", "notion_secret_xyz")

        run_wizard(tmp_path, env_file=env_file, _input=_inp, _getpass=_gp)

        result = _read_env(env_file)
        domains = result.get("PRAXIS_ALLOWED_DOMAINS", "")
        assert domains.count("api.notion.com") == 1


# ===========================================================================
# TestCalendarStep
# ===========================================================================

class TestCalendarStep:
    def test_calendar_skipped_writes_nothing(self, tmp_path):
        """Skipping Calendar writes no Calendar keys."""
        _inp, _gp = _skip_all_inputs(tmp_path)
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert "PRAXIS_CALENDAR_URL" not in env_data

    def test_calendar_enabled_writes_url_and_domain(self, tmp_path):
        """Calendar y → PRAXIS_CALENDAR_URL written; calendar.google.com appended to domains."""
        cal_url = "https://calendar.google.com/calendar/ical/secret/basic.ics"
        # 1=runtime, y=ws, n=slack, n=github, n=linear, n=notion, y=calendar (url), n=web, n=email, ""=cost, n=brief, n=wiki
        _inp = make_input("1", "y", "n", "n", "n", "n", "y", cal_url, "n", "n", "", "n", "n")
        _gp = make_getpass("oauth_tok")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("PRAXIS_CALENDAR_URL") == cal_url
        assert "calendar.google.com" in env_data.get("PRAXIS_ALLOWED_DOMAINS", "")

    def test_calendar_domain_not_duplicated(self, tmp_path):
        """If calendar.google.com already present in PRAXIS_ALLOWED_DOMAINS, not duplicated."""
        env_file = tmp_path / ".env"
        env_file.write_text("PRAXIS_ALLOWED_DOMAINS=calendar.google.com\n", encoding="utf-8")

        cal_url = "https://calendar.google.com/calendar/ical/secret/basic.ics"
        _inp = make_input("1", "y", "n", "n", "n", "n", "y", cal_url, "n", "n", "", "n", "n")
        _gp = make_getpass("oauth_tok")

        run_wizard(tmp_path, env_file=env_file, _input=_inp, _getpass=_gp)

        result = _read_env(env_file)
        domains = result.get("PRAXIS_ALLOWED_DOMAINS", "")
        assert domains.count("calendar.google.com") == 1

    def test_calendar_step_yes_writes_url(self, tmp_path):
        """Feature test: Calendar 'y' → PRAXIS_CALENDAR_URL written; calendar.google.com in domains."""
        cal_url = "https://calendar.google.com/calendar/ical/feature_test/basic.ics"
        _inp = make_input("1", "y", "n", "n", "n", "n", "y", cal_url, "n", "n", "", "n", "n")
        _gp = make_getpass("oauth_tok")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        assert env_data.get("PRAXIS_CALENDAR_URL") == cal_url
        assert "calendar.google.com" in env_data.get("PRAXIS_ALLOWED_DOMAINS", "")

    def test_calendar_url_uses_input_not_getpass(self, tmp_path):
        """Feature test: PRAXIS_CALENDAR_URL is captured via _input (not _getpass)."""
        cal_url = "https://calendar.google.com/calendar/ical/input_test/basic.ics"
        # URL is in the _input sequence; _getpass only has the OAuth token
        _inp = make_input("1", "y", "n", "n", "n", "n", "y", cal_url, "n", "n", "", "n", "n")
        _gp = make_getpass("oauth_tok")
        env_data = {}

        def capture_write(env_file, data, mode):
            env_data.update(data)

        with patch("praxis.setup_wizard._write_env", side_effect=capture_write):
            run_wizard(tmp_path, env_file=tmp_path / ".env", _input=_inp, _getpass=_gp)

        # URL was provided via _input and must be captured correctly
        assert env_data.get("PRAXIS_CALENDAR_URL") == cal_url


# ===========================================================================
# TestAllowedDomainsAdditive
# ===========================================================================

class TestAllowedDomainsAdditive:
    """Feature tests for _append_allowed_domain — additive, no-duplicate behaviour."""

    def test_allowed_domains_additive_preserves_existing(self, tmp_path):
        """_append_allowed_domain preserves existing domains when adding a new one."""
        env_file = tmp_path / ".env"
        env_file.write_text("PRAXIS_ALLOWED_DOMAINS=existing.com\n", encoding="utf-8")

        _append_allowed_domain(env_file, "new.com")

        data = _read_env(env_file)
        domains = [d.strip() for d in data.get("PRAXIS_ALLOWED_DOMAINS", "").split(",") if d.strip()]
        assert "existing.com" in domains
        assert "new.com" in domains

    def test_allowed_domains_no_duplicate_on_rerun(self, tmp_path):
        """Calling _append_allowed_domain twice with the same domain adds it only once."""
        env_file = tmp_path / ".env"

        _append_allowed_domain(env_file, "api.linear.app")
        _append_allowed_domain(env_file, "api.linear.app")

        data = _read_env(env_file)
        domains = [d.strip() for d in data.get("PRAXIS_ALLOWED_DOMAINS", "").split(",") if d.strip()]
        assert domains.count("api.linear.app") == 1


# ===========================================================================
# TestMainSetupMode
# ===========================================================================

class TestMainSetupMode:
    """Tests for praxis/__main__.py --setup plumbing."""

    def test_parse_mode_setup(self):
        from praxis.__main__ import _parse_mode
        assert _parse_mode(["p", "--setup"]) == "setup"

    def test_parse_mode_setup_before_interactive(self):
        from praxis.__main__ import _parse_mode
        # --setup flag present alongside a positional; --setup wins
        assert _parse_mode(["p", "--setup", "some_message"]) == "setup"

    def test_main_setup_calls_run_wizard(self, tmp_path):
        """main() with --setup should call run_wizard exactly once."""
        import os
        from praxis.__main__ import main

        # env_file does NOT exist — no "already exists" prompt needed
        env_vars = {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}

        with (
            patch("sys.argv", ["praxis", "--setup"]),
            patch.dict(os.environ, env_vars, clear=False),
            patch("praxis.setup_wizard.run_wizard") as mock_wizard,
        ):
            main()

        mock_wizard.assert_called_once()

    def test_main_setup_cancel_when_existing_env(self, tmp_path, capsys):
        """When .env exists and user answers 'c', run_wizard must NOT be called."""
        import os
        from praxis.__main__ import main

        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=yes\n", encoding="utf-8")

        env_vars = {"PRAXIS_WORKSPACE_ROOT": str(tmp_path)}

        with (
            patch("sys.argv", ["praxis", "--setup"]),
            patch.dict(os.environ, env_vars, clear=False),
            patch("builtins.input", return_value="c"),
            patch("praxis.setup_wizard.run_wizard") as mock_wizard,
        ):
            main()

        mock_wizard.assert_not_called()
        out = capsys.readouterr().out
        assert "cancelled" in out.lower() or "Setup cancelled" in out
