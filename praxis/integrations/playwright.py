"""Playwright browser automation integration — isolated subprocess, no session cookies.

Requires: pip install praxis[playwright]
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..config import Config

# Auth env var names to strip from subprocess environment
_AUTH_VARS = frozenset(
    [
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "PRAXIS_SLACK_WEBHOOK_URL",
        "PRAXIS_SLACK_BOT_TOKEN",
        "PRAXIS_SLACK_APP_TOKEN",
        "PRAXIS_WEB_SEARCH_API_KEY",
        "PRAXIS_EMAIL_PASSWORD",
        "PRAXIS_CALENDAR_URL",
        "PRAXIS_NOTION_TOKEN",
        "PRAXIS_LINEAR_API_KEY",
        "GITHUB_TOKEN",
    ]
)

_PLAYWRIGHT_FETCH_SCRIPT = textwrap.dedent(
    """\
    import asyncio, os, sys
    async def main():
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            print("PLAYWRIGHT_IMPORT_ERROR", flush=True)
            return
        url = os.environ["_PRAXIS_PW_URL"]
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                content = await page.inner_text("body")
            except Exception as exc:
                print(f"PLAYWRIGHT_ERROR:{exc}", flush=True)
                await browser.close()
                return
            await browser.close()
            print(content, flush=True)
    asyncio.run(main())
    """
)

_PLAYWRIGHT_SCREENSHOT_SCRIPT = textwrap.dedent(
    """\
    import asyncio, os, sys
    async def main():
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            print("PLAYWRIGHT_IMPORT_ERROR", flush=True)
            return
        url = os.environ["_PRAXIS_PW_URL"]
        out_path = os.environ["_PRAXIS_PW_OUT"]
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.screenshot(path=out_path, full_page=True)
            except Exception as exc:
                print(f"PLAYWRIGHT_ERROR:{exc}", flush=True)
                await browser.close()
                return
            await browser.close()
            print(f"SAVED:{out_path}", flush=True)
    asyncio.run(main())
    """
)


def _check_domain(url: str, config: Config) -> str | None:
    """Return error string if domain not in allowlist, else None."""
    if not config.allowed_domains:
        return (
            f"playwright: domain not in PRAXIS_ALLOWED_DOMAINS. "
            f"Add the domain to allow browser access."
        )
    domain = urlparse(url).hostname or ""
    if domain and domain not in config.allowed_domains:
        return (
            f"playwright: domain '{domain}' not in PRAXIS_ALLOWED_DOMAINS "
            f"(allowed: {', '.join(sorted(config.allowed_domains))})."
        )
    return None


def _subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build a clean subprocess env: inherit PATH/HOME/system vars, strip auth tokens."""
    env = {k: v for k, v in os.environ.items() if k not in _AUTH_VARS}
    if extra:
        env.update(extra)
    return env


_RETRY_DELAYS = [1, 2, 4]  # seconds between attempts


