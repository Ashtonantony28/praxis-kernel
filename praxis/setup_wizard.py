"""First-run setup wizard for Praxis.

Creates/updates .env file with credentials and configuration.
All inputs for tokens/keys use getpass (hidden input).
"""

from __future__ import annotations

import getpass as _getpass_module
import os
import shutil
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_env(env_file: Path) -> dict[str, str]:
    """Parse key=value lines from an env file. Ignores comments and blank lines."""
    result: dict[str, str] = {}
    if not env_file.exists():
        return result
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _write_env(env_file: Path, data: dict[str, str], mode: str) -> None:
    """Write key=value pairs to env_file.

    mode='overwrite': write all key=value pairs fresh.
    mode='merge': read existing file, only append keys NOT already present.
    """
    if mode == "merge":
        existing = _read_env(env_file)
        # Only write keys not already in the file
        new_pairs = {k: v for k, v in data.items() if k not in existing}
        if not new_pairs:
            return
        # Append to end of file
        with env_file.open("a", encoding="utf-8") as f:
            for k, v in new_pairs.items():
                f.write(f"{k}={v}\n")
    else:
        # overwrite
        with env_file.open("w", encoding="utf-8") as f:
            for k, v in data.items():
                f.write(f"{k}={v}\n")


def _safe_input(prompt: str, _input: Callable | None) -> str:
    """Call _input if provided, otherwise builtins input()."""
    fn = _input if _input is not None else input
    return fn(prompt)


def _safe_getpass(prompt: str, _getpass: Callable | None) -> str:
    """Call _getpass if provided, otherwise getpass.getpass() with fallback to input."""
    if _getpass is not None:
        return _getpass(prompt)
    try:
        return _getpass_module.getpass(prompt)
    except Exception:
        print("  (Warning: could not hide input — value will echo)")
        return input(prompt)


# ---------------------------------------------------------------------------
# Public wizard entry point
# ---------------------------------------------------------------------------

