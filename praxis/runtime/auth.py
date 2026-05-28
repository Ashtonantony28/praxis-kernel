"""Auth rotation hardening — expiry detection, credential inventory, clean error messages."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def parse_jwt_expiry(token: str) -> Optional[float]:
    """Extract the 'exp' claim from a JWT token string.

    Returns the expiry as a float (Unix timestamp) if found, or None if:
    - the token is not a valid JWT (not 3 dot-separated parts)
    - the payload cannot be base64url-decoded or JSON-parsed
    - there is no 'exp' field in the payload
    - any other error occurs
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        # base64url-decode the payload (middle part), adding padding as needed
        payload_b64 = parts[1]
        # Add padding
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding

        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))

        exp = payload.get("exp")
        if exp is None:
            return None
        return float(exp)
    except Exception:
        return None


def check_token_expiry(
    token: Optional[str],
    *,
    warning_hours: float = 24,
) -> dict:
    """Check token configuration and expiry status.

    Returns a dict with keys:
      configured:        bool — True if token is a non-empty string
      expires_at:        str | None — ISO8601 UTC if JWT with exp, else None
      expires_in_hours:  float | None — hours until expiry (negative = already expired)
      near_expiry:       bool — True if 0 < expires_in_hours < warning_hours
    """
    if not token:
        return {
            "configured": False,
            "expires_at": None,
            "expires_in_hours": None,
            "near_expiry": False,
        }

    exp_ts = parse_jwt_expiry(token)
    if exp_ts is None:
        # Opaque token (not a JWT, or no exp claim)
        return {
            "configured": True,
            "expires_at": None,
            "expires_in_hours": None,
            "near_expiry": False,
        }

    now_ts = datetime.now(timezone.utc).timestamp()
    expires_in_hours = (exp_ts - now_ts) / 3600
    # near_expiry: only within the warning window, NOT already expired
    near_expiry = 0 < expires_in_hours < warning_hours
    expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc).isoformat()

    return {
        "configured": True,
        "expires_at": expires_at,
        "expires_in_hours": expires_in_hours,
        "near_expiry": near_expiry,
    }


# Each tuple: (env_var_name, description, check_expiry)
KNOWN_CREDENTIALS: list[tuple[str, str, bool]] = [
    ("CLAUDE_CODE_OAUTH_TOKEN", "Anthropic OAuth token (subscription)", True),
    ("ANTHROPIC_API_KEY", "Anthropic API key (pay-per-token)", False),
    ("PRAXIS_CLOUD_API_KEY", "Cloud provider API key", False),
    ("PRAXIS_SLACK_WEBHOOK_URL", "Slack incoming webhook URL", False),
    ("PRAXIS_SLACK_BOT_TOKEN", "Slack bot token", False),
    ("PRAXIS_SLACK_APP_TOKEN", "Slack app-level token", False),
    ("PRAXIS_NOTION_TOKEN", "Notion integration token", False),
    ("PRAXIS_LINEAR_API_KEY", "Linear API key", False),
    ("PRAXIS_WEB_SEARCH_API_KEY", "Brave Search API key", False),
    ("PRAXIS_EMAIL_PASSWORD", "Email app password", False),
]


def build_credential_inventory() -> dict:
    """Build a metadata-only inventory of known credentials.

    CRITICAL: the returned dict NEVER contains credential values —
    only presence/absence and expiry metadata.

    Returns:
        {
            "generated_at": "<ISO8601>",
            "credentials": [
                {
                    "name": "<env_var_name>",
                    "description": "<human description>",
                    "configured": bool,
                    "expires_at": str | None,
                    "expires_in_hours": float | None,
                    "near_expiry": bool,
                },
                ...
            ]
        }
    """
    credentials = []

    for env_var, description, check_expiry in KNOWN_CREDENTIALS:
        value = os.environ.get(env_var)
        configured = bool(value)

        if configured and check_expiry:
            expiry_info = check_token_expiry(value)
            expires_at = expiry_info["expires_at"]
            expires_in_hours = expiry_info["expires_in_hours"]
            near_expiry = expiry_info["near_expiry"]
        else:
            expires_at = None
            expires_in_hours = None
            near_expiry = False

        credentials.append(
            {
                "name": env_var,
                "description": description,
                "configured": configured,
                "expires_at": expires_at,
                "expires_in_hours": expires_in_hours,
                "near_expiry": near_expiry,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "credentials": credentials,
    }


def write_credential_inventory(workspace_root: Path, inventory: dict) -> None:
    """Write credential inventory JSON to .praxis/security/credentials.json.

    Creates the directory if it does not exist. The file contains only
    presence/metadata — never credential values.
    """
    security_dir = workspace_root / ".praxis" / "security"
    security_dir.mkdir(parents=True, exist_ok=True)
    out_path = security_dir / "credentials.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2)
        f.write("\n")


def warn_near_expiry(inventory: dict) -> list[str]:
    """Return warning strings for any near-expiry credentials.

    Format:
      "[praxis] WARNING: {name} expires in {hours:.1f}h (at {expires_at}). ..."
    Returns empty list if no credentials are near expiry.
    """
    warnings = []
    for cred in inventory.get("credentials", []):
        if cred.get("near_expiry"):
            name = cred["name"]
            hours = cred["expires_in_hours"]
            expires_at = cred["expires_at"]
            warnings.append(
                f"[praxis] WARNING: {name} expires in {hours:.1f}h "
                f"(at {expires_at}). "
                "Refresh it before expiry to avoid interruption."
            )
    return warnings


def graceful_auth_error_message(auth_method: str = "api_key") -> str:
    """Return a human-readable authentication failure message.

    Args:
        auth_method: One of "oauth", "cloud", "local", or "api_key" (default).

    Returns:
        Multi-line string with diagnosis and remediation steps.
    """
    if auth_method == "oauth":
        return (
            "[praxis] fatal: authentication failed — CLAUDE_CODE_OAUTH_TOKEN may be expired.\n"
            "To refresh:\n"
            "  1. Run: claude auth login\n"
            "  2. Complete the browser flow.\n"
            "  3. Re-export CLAUDE_CODE_OAUTH_TOKEN from the refreshed session.\n"
            "Alternatively, set ANTHROPIC_API_KEY as a fallback."
        )
    if auth_method == "cloud":
        return (
            "[praxis] fatal: cloud API rejected authentication.\n"
            "Check your PRAXIS_CLOUD_API_KEY:\n"
            "  - Verify the key is correct and not expired.\n"
            "  - Generate a new key from your cloud provider dashboard.\n"
            "  - Update PRAXIS_CLOUD_API_KEY in your environment or .env file."
        )
    if auth_method == "local":
        return (
            "[praxis] fatal: local model server rejected authentication.\n"
            "Check your server configuration:\n"
            "  - Verify PRAXIS_LOCAL_BASE_URL points to your running server.\n"
            "  - Check if the server requires an API key and configure it.\n"
            "  - Ensure the local model server is running and reachable."
        )
    # Default: api_key
    return (
        "[praxis] fatal: authentication failed — check your ANTHROPIC_API_KEY.\n"
        "To get a new key:\n"
        "  1. Visit: https://console.anthropic.com/settings/keys\n"
        "  2. Create or regenerate your API key.\n"
        "  3. Update ANTHROPIC_API_KEY in your environment or .env file.\n"
        "Or use CLAUDE_CODE_OAUTH_TOKEN for subscription auth (flat cost)."
    )
