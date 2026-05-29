"""Entry point: python -m praxis"""

from __future__ import annotations

import sys

from .config import Config
from .convergence import ConvergenceConfig
from .orchestrator import Orchestrator
from .runtime import ClaudeCodeRuntime, LocalRuntime, OpenAICloudRuntime
from .runtime.base import Runtime


def _create_runtimes(conv: ConvergenceConfig):
    """Create all runtimes needed by the convergence config.

    Returns (default_runtime, overrides_dict).
    """
    runtimes: dict[str, Runtime] = {}

    if conv.needs_claude():
        rt = ClaudeCodeRuntime.from_env()
        sys.stderr.write(f"[praxis] runtime claude: auth={rt.auth_method}\n")
        runtimes["claude"] = rt

    if conv.needs_local():
        rt = LocalRuntime.from_env()
        sys.stderr.write(
            f"[praxis] runtime local: {rt.base_url}, "
            f"model={rt.default_model}\n"
        )
        runtimes["local"] = rt

    if conv.needs_cloud():
        rt = OpenAICloudRuntime.from_env()
        sys.stderr.write(
            f"[praxis] runtime cloud: {rt.base_url}, "
            f"model={rt.default_model}\n"
        )
        runtimes["cloud"] = rt

    default = runtimes[conv.default_runtime]
    overrides = {
        name: runtimes[rt_name]
        for name, rt_name in conv.overrides.items()
        if rt_name != conv.default_runtime
    }

    return default, overrides