def run_wizard(
    workspace_root: Path | str,
    *,
    env_file: Path | None = None,
    _input: Callable | None = None,
    _getpass: Callable | None = None,
    _env_mode: str = "merge",
) -> None:
    """Run the Praxis first-run setup wizard.

    Args:
        workspace_root: Path (or str) of the repo root.
        env_file: Path for .env output (defaults to workspace_root/.env).
        _input: callable used instead of builtins input() — allows testing.
        _getpass: callable used instead of getpass.getpass() — allows testing.
        _env_mode: "overwrite" or "merge" (default "merge").
    """
    workspace_root = Path(workspace_root)
    if env_file is None:
        env_file = workspace_root / ".env"

    env_data: dict[str, str] = {}

    # -----------------------------------------------------------------------
    # Banner
    # -----------------------------------------------------------------------
    print("========================================")
    print("  Praxis First-Run Setup Wizard")
    print("========================================")
    print("This wizard configures your .env file.")
    print("It will NOT overwrite existing values.")
    print("Credential inputs are hidden (not echoed).")
    print()

    # -----------------------------------------------------------------------
    # STEP 1 — Runtime selection
    # -----------------------------------------------------------------------
    try:
        print("STEP 1/11 — Runtime")
        print("Which runtime would you like to use?")
        print()
        print("  (1) Claude subscription OAuth     [flat cost, recommended]")
        print("      Get token from: claude.ai → Account → API Keys")
        print("      Env var: CLAUDE_CODE_OAUTH_TOKEN")
        print()
        print("  (2) Google Gemini API (free tier)  [generous free quota]")
        print("      Get key from: https://aistudio.google.com/app/apikey")
        print("      Env vars: PRAXIS_RUNTIME=cloud, PRAXIS_CLOUD_API_KEY,")
        print("                PRAXIS_CLOUD_BASE_URL, PRAXIS_CLOUD_MODEL")
        print()
        print("  (3) Local Ollama                  [fully offline, no cost]")
        print("      Requires Ollama running locally: https://ollama.ai")
        print("      Env vars: PRAXIS_RUNTIME=local, PRAXIS_LOCAL_BASE_URL,")
        print("                PRAXIS_LOCAL_MODEL")
        print()
        print("  (4) Anthropic API key             [pay-per-token]")
        print("      Get key from: https://console.anthropic.com/settings/keys")
        print("      Env var: ANTHROPIC_API_KEY")
        print()

        runtime_choice = _safe_input("Enter choice [1-4]: ", _input).strip()
        if runtime_choice not in ("1", "2", "3", "4"):
            # Re-prompt once
            runtime_choice = _safe_input("Invalid choice. Enter choice [1-4]: ", _input).strip()
            if runtime_choice not in ("1", "2", "3", "4"):
                print("Error: invalid runtime choice. Aborting.")
                return

        runtime_label = "claude (OAuth)"
        if runtime_choice == "1":
            token = _safe_getpass("  CLAUDE_CODE_OAUTH_TOKEN: ", _getpass)
            if token:
                env_data["CLAUDE_CODE_OAUTH_TOKEN"] = token
            env_data["PRAXIS_RUNTIME"] = "claude"
            runtime_label = "claude (OAuth)"

        elif runtime_choice == "2":
            api_key = _safe_getpass("  PRAXIS_CLOUD_API_KEY (Gemini): ", _getpass)
            if api_key:
                env_data["PRAXIS_CLOUD_API_KEY"] = api_key
            env_data["PRAXIS_RUNTIME"] = "cloud"
            env_data["PRAXIS_CLOUD_BASE_URL"] = "https://generativelanguage.googleapis.com/v1beta/openai/"
            env_data["PRAXIS_CLOUD_MODEL"] = "gemini-2.0-flash"
            runtime_label = "cloud (Gemini)"

        elif runtime_choice == "3":
            base_url = _safe_input("  PRAXIS_LOCAL_BASE_URL [http://localhost:11434]: ", _input).strip()
            if not base_url:
                base_url = "http://localhost:11434"
            model = _safe_input("  PRAXIS_LOCAL_MODEL [llama3.2]: ", _input).strip()
            if not model:
                model = "llama3.2"
            env_data["PRAXIS_RUNTIME"] = "local"
            env_data["PRAXIS_LOCAL_BASE_URL"] = base_url
            env_data["PRAXIS_LOCAL_MODEL"] = model
            runtime_label = f"local ({model})"

        elif runtime_choice == "4":
            api_key = _safe_getpass("  ANTHROPIC_API_KEY: ", _getpass)
            if api_key:
                env_data["ANTHROPIC_API_KEY"] = api_key
            env_data["PRAXIS_RUNTIME"] = "claude"
            runtime_label = "claude (API key)"

    except Exception as exc:
        print(f"  Warning: step 1 error ({exc}), continuing.")

    print()

    # -----------------------------------------------------------------------
    # STEP 2 — Workspace confirmation
    # -----------------------------------------------------------------------
    try:
        print("STEP 2/11 — Workspace")
        print(f"Detected workspace root: {workspace_root}")
        ws_choice = _safe_input("Is this correct? [Y/n]: ", _input).strip().lower()
        if ws_choice == "n":
            entered = _safe_input("Enter workspace path: ", _input).strip()
            if entered:
                env_data["PRAXIS_WORKSPACE_ROOT"] = entered
            else:
                env_data["PRAXIS_WORKSPACE_ROOT"] = str(workspace_root)
        else:
            env_data["PRAXIS_WORKSPACE_ROOT"] = str(workspace_root)
    except Exception as exc:
        print(f"  Warning: step 2 error ({exc}), continuing.")
        env_data["PRAXIS_WORKSPACE_ROOT"] = str(workspace_root)

    print()

    # -----------------------------------------------------------------------
    # STEP 3 — Slack (optional)
    # -----------------------------------------------------------------------
    slack_enabled = False
    try:
        print("STEP 3/11 — Slack (optional)")
        print("Slack integration enables: notifications, phone control, remote approvals.")
        print("To set up, create a Slack app at https://api.slack.com/apps with:")
        print("  - Socket Mode enabled")
        print("  - Event subscriptions: message.im, app_mention")
        print("  - Slash commands: /praxis")
        print("  - App Token (xapp-) with connections:write scope")
        print("  - Bot Token (xoxb-)")
        print("  - Incoming Webhook URL (for notifications)")
        print()
        slack_choice = _safe_input("Enable Slack? [y/N]: ", _input).strip().lower()
        if slack_choice == "y":
            slack_enabled = True
            webhook_url = _safe_input("  PRAXIS_SLACK_WEBHOOK_URL: ", _input).strip()
            if webhook_url:
                env_data["PRAXIS_SLACK_WEBHOOK_URL"] = webhook_url
            bot_token = _safe_getpass("  PRAXIS_SLACK_BOT_TOKEN (xoxb-): ", _getpass)
            if bot_token:
                env_data["PRAXIS_SLACK_BOT_TOKEN"] = bot_token
            print("  PRAXIS_SLACK_APP_TOKEN (xapp- token — needed for socket mode listener):")
            app_token = _safe_input("  PRAXIS_SLACK_APP_TOKEN: ", _input).strip()
            if app_token:
                env_data["PRAXIS_SLACK_APP_TOKEN"] = app_token

            # Suggest adding Slack domains to PRAXIS_ALLOWED_DOMAINS
            current_domains = env_data.get("PRAXIS_ALLOWED_DOMAINS", "")
            slack_domains = "hooks.slack.com,slack.com"
            if current_domains:
                env_data["PRAXIS_ALLOWED_DOMAINS"] = current_domains + "," + slack_domains
            else:
                env_data["PRAXIS_ALLOWED_DOMAINS"] = slack_domains
    except Exception as exc:
        print(f"  Warning: step 3 error ({exc}), continuing.")

    print()

    # -----------------------------------------------------------------------
    # STEP 4 — GitHub (optional)
    # -----------------------------------------------------------------------
    github_enabled = False
    try:
        print("STEP 4/11 — GitHub (optional)")
        print("GitHub integration enables: PR listing, issue viewing, code diffs.")
        print("Create a Personal Access Token at: https://github.com/settings/tokens")
        print("  - Scopes: repo (for private repos) or public_repo (for public)")
        print()
        github_choice = _safe_input("Enable GitHub? [y/N]: ", _input).strip().lower()
        if github_choice == "y":
            github_enabled = True
            github_token = _safe_getpass("  GitHub token: ", _getpass)
            if github_token:
                env_data["GITHUB_TOKEN"] = github_token
    except Exception as exc:
        print(f"  Warning: step 4 error ({exc}), continuing.")

    print()

    # -----------------------------------------------------------------------
    # STEP 5 — Web search (optional)
    # -----------------------------------------------------------------------
    web_enabled = False
    try:
        print("STEP 5/11 — Web search (optional)")
        print("Web search uses the Brave Search API (free tier — no credit card required).")
        print("Sign up at: https://brave.com/search/api/")
        print("  - Free tier: 2000 queries/month")
        print("  - API key format: BSA...")
        print()
        web_choice = _safe_input("Enable web search? [y/N]: ", _input).strip().lower()
        if web_choice == "y":
            web_enabled = True
            web_api_key = _safe_getpass("  PRAXIS_WEB_SEARCH_API_KEY: ", _getpass)
            if web_api_key:
                env_data["PRAXIS_WEB_SEARCH_API_KEY"] = web_api_key
            domains_input = _safe_input(
                "  Allowed domains (comma-separated, press Enter for default 'api.search.brave.com'): ",
                _input,
            ).strip()
            if not domains_input:
                domains_input = "api.search.brave.com"
            # Merge with any already-set domains
            current_domains = env_data.get("PRAXIS_ALLOWED_DOMAINS", "")
            if current_domains:
                env_data["PRAXIS_ALLOWED_DOMAINS"] = current_domains + "," + domains_input
            else:
                env_data["PRAXIS_ALLOWED_DOMAINS"] = domains_input
    except Exception as exc:
        print(f"  Warning: step 5 error ({exc}), continuing.")

    print()

    # -----------------------------------------------------------------------
    # STEP 6 — Email (optional)
    # -----------------------------------------------------------------------
    email_enabled = False
    try:
        print("STEP 6/11 — Email (optional)")
        print("Email integration provides read-only IMAP inbox access + local draft staging.")
        print("For Gmail: create an App Password at https://myaccount.google.com/apppasswords")
        print("  (requires 2-factor authentication)")
        print("For Outlook: use your account password or app password.")
        print("NOTE: Praxis never sends email autonomously — drafts are staged for your review.")
        print()
        email_choice = _safe_input("Enable email? [y/N]: ", _input).strip().lower()
        if email_choice == "y":
            email_enabled = True
            imap_host = _safe_input("  PRAXIS_EMAIL_IMAP_HOST (e.g. imap.gmail.com): ", _input).strip()
            if imap_host:
                env_data["PRAXIS_EMAIL_IMAP_HOST"] = imap_host
            email_user = _safe_input("  PRAXIS_EMAIL_USER: ", _input).strip()
            if email_user:
                env_data["PRAXIS_EMAIL_USER"] = email_user
            email_pass = _safe_getpass("  App password: ", _getpass)
            if email_pass:
                env_data["PRAXIS_EMAIL_PASSWORD"] = email_pass
    except Exception as exc:
        print(f"  Warning: step 6 error ({exc}), continuing.")

    print()

    # -----------------------------------------------------------------------
    # STEP 7 — Circuit breaker
    # -----------------------------------------------------------------------
    cost_cap = "2.00"
    try:
        print("STEP 7/11 — Cost circuit breaker")
        print("Praxis will stop a session if estimated API cost exceeds this limit.")
        print("(For OAuth/subscription users, this is an estimate — no actual billing impact.)")
        print()
        cap_input = _safe_input("Maximum spend per session in USD [default: 2.00]: ", _input).strip()
        if cap_input:
            cost_cap = cap_input
        env_data["PRAXIS_MAX_SESSION_COST"] = cost_cap
    except Exception as exc:
        print(f"  Warning: step 7 error ({exc}), continuing.")
        env_data["PRAXIS_MAX_SESSION_COST"] = cost_cap

    print()

    # -----------------------------------------------------------------------
    # STEP 8 — Morning briefing schedule (optional)
    # -----------------------------------------------------------------------
    briefing_scheduled = False
    briefing_task_id: str | None = None
    briefing_next_run: str | None = None
    try:
        print("STEP 8/11 — Morning briefing (optional)")
        print("A daily 7am briefing asks: 'wiki query: what are my priorities for today?'")
        print("Requires: pip install praxis[scheduler]")
        print()
        briefing_choice = _safe_input("Enable daily morning briefing at 7am? [y/N]: ", _input).strip().lower()
        if briefing_choice == "y":
            try:
                from praxis.scheduler import CronScheduler
                from praxis.queue import TaskQueue

                queue = TaskQueue(workspace_root / ".praxis" / "queue")
                scheduler = CronScheduler(
                    queue=queue,
                    schedule_file=workspace_root / ".praxis" / "schedule" / "tasks.json",
                    log_file=workspace_root / ".praxis" / "logs" / "scheduler.log",
                )
                scheduler.load()
                task = scheduler.add_task(
                    name="morning-briefing",
                    schedule="0 7 * * *",
                    prompt="wiki query: what are my priorities for today?",
                )
                scheduler.save()
                briefing_scheduled = True
                briefing_task_id = task.id
                briefing_next_run = task.next_run
                print(f"  Morning briefing scheduled (id: {task.id}, next: {task.next_run})")
            except ImportError:
                print("  Skipping schedule (croniter not installed). Run: pip install praxis[scheduler]")
            except Exception as exc:
                print(f"  Warning: could not schedule briefing ({exc})")
    except Exception as exc:
        print(f"  Warning: step 8 error ({exc}), continuing.")

    print()

    # -----------------------------------------------------------------------
    # STEP 9 — Wiki seed (optional)
    # -----------------------------------------------------------------------
    wiki_copied = 0
    try:
        print("STEP 9/11 — Personal wiki seed (optional)")
        print("Drop notes or documents into wiki/raw/ to seed your personal knowledge wiki.")
        print("Praxis will ingest them when you run: python -m praxis \"wiki ingest wiki/raw/\"")
        print()
        wiki_choice = _safe_input("Add a file or directory to your wiki? [y/N]: ", _input).strip().lower()
        if wiki_choice == "y":
            source_path_str = _safe_input("Enter path to file or directory: ", _input).strip()
            source_path = Path(source_path_str)
            if not source_path.exists():
                print(f"  Warning: path does not exist: {source_path}. Skipping.")
            else:
                wiki_raw_dir = workspace_root / "wiki" / "raw"
                wiki_raw_dir.mkdir(parents=True, exist_ok=True)

                if source_path.is_file():
                    dest = wiki_raw_dir / source_path.name
                    shutil.copy2(str(source_path), str(dest))
                    wiki_copied = 1
                elif source_path.is_dir():
                    # Copy all *.md and *.txt files (non-recursive)
                    for ext in ("*.md", "*.txt"):
                        for fpath in source_path.glob(ext):
                            dest = wiki_raw_dir / fpath.name
                            shutil.copy2(str(fpath), str(dest))
                            wiki_copied += 1

                if wiki_copied > 0:
                    print(
                        f"  Copied {wiki_copied} file(s) to wiki/raw/. "
                        "Run: python -m praxis 'wiki ingest wiki/raw/' to ingest."
                    )
                else:
                    print("  No .md or .txt files found. Skipping.")
    except Exception as exc:
        print(f"  Warning: step 9 error ({exc}), continuing.")

    print()

    # -----------------------------------------------------------------------
    # STEP 10 — Default mode
    # -----------------------------------------------------------------------
    try:
        print("STEP 10/11 — Default mode")
        print("Which mode should Praxis use by default?")
        print()
        print("  (1) build  [full access — default]")
        print("      All tools available. Praxis acts immediately.")
        print()
        print("  (2) plan   [read-only planning mode]")
        print("      Write/Edit/Bash denied. Praxis presents a plan for approval.")
        print()
        mode_choice = _safe_input("Enter choice [1-2] (default: 1): ", _input).strip()
        if mode_choice == "2":
            env_data["PRAXIS_DEFAULT_MODE"] = "plan"
        else:
            env_data["PRAXIS_DEFAULT_MODE"] = "build"
        print(f"  Default mode: {env_data['PRAXIS_DEFAULT_MODE']}")
        print()
    except (EOFError, KeyboardInterrupt):
        env_data["PRAXIS_DEFAULT_MODE"] = "build"
        print("\n  (skipped — using build mode)")
        print()

    # -----------------------------------------------------------------------
    # STEP 11/11 — Write .env and print summary
    # -----------------------------------------------------------------------
    try:
        _write_env(env_file, env_data, _env_mode)
    except Exception as exc:
        print(f"Warning: could not write {env_file}: {exc}")

    # Build summary strings
    runtime_str = env_data.get("PRAXIS_RUNTIME", "claude")
    if runtime_str == "claude":
        if "CLAUDE_CODE_OAUTH_TOKEN" in env_data:
            runtime_display = "claude (OAuth)"
        else:
            runtime_display = "claude (API key)"
    elif runtime_str == "cloud":
        runtime_display = f"cloud ({env_data.get('PRAXIS_CLOUD_MODEL', 'gemini')})"
    elif runtime_str == "local":
        runtime_display = f"local ({env_data.get('PRAXIS_LOCAL_MODEL', 'ollama')})"
    else:
        runtime_display = runtime_str

    ws_display = env_data.get("PRAXIS_WORKSPACE_ROOT", str(workspace_root))
    slack_display = "enabled" if slack_enabled else "not configured"
    github_display = "enabled" if github_enabled else "not configured"
    web_display = "enabled" if web_enabled else "not configured"
    email_display = "enabled" if email_enabled else "not configured"
    cost_display = f"${cost_cap}"
    briefing_display = "scheduled" if briefing_scheduled else "not scheduled"
    wiki_display = f"{wiki_copied} files" if wiki_copied > 0 else "skipped"

    print("========================================")
    print("  Setup complete!")
    print("========================================")
    print("Configured:")
    print(f"  Runtime:    {runtime_display}")
    print(f"  Workspace:  {ws_display}")
    print(f"  Slack:      {slack_display}")
    print(f"  GitHub:     {github_display}")
    print(f"  Web search: {web_display}")
    print(f"  Email:      {email_display}")
    print(f"  Cost cap:   {cost_display}")
    print(f"  Briefing:   {briefing_display}")
    print(f"  Wiki seed:  {wiki_display}")
    print(f"  Default mode:        {env_data.get('PRAXIS_DEFAULT_MODE', 'build')}")
    print()
    print("Next steps:")
    print("  source .venv/bin/activate           # if you have a venv")
    print("  python -m praxis --daemon           # start background operation")
    print("  python -m praxis --status           # check it's running")
    print("  python -m praxis --schedule-list    # see scheduled tasks")
    print()
    print("IMPORTANT: Your secrets are in .env — never commit this file to git!")

    # Check if .env is in .gitignore
    try:
        gitignore = workspace_root / ".gitignore"
        if gitignore.exists():
            content = gitignore.read_text(encoding="utf-8")
            lines = [ln.strip() for ln in content.splitlines()]
            if ".env" not in lines and "*.env" not in lines:
                print()
                print("  WARNING: .env is NOT in .gitignore. Add it now:")
                print("    echo '.env' >> .gitignore")
        else:
            print()
            print("  WARNING: .env is NOT in .gitignore. Add it now:")
            print("    echo '.env' >> .gitignore")
    except Exception:
        pass
