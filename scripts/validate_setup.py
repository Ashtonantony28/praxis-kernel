"""
Praxis — Connection Validation Script

Checks each configured integration and reports pass/fail/skip.
Can be run directly or imported as a module.

Usage:
    python scripts/validate_setup.py [--load-dotenv]
"""

import imaplib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Tuple


def check_email() -> Tuple[str, str]:
    host = os.environ.get("PRAXIS_EMAIL_IMAP_HOST", "")
    user = os.environ.get("PRAXIS_EMAIL_USER", "")
    password = os.environ.get("PRAXIS_EMAIL_PASSWORD", "")
    if not host:
        return ("skip", "not configured (skip)")
    try:
        mail = imaplib.IMAP4_SSL(host)
        mail.login(user, password)
        mail.logout()
        return ("pass", f"({host})")
    except Exception:
        return ("fail", "Check app password (PRAXIS_EMAIL_USER / PRAXIS_EMAIL_PASSWORD)")


def check_calendar() -> Tuple[str, str]:
    url = os.environ.get("PRAXIS_CALENDAR_URL", "")
    if not url:
        return ("skip", "not configured (skip)")
    try:
        urllib.request.urlopen(url, timeout=5)
        return ("pass", "iCal feed reachable")
    except Exception:
        return ("fail", "Check URL/domain (PRAXIS_CALENDAR_URL)")


def check_github() -> Tuple[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return ("skip", "not configured (skip)")
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            return ("pass", "gh auth ok")
        return ("fail", "Run: gh auth login")
    except Exception:
        return ("fail", "Run: gh auth login")


def check_linear() -> Tuple[str, str]:
    api_key = os.environ.get("PRAXIS_LINEAR_API_KEY", "")
    if not api_key:
        return ("skip", "not configured (skip)")
    try:
        query = '{"query": "{ viewer { id name } }"}'
        req = urllib.request.Request(
            "https://api.linear.app/graphql",
            data=query.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": api_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        name = body.get("data", {}).get("viewer", {}).get("name", "unknown")
        if name:
            return ("pass", f"authenticated as {name}")
        return ("fail", "API key invalid (PRAXIS_LINEAR_API_KEY)")
    except Exception:
        return ("fail", "API key invalid (PRAXIS_LINEAR_API_KEY)")


def check_notion() -> Tuple[str, str]:
    token = os.environ.get("PRAXIS_NOTION_TOKEN", "")
    if not token:
        return ("skip", "not configured (skip)")
    try:
        req = urllib.request.Request(
            "https://api.notion.com/v1/users/me",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return ("pass", "token valid")
        return ("fail", "Check token (PRAXIS_NOTION_TOKEN)")
    except Exception:
        return ("fail", "Check token (PRAXIS_NOTION_TOKEN)")


def check_slack() -> Tuple[str, str]:
    webhook_url = os.environ.get("PRAXIS_SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        return ("skip", "not configured (skip)")
    try:
        payload = json.dumps({"text": "praxis validate_setup ping (ignore)"}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return ("pass", "webhook ok")
        return ("fail", "Check webhook URL (PRAXIS_SLACK_WEBHOOK_URL)")
    except Exception:
        return ("fail", "Check webhook URL (PRAXIS_SLACK_WEBHOOK_URL)")


def check_telegram() -> Tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return ("skip", "not configured (skip)")
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getMe",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        username = body.get("result", {}).get("username", "")
        if username:
            return ("pass", f"bot @{username}")
        return ("fail", "Check bot token (TELEGRAM_BOT_TOKEN)")
    except Exception:
        return ("fail", "Check bot token (TELEGRAM_BOT_TOKEN)")


def check_whatsapp() -> Tuple[str, str]:
    allowed = os.environ.get("PRAXIS_WHATSAPP_ALLOWED_NUMBERS", "")
    if not allowed:
        return ("skip", "not configured (skip)")
    port = os.environ.get("PRAXIS_WHATSAPP_BRIDGE_PORT", "3001")
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/ping",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2):
            return ("pass", "bridge running")
    except Exception:
        return ("fail", "Start bridge first: node whatsapp-bridge/bridge.js")


def check_web_search() -> Tuple[str, str]:
    api_key = os.environ.get("PRAXIS_WEB_SEARCH_API_KEY", "")
    if not api_key:
        return ("skip", "not configured (skip)")
    try:
        req = urllib.request.Request(
            "https://api.search.brave.com/res/v1/web/search?q=test",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return ("pass", "search API ok")
        return ("fail", "Check API key (PRAXIS_WEB_SEARCH_API_KEY)")
    except Exception:
        return ("fail", "Check API key (PRAXIS_WEB_SEARCH_API_KEY)")


_CHECKS = [
    ("Email", check_email),
    ("Slack", check_slack),
    ("Linear", check_linear),
    ("Notion", check_notion),
    ("Telegram", check_telegram),
    ("WhatsApp", check_whatsapp),
    ("Web search", check_web_search),
    ("Calendar", check_calendar),
    ("GitHub", check_github),
]

_STATUS_SYMBOL = {
    "pass": "✓",  # checkmark
    "fail": "✗",  # cross
    "skip": "-",
}


def run_validation(workspace_root=None) -> dict:
    """Run all checks; return {name: (status, detail)} dict. Also prints table."""
    results = {}
    for name, fn in _CHECKS:
        try:
            status, detail = fn()
        except Exception as exc:
            status, detail = "fail", str(exc)
        results[name] = (status, detail)

    print("Praxis — Connection Validation")
    print("================================")
    for name, (status, detail) in results.items():
        symbol = _STATUS_SYMBOL.get(status, "?")
        if status == "fail":
            print(f"  {symbol} {name} — {detail}")
        elif status == "skip":
            print(f"  {symbol} {name} — {detail}")
        else:
            print(f"  {symbol} {name} {detail}")
    print("================================")

    passed = sum(1 for s, _ in results.values() if s == "pass")
    failed = sum(1 for s, _ in results.values() if s == "fail")
    skipped = sum(1 for s, _ in results.values() if s == "skip")
    total = len(results)
    print(f"{total} checks: {passed} passed, {failed} failed, {skipped} skipped")

    return results


def _load_dotenv(path: str) -> None:
    """Minimal .env loader using stdlib only."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    if "--load-dotenv" in sys.argv:
        _load_dotenv(os.path.join(os.getcwd(), ".env"))
    run_validation()