def _run_playwright_script_once(script: str, env: dict[str, str]) -> str:
    """Write script to temp file and run it in a subprocess. Return stdout."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        if not result.stdout and result.returncode != 0:
            stderr_snippet = (result.stderr or "")[:200]
            return f"PLAYWRIGHT_ERROR:Browser process crashed (exit {result.returncode}): {stderr_snippet}"
        return result.stdout
    except subprocess.TimeoutExpired:
        return "PLAYWRIGHT_ERROR:Browser process timed out (60s subprocess limit)"
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def _clean_playwright_error(raw: str) -> str:
    """Convert raw Playwright exception text to a clean user-facing string."""
    msg = raw.strip()
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return "Browser navigation timed out — the page did not respond within the timeout limit."
    if "net::" in msg or "connection refused" in msg.lower():
        return f"Browser network error: {msg[:200]}"
    # Strip the 'File "..."' traceback lines if present
    lines = [
        l
        for l in msg.splitlines()
        if not l.strip().startswith('File "') and "Traceback" not in l
    ]
    return " ".join(lines[:3]).strip()[:300] or "Browser error (no details available)."


def _run_playwright_script(
    script: str, env: dict[str, str], max_retries: int = 3
) -> str:
    """Run script with retry-with-backoff on transient PLAYWRIGHT_ERROR."""
    last_output = ""
    for attempt in range(max_retries + 1):
        output = _run_playwright_script_once(script, env)
        last_output = output
        if not output.startswith("PLAYWRIGHT_ERROR:"):
            return output
        # PLAYWRIGHT_IMPORT_ERROR is not transient — don't retry
        if output.startswith("PLAYWRIGHT_IMPORT_ERROR"):
            return output
        if attempt < max_retries:
            delay = (
                _RETRY_DELAYS[attempt]
                if attempt < len(_RETRY_DELAYS)
                else _RETRY_DELAYS[-1]
            )
            time.sleep(delay)
    return last_output


def _fetch(args: dict[str, Any], config: Config) -> str:
    """Navigate to URL and return page text content."""
    from ..tools import _redact_secrets

    url = args.get("url", "")
    if not url:
        return "playwright fetch: 'url' is required."
    max_chars = int(args.get("max_chars", 4000))

    err = _check_domain(url, config)
    if err:
        return err

    env = _subprocess_env({"_PRAXIS_PW_URL": url})
    output = _run_playwright_script(_PLAYWRIGHT_FETCH_SCRIPT, env)

    if output.startswith("PLAYWRIGHT_IMPORT_ERROR"):
        return (
            "playwright not installed. Run: pip install praxis[playwright]\n"
            "(installs playwright>=1.40; also run: playwright install chromium)"
        )
    if output.startswith("PLAYWRIGHT_ERROR:"):
        clean = _clean_playwright_error(output[len("PLAYWRIGHT_ERROR:"):])
        return _redact_secrets(f"playwright fetch error: {clean}")

    content = output.strip()
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n[truncated at {max_chars} chars]"
    return _redact_secrets(content) if content else "(page returned no text content)"


def _screenshot(args: dict[str, Any], config: Config) -> str:
    """Navigate to URL and save a screenshot inside the workspace."""
    from ..tools import _redact_secrets

    url = args.get("url", "")
    output_path = args.get("output_path", "")
    if not url:
        return "playwright screenshot: 'url' is required."
    if not output_path:
        return "playwright screenshot: 'output_path' is required."

    err = _check_domain(url, config)
    if err:
        return err

    # Resolve output path and verify it stays inside workspace
    resolved = (config.workspace_root / output_path).resolve()
    try:
        resolved.relative_to(config.workspace_root.resolve())
    except ValueError:
        return (
            f"playwright screenshot: output_path '{output_path}' resolves outside "
            f"WORKSPACE_ROOT — not allowed."
        )
    resolved.parent.mkdir(parents=True, exist_ok=True)

    env = _subprocess_env({"_PRAXIS_PW_URL": url, "_PRAXIS_PW_OUT": str(resolved)})
    output = _run_playwright_script(_PLAYWRIGHT_SCREENSHOT_SCRIPT, env)

    if output.startswith("PLAYWRIGHT_IMPORT_ERROR"):
        return (
            "playwright not installed. Run: pip install praxis[playwright]\n"
            "(installs playwright>=1.40; also run: playwright install chromium)"
        )
    if output.startswith("PLAYWRIGHT_ERROR:"):
        clean = _clean_playwright_error(output[len("PLAYWRIGHT_ERROR:"):])
        return _redact_secrets(f"playwright screenshot error: {clean}")
    if output.startswith("SAVED:"):
        return _redact_secrets(f"Screenshot saved: {output[len('SAVED:'):].strip()}")

    return _redact_secrets(output.strip()) or f"Screenshot attempted to: {resolved}"


def _execute_playwright(args: dict[str, Any], config: Config) -> str:
    """Dispatch playwright action."""
    action = args.get("action", "")
    if action == "fetch":
        return _fetch(args, config)
    if action == "screenshot":
        return _screenshot(args, config)
    return f"playwright: unknown action '{action}'. Valid: fetch, screenshot."


SCHEMAS: dict[str, dict[str, Any]] = {
    "playwright": {
        "name": "playwright",
        "description": (
            "Browser automation via Playwright subprocess. Runs in isolated headless "
            "mode with no access to local session cookies or credentials. "
            "Requires pip install praxis[playwright] and playwright install chromium."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["fetch", "screenshot"],
                    "description": (
                        "fetch: navigate to URL and return page text content. "
                        "screenshot: navigate to URL and save a PNG screenshot."
                    ),
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (must be in PRAXIS_ALLOWED_DOMAINS).",
                },
                "output_path": {
                    "type": "string",
                    "description": (
                        "For screenshot: relative path within workspace to save the PNG "
                        "(e.g. '.praxis/screenshots/page.png')."
                    ),
                },
                "max_chars": {
                    "type": "integer",
                    "description": "For fetch: max characters to return (default 4000).",
                    "default": 4000,
                },
            },
            "required": ["action", "url"],
        },
    }
}

IMPLEMENTATIONS: dict[str, Any] = {
    "playwright": _execute_playwright,
}