def _run_approve(staging_file: "Path") -> None:
    """Interactive approval loop for staged external actions."""
    import json as _json
    import os as _os
    from pathlib import Path as _Path

    if not staging_file.exists():
        print("No pending actions. (Staging file not found.)")
        return

    # Load all entries
    entries = []
    with staging_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(_json.loads(line))
                except _json.JSONDecodeError:
                    pass

    pending = [e for e in entries if e.get("status") == "pending"]
    if not pending:
        print("No pending actions.")
        return

    print(f"\nPending external actions ({len(pending)} total):\n")
    for i, entry in enumerate(pending, 1):
        print(f"{i}. [{entry['provider']}] {entry['action']}")
        params = entry.get("params", {})
        for k, v in list(params.items())[:4]:  # show up to 4 params
            print(f"   {k}: {str(v)[:80]}")
        print(f"   Queued: {entry.get('queued_at', 'unknown')}")
        print()

        try:
            choice = input("   Approve? [y/N/s(kip)]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            break

        if choice == "y":
            result = _execute_approved_action(entry)
            print(f"   → {result}")
            entry["status"] = "approved"
            entry["executed_result"] = result
        elif choice == "s":
            print("   → Skipped (left pending).")
        else:
            print("   → Rejected.")
            entry["status"] = "rejected"
        print()

    # Rewrite the staging file with updated statuses
    with staging_file.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(_json.dumps(e) + "\n")

    approved = sum(1 for e in pending if e.get("status") == "approved")
    rejected = sum(1 for e in pending if e.get("status") == "rejected")
    skipped = sum(1 for e in pending if e.get("status") == "pending")
    print(f"Done. {approved} approved, {rejected} rejected, {skipped} left pending.")


def _run_list_staged(workspace_root: "Path") -> None:
    """List all pending staged items across .praxis/staging/ — read-only, no prompts."""
    import json as _json
    from pathlib import Path as _Path

    staging = workspace_root / ".praxis" / "staging"
    if not staging.exists():
        print("No staged items. (.praxis/staging/ not found)")
        return

    found_any = False

    # 1. External actions (Notion + Linear write-escalate)
    actions_file = staging / "external_actions.jsonl"
    if actions_file.exists():
        pending = []
        for line in actions_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entry = _json.loads(line)
                    if entry.get("status") == "pending":
                        pending.append(entry)
                except _json.JSONDecodeError:
                    pass
        if pending:
            found_any = True
            print(f"\nExternal actions ({len(pending)} pending):")
            for e in pending:
                print(f"  [{e.get('provider','?')}] {e.get('action','?')}  queued={e.get('queued_at','?')[:19]}")

    # 2. Slack staged messages
    slack_msgs = staging / "slack" / "messages"
    if slack_msgs.exists():
        msg_files = list(slack_msgs.glob("*.json"))
        if msg_files:
            found_any = True
            print(f"\nSlack staged messages: {len(msg_files)}")
            for f in msg_files[:5]:
                print(f"  {f.name}")
            if len(msg_files) > 5:
                print(f"  ... and {len(msg_files) - 5} more")

    # 3. Slack staged approvals
    slack_approvals = staging / "slack" / "approvals"
    if slack_approvals.exists():
        approval_files = list(slack_approvals.glob("*.json"))
        if approval_files:
            found_any = True
            print(f"\nSlack staged approvals: {len(approval_files)}")
            for f in approval_files[:5]:
                print(f"  {f.name}")

    # 4. Email drafts
    drafts_dir = staging / "drafts"
    if drafts_dir.exists():
        drafts = list(drafts_dir.glob("*.eml"))
        if drafts:
            found_any = True
            print(f"\nEmail drafts: {len(drafts)}")
            for f in drafts[:5]:
                print(f"  {f.name}")

    # 5. Calendar event proposals
    events_dir = staging / "events"
    if events_dir.exists():
        events = list(events_dir.glob("*.ics"))
        if events:
            found_any = True
            print(f"\nCalendar event proposals: {len(events)}")
            for f in events[:5]:
                print(f"  {f.name}")

    # 6. Wiki update proposals (from pull_linear_updates)
    wiki_updates_file = staging / "wiki_updates.jsonl"
    if wiki_updates_file.exists():
        wiki_pending = []
        for line in wiki_updates_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entry = _json.loads(line)
                    if entry.get("status") == "pending":
                        wiki_pending.append(entry)
                except _json.JSONDecodeError:
                    pass
        if wiki_pending:
            found_any = True
            print(f"\nWiki update proposals ({len(wiki_pending)} pending):")
            for e in wiki_pending[:5]:
                slug = e.get("page_slug", "?")
                issue = e.get("linear_issue_id", "?")
                state = e.get("current_state", "?")
                queued = e.get("queued_at", "?")[:19]
                print(f"  [{slug}] issue={issue} state={state}  queued={queued}")
            if len(wiki_pending) > 5:
                print(f"  ... and {len(wiki_pending) - 5} more")

    if not found_any:
        print("No staged items.")


def _execute_approved_action(entry: dict) -> str:
    """Execute a single approved staged action. Returns result string."""
    import json as _json
    import os as _os
    from urllib.parse import urlparse

    provider = entry.get("provider", "")
    action = entry.get("action", "")
    params = entry.get("params", {})

    # Domain allowlist check
    allowed_raw = _os.environ.get("PRAXIS_ALLOWED_DOMAINS", "")
    allowed = frozenset(d.strip() for d in allowed_raw.split(",") if d.strip())

    def check_domain(domain: str) -> str | None:
        if not allowed:
            return f"Domain '{domain}' not in PRAXIS_ALLOWED_DOMAINS — add it to execute this action."
        if domain not in allowed:
            return f"Domain '{domain}' not in PRAXIS_ALLOWED_DOMAINS — add it to execute this action."
        return None

    if provider == "notion":
        token = _os.environ.get("PRAXIS_NOTION_TOKEN", "")
        if not token:
            return "Error: PRAXIS_NOTION_TOKEN not set."
        err = check_domain("api.notion.com")
        if err:
            return err
        return _notion_execute(action, params, token)

    elif provider == "linear":
        api_key = _os.environ.get("PRAXIS_LINEAR_API_KEY", "")
        if not api_key:
            return "Error: PRAXIS_LINEAR_API_KEY not set."
        err = check_domain("api.linear.app")
        if err:
            return err
        return _linear_execute(action, params, api_key)

    return f"Error: unknown provider '{provider}'."


def _notion_execute(action: str, params: dict, token: str) -> str:
    """Execute an approved Notion write action."""
    import json as _json
    import urllib.request
    import urllib.error

    base = "https://api.notion.com/v1"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    try:
        if action == "create_page":
            parent_id = params.get("parent_id", "")
            title = params.get("title", "Untitled")
            body = {
                "parent": {"page_id": parent_id},
                "properties": {
                    "title": {"title": [{"text": {"content": title}}]}
                },
            }
            content = params.get("content", "")
            if content:
                body["children"] = [
                    {"object": "block", "type": "paragraph",
                     "paragraph": {"rich_text": [{"type": "text", "text": {"content": content[:2000]}}]}}
                ]
            data = _json.dumps(body).encode()
            req = urllib.request.Request(f"{base}/pages", data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return f"Created page: {resp.read().decode()[:200]}"

        elif action == "update_page":
            page_id = params.get("page_id", "")
            props = params.get("properties", {})
            data = _json.dumps({"properties": props}).encode()
            req = urllib.request.Request(f"{base}/pages/{page_id}", data=data, headers=headers, method="PATCH")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return f"Updated page: {resp.read().decode()[:200]}"

        elif action == "append_block":
            block_id = params.get("block_id", "")
            content = params.get("content", "")
            body = {"children": [
                {"object": "block", "type": "paragraph",
                 "paragraph": {"rich_text": [{"type": "text", "text": {"content": content[:2000]}}]}}
            ]}
            data = _json.dumps(body).encode()
            req = urllib.request.Request(f"{base}/blocks/{block_id}/children", data=data, headers=headers, method="PATCH")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return f"Appended block: {resp.read().decode()[:200]}"

        return f"notion: unknown action '{action}'."
    except urllib.error.URLError as exc:
        return f"notion API error: {exc}"
    except Exception as exc:
        return f"notion execute failed: {exc}"


def _linear_execute(action: str, params: dict, api_key: str) -> str:
    """Execute an approved Linear write action via GraphQL."""
    import json as _json
    import urllib.request
    import urllib.error

    url = "https://api.linear.app/graphql"
    headers = {"Authorization": api_key, "Content-Type": "application/json"}

    mutations = {
        "create_issue": (
            "mutation CreateIssue($teamId: String!, $title: String!, $description: String) { "
            "  issueCreate(input: {teamId: $teamId, title: $title, description: $description}) { "
            "    success issue { id title } } }",
            {"teamId": params.get("team_id", ""), "title": params.get("title", ""),
             "description": params.get("description", "")},
        ),
        "update_issue": (
            "mutation UpdateIssue($id: String!, $updates: IssueUpdateInput!) { "
            "  issueUpdate(id: $id, input: $updates) { success issue { id title } } }",
            {"id": params.get("issue_id", ""), "updates": params.get("updates", {})},
        ),
        "add_comment": (
            "mutation AddComment($issueId: String!, $body: String!) { "
            "  commentCreate(input: {issueId: $issueId, body: $body}) { "
            "    success comment { id body } } }",
            {"issueId": params.get("issue_id", ""), "body": params.get("body", "")},
        ),
    }

    if action not in mutations:
        return f"linear: unknown action '{action}'."

    query, variables = mutations[action]
    try:
        data = _json.dumps({"query": query, "variables": variables}).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return f"linear {action}: {resp.read().decode()[:200]}"
    except urllib.error.URLError as exc:
        return f"linear API error: {exc}"
    except Exception as exc:
        return f"linear execute failed: {exc}"


def _run_credential_check(workspace_root: "Path") -> None:
    """Check credential status at startup — never blocks execution."""
    import sys as _sys
    try:
        from .runtime.auth import (
            build_credential_inventory,
            write_credential_inventory,
            warn_near_expiry,
        )
        inventory = build_credential_inventory()
        try:
            write_credential_inventory(workspace_root, inventory)
        except Exception:
            pass  # never block on inventory write failure

        # Print warnings to stderr
        for msg in warn_near_expiry(inventory):
            _sys.stderr.write(msg + "\n")

        # If near-expiry detected and Slack webhook is configured, try to notify
        near_expiry_creds = [
            c for c in inventory.get("credentials", [])
            if c.get("near_expiry")
        ]
        if near_expiry_creds:
            import os as _os
            webhook_url = _os.environ.get("PRAXIS_SLACK_WEBHOOK_URL", "")
            if webhook_url:
                try:
                    from .integrations.slack import execute_slack
                    from .config import Config as _Config
                    _cfg = _Config.from_env()
                    names = ", ".join(c["name"] for c in near_expiry_creds)
                    hours = min(c["expires_in_hours"] for c in near_expiry_creds)
                    execute_slack(
                        {"action": "notify", "message": f"[Praxis] Credential near expiry: {names} (within {hours:.1f}h). Please refresh."},
                        _cfg,
                    )
                except Exception:
                    pass  # never block on Slack failure
    except Exception:
        pass  # auth check must never crash startup


def _make_scheduler(config: "Config") -> "CronScheduler":
    """Build a CronScheduler with the standard Praxis paths. Exits 1 if croniter missing."""
    try:
        from .scheduler import CronScheduler
    except ImportError:
        print("[praxis] croniter not installed. Run: pip install praxis[scheduler]", file=sys.stderr)
        raise SystemExit(1)
    from .queue import TaskQueue
    queue = TaskQueue(config.workspace_root / ".praxis" / "queue")
    scheduler = CronScheduler(
        queue=queue,
        schedule_file=config.workspace_root / ".praxis" / "schedule" / "tasks.json",
        log_file=config.workspace_root / ".praxis" / "logs" / "scheduler.log",
    )
    scheduler.load()
    return scheduler


def _parse_mode(argv: list[str]) -> str:
    """Determine execution mode from argv flags."""
    if "--daemon" in argv:
        return "daemon"
    if "--stop" in argv:
        return "stop"
    if "--status" in argv:
        return "status"
    if "--queue" in argv:
        return "queue"
    if "--slack-listen" in argv:
        return "slack"
    if "--mcp" in argv:
        return "mcp"
    if "--approve" in argv:
        return "approve"
    if "--list-staged" in argv:
        return "list_staged"
    if "--schedule-add" in argv:
        return "schedule_add"
    if "--schedule-list" in argv:
        return "schedule_list"
    if "--schedule-enable" in argv:
        return "schedule_enable"
    if "--schedule-disable" in argv:
        return "schedule_disable"
    if "--schedule-remove" in argv:
        return "schedule_remove"
    if "--wiki-sync-notion" in argv:
        return "wiki_sync_notion"
    if "--wiki-sync-linear" in argv:
        return "wiki_sync_linear"
    if "--wiki-link-issue" in argv:
        return "wiki_link_issue"
    if "--setup" in argv:
        return "setup"
    if "--config" in argv:
        return "config"
    return "interactive"


def main() -> None:
    try:
        mode = _parse_mode(sys.argv)

        if mode == "interactive":
            config = Config.from_env()
            _run_credential_check(config.workspace_root)
            conv = ConvergenceConfig.load(config.workspace_root)
            default_runtime, runtime_overrides = _create_runtimes(conv)
            orch = Orchestrator(default_runtime, config, runtime_overrides=runtime_overrides)

            # Determine active Mode (plan / build / custom)
            _mode_name = None
            _argv_full = sys.argv[1:]
            if "--plan" in _argv_full:
                _mode_name = "plan"
            elif "--mode" in _argv_full:
                _idx = _argv_full.index("--mode")
                if _idx + 1 < len(_argv_full):
                    _mode_name = _argv_full[_idx + 1]
            if _mode_name is None:
                import os as _os2
                _mode_name = _os2.environ.get("PRAXIS_DEFAULT_MODE", "build")

            _active_mode = None
            try:
                from .modes import Mode as _Mode
                _active_mode = _Mode.load(_mode_name)
                if _mode_name != "build":
                    sys.stderr.write(f"[praxis] mode: {_mode_name}\n")
            except (ImportError, ValueError) as _me:
                sys.stderr.write(f"[praxis] warning: mode '{_mode_name}' not found ({_me}); running in build mode\n")

            # Filter out --plan, --mode <name>, and other flags
            _skip_next_arg = False
            args = []
            for _a in sys.argv[1:]:
                if _skip_next_arg:
                    _skip_next_arg = False
                    continue
                if _a == "--mode":
                    _skip_next_arg = True
                    continue
                if _a.startswith("--"):
                    continue
                args.append(_a)

            if args:
                message = " ".join(args)
            else:
                message = sys.stdin.read()

            try:
                result = orch.run(message, mode=_active_mode)
            except TypeError:
                result = orch.run(message)
            print(result)

        elif mode == "queue":
            from .queue_runner import run_queue_loop

            config = Config.from_env()
            _run_credential_check(config.workspace_root)
            run_queue_loop(config.workspace_root)

        elif mode == "daemon":
            from .daemon import start_daemon

            config = Config.from_env()
            _run_credential_check(config.workspace_root)
            start_daemon(config.workspace_root)

        elif mode == "stop":
            from .daemon import stop_daemon

            config = Config.from_env()
            stop_daemon(config.workspace_root)

        elif mode == "status":
            from .daemon import report_status

            config = Config.from_env()
            report_status(config.workspace_root)

        elif mode == "slack":
            import os as _os
            from .queue import TaskQueue
            from .slack_listener import SlackSocketListener

            config = Config.from_env()
            bot_token = _os.environ.get("PRAXIS_SLACK_BOT_TOKEN", "")
            app_token = _os.environ.get("PRAXIS_SLACK_APP_TOKEN", "")
            if not bot_token:
                raise SystemExit(
                    "[praxis] fatal: PRAXIS_SLACK_BOT_TOKEN not set. "
                    "Set it to your Slack bot token (xoxb- prefix). "
                    "See .env.example for setup instructions."
                )
            if not app_token:
                raise SystemExit(
                    "[praxis] fatal: PRAXIS_SLACK_APP_TOKEN not set. "
                    "Set it to your Slack app-level token (xapp- prefix). "
                    "Get it from: Slack app > Basic Information > App-Level Tokens "
                    "(needs connections:write scope). See .env.example."
                )
            queue = TaskQueue(config.workspace_root / ".praxis" / "queue")
            listener = SlackSocketListener(
                bot_token=bot_token,
                app_token=app_token,
                workspace_root=str(config.workspace_root),
                queue=queue,
            )
            sys.stderr.write("[praxis] Starting Slack socket mode listener...\n")
            listener.start()

        elif mode == "mcp":
            import os as _os
            try:
                from .mcp_server import MCPServer
            except ImportError as exc:
                raise SystemExit(str(exc))
            config = Config.from_env()
            port = int(_os.environ.get("PRAXIS_MCP_PORT", "8765"))
            server = MCPServer(config)
            server.start(port=port)

        elif mode == "approve":
            import os as _os
            from .config import Config as _Config
            config = _Config.from_env()
            staging_file = config.workspace_root / ".praxis" / "staging" / "external_actions.jsonl"
            _run_approve(staging_file)

        elif mode == "list_staged":
            from .config import Config as _Config
            config = _Config.from_env()
            _run_list_staged(config.workspace_root)

        elif mode == "schedule_add":
            config = Config.from_env()
            argv = sys.argv
            try:
                idx = argv.index("--schedule-add")
                args = argv[idx + 1:]
            except ValueError:
                args = []
            # Filter out any other flags
            pos_args = [a for a in args if not a.startswith("--")]
            if len(pos_args) < 3:
                print(
                    "Usage: python -m praxis --schedule-add <name> <cron> <prompt>",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            name, cron, prompt = pos_args[0], pos_args[1], pos_args[2]
            scheduler = _make_scheduler(config)
            try:
                task = scheduler.add_task(name, cron, prompt)
            except ValueError as exc:
                print(f"[praxis] Invalid cron expression: {exc}", file=sys.stderr)
                raise SystemExit(1)
            scheduler.save()
            print(
                f"Scheduled task added: {task.id}\n"
                f"  Name: {task.name}\n"
                f"  Schedule: {task.schedule}\n"
                f"  Next run: {task.next_run}"
            )

        elif mode == "schedule_list":
            config = Config.from_env()
            scheduler = _make_scheduler(config)
            tasks = scheduler.list_tasks()
            if not tasks:
                print("No scheduled tasks.")
            else:
                header = f"{'ID':<36}  {'Name':<20}  {'Schedule':<20}  {'Enabled':<7}  {'Next Run':<25}  {'Last Run'}"
                print(header)
                print("-" * len(header))
                for t in tasks:
                    enabled_str = "yes" if t.enabled else "no"
                    next_run_str = t.next_run or "-"
                    last_run_str = t.last_run or "-"
                    print(
                        f"{t.id:<36}  {t.name:<20}  {t.schedule:<20}  {enabled_str:<7}  {next_run_str:<25}  {last_run_str}"
                    )

        elif mode == "schedule_enable":
            config = Config.from_env()
            argv = sys.argv
            try:
                idx = argv.index("--schedule-enable")
                task_id = argv[idx + 1]
            except (ValueError, IndexError):
                print(
                    "Usage: python -m praxis --schedule-enable <task-id>",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            scheduler = _make_scheduler(config)
            try:
                scheduler.enable_task(task_id)
            except KeyError:
                print(f"Task not found: {task_id}", file=sys.stderr)
                raise SystemExit(1)
            scheduler.save()
            print(f"Task {task_id} enabled.")

        elif mode == "schedule_disable":
            config = Config.from_env()
            argv = sys.argv
            try:
                idx = argv.index("--schedule-disable")
                task_id = argv[idx + 1]
            except (ValueError, IndexError):
                print(
                    "Usage: python -m praxis --schedule-disable <task-id>",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            scheduler = _make_scheduler(config)
            try:
                scheduler.disable_task(task_id)
            except KeyError:
                print(f"Task not found: {task_id}", file=sys.stderr)
                raise SystemExit(1)
            scheduler.save()
            print(f"Task {task_id} disabled.")

        elif mode == "schedule_remove":
            config = Config.from_env()
            argv = sys.argv
            try:
                idx = argv.index("--schedule-remove")
                task_id = argv[idx + 1]
            except (ValueError, IndexError):
                print(
                    "Usage: python -m praxis --schedule-remove <task-id>",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            scheduler = _make_scheduler(config)
            try:
                scheduler.remove_task(task_id)
            except KeyError:
                print(f"Task not found: {task_id}", file=sys.stderr)
                raise SystemExit(1)
            scheduler.save()
            print(f"Task {task_id} removed.")

        elif mode == "wiki_sync_notion":
            from .integrations.wiki_sync import sync_to_notion
            config = Config.from_env()
            try:
                idx = sys.argv.index("--wiki-sync-notion")
                pos_args = [a for a in sys.argv[idx + 1:] if not a.startswith("--")]
            except ValueError:
                pos_args = []
            if len(pos_args) < 2:
                print(
                    "Usage: python -m praxis --wiki-sync-notion <page_slug> <notion_parent_id>",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            page_slug, notion_parent_id = pos_args[0], pos_args[1]
            wiki_root = config.workspace_root / "wiki"
            result = sync_to_notion(page_slug, notion_parent_id, wiki_root=wiki_root, config=config)
            print(result)

        elif mode == "wiki_sync_linear":
            from .integrations.wiki_sync import sync_to_linear
            config = Config.from_env()
            try:
                idx = sys.argv.index("--wiki-sync-linear")
                pos_args = [a for a in sys.argv[idx + 1:] if not a.startswith("--")]
            except ValueError:
                pos_args = []
            if len(pos_args) < 2:
                print(
                    "Usage: python -m praxis --wiki-sync-linear <page_slug> <team_id>",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            page_slug, team_id = pos_args[0], pos_args[1]
            wiki_root = config.workspace_root / "wiki"
            result = sync_to_linear(page_slug, team_id, wiki_root=wiki_root, config=config)
            print(result)

        elif mode == "wiki_link_issue":
            from .integrations.wiki_sync import link_linear_issue
            config = Config.from_env()
            try:
                idx = sys.argv.index("--wiki-link-issue")
                pos_args = [a for a in sys.argv[idx + 1:] if not a.startswith("--")]
            except ValueError:
                pos_args = []
            if len(pos_args) < 2:
                print(
                    "Usage: python -m praxis --wiki-link-issue <page_slug> <issue_id>",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            page_slug, issue_id = pos_args[0], pos_args[1]
            wiki_root = config.workspace_root / "wiki"
            result = link_linear_issue(page_slug, issue_id, wiki_root=wiki_root)
            print(result)

        elif mode == "setup":
            import os as _os
            from pathlib import Path as _Path
            from .setup_wizard import run_wizard

            # Determine workspace root from env or cwd
            workspace_root = _Path(_os.environ.get("PRAXIS_WORKSPACE_ROOT", _os.getcwd()))
            env_file = workspace_root / ".env"

            # Handle existing .env
            if env_file.exists():
                try:
                    choice = input(
                        "A .env file already exists. [o]verwrite, [m]erge, [c]ancel? [m]: "
                    ).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    choice = "c"
                if choice in ("c", "cancel"):
                    print("Setup cancelled.")
                    return
                elif choice in ("o", "overwrite"):
                    _mode = "overwrite"
                else:
                    _mode = "merge"  # default
            else:
                _mode = "overwrite"

            run_wizard(workspace_root, env_file=env_file, _env_mode=_mode)

        elif mode == "config":
            import os as _os
            from pathlib import Path as _Path
            from .config_wizard import run_config_wizard

            workspace_root = _Path(_os.environ.get("PRAXIS_WORKSPACE_ROOT", _os.getcwd()))
            env_file = workspace_root / ".env"
            run_config_wizard(workspace_root, env_file=env_file)

    except KeyboardInterrupt:
        sys.stderr.write("\n[praxis] interrupted.\n")
        raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(f"[praxis] fatal: {exc}")


if __name__ == "__main__":
    main()
