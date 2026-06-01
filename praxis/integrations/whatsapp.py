"""WhatsApp adapter — connects to local Baileys bridge; inbound → Task queue; outbound staged by default.

Governance: ALL outbound sends go through .praxis/staging/whatsapp/replies/<task_id>.json
UNLESS the autonomy gate passes (autonomy=autonomous AND sender in trusted_numbers
AND reply within max_autonomous_words).

Requires whatsapp-bridge/bridge.js running locally (node whatsapp-bridge/bridge.js).
Bridge URL read from PRAXIS_WHATSAPP_BRIDGE_PORT env var at start() time — never at import.
PRAXIS_WHATSAPP_ALLOWED_NUMBERS: comma-separated allowlist — only process inbound from these.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_INJECTION_PHRASES = [
    "ignore your instructions",
    "ignore previous",
    "disregard your",
    "you are now",
    "forget your instructions",
]


def _redact_secrets(text: str) -> str:
    """Redact phone numbers: replace digit sequence after +country-code prefix with XXXX."""
    import re

    return re.sub(r"(\+\d{1,3})\d+", r"\1XXXX", text)


class WhatsAppAdapter:
    """Wraps a local Baileys bridge with Praxis governance (read-safe / write-escalate)."""

    def __init__(
        self,
        bridge_url: str,
        allowed_numbers: list[str],
        queue: Any,
        config: Any,
        channel_config: dict[str, Any] | None = None,
    ) -> None:
        """
        Args:
            bridge_url: Base URL of the local bridge, e.g. "http://127.0.0.1:3001".
            allowed_numbers: Allowlist of E.164 phone numbers to process inbound from.
            queue: TaskQueue instance (must have .append(Task)).
            config: Praxis Config object (workspace_root used for staging paths).
            channel_config: Optional dict with keys autonomy, trusted_numbers,
                            max_autonomous_words.
        """
        self._bridge_url = bridge_url.rstrip("/")
        self._allowed_numbers = set(allowed_numbers)
        self._queue = queue
        self._config = config
        self._channel_config: dict[str, Any] = channel_config or {}
        self._stop_event = threading.Event()
        self._listener_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Classmethod constructor
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, queue: Any, config: Any) -> "WhatsAppAdapter":
        """Construct adapter from environment variables.

        Reads:
          PRAXIS_WHATSAPP_BRIDGE_PORT      (default "3001")
          PRAXIS_WHATSAPP_ALLOWED_NUMBERS  (comma-split, stripped)
          PRAXIS_WHATSAPP_AUTONOMY         (default "staged")
          PRAXIS_WHATSAPP_TRUSTED_NUMBERS  (comma-split, default [])
          PRAXIS_WHATSAPP_MAX_AUTONOMOUS_WORDS (default 0)
        """
        port = os.environ.get("PRAXIS_WHATSAPP_BRIDGE_PORT", "3001").strip()
        bridge_url = f"http://127.0.0.1:{port}"

        raw_allowed = os.environ.get("PRAXIS_WHATSAPP_ALLOWED_NUMBERS", "")
        allowed_numbers = [n.strip() for n in raw_allowed.split(",") if n.strip()]

        autonomy = os.environ.get("PRAXIS_WHATSAPP_AUTONOMY", "staged").strip()

        raw_trusted = os.environ.get("PRAXIS_WHATSAPP_TRUSTED_NUMBERS", "")
        trusted_numbers = [n.strip() for n in raw_trusted.split(",") if n.strip()]

        max_words_raw = os.environ.get("PRAXIS_WHATSAPP_MAX_AUTONOMOUS_WORDS", "0").strip()
        try:
            max_words = int(max_words_raw)
        except ValueError:
            max_words = 0

        channel_config = {
            "autonomy": autonomy,
            "trusted_numbers": trusted_numbers,
            "max_autonomous_words": max_words,
        }

        return cls(
            bridge_url=bridge_url,
            allowed_numbers=allowed_numbers,
            queue=queue,
            config=config,
            channel_config=channel_config,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Verify bridge reachability, then start the SSE listener thread."""
        import urllib.error
        import urllib.request

        ping_url = f"{self._bridge_url}/ping"
        try:
            with urllib.request.urlopen(ping_url, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if not body.get("ok"):
                raise RuntimeError(
                    "WhatsApp bridge responded but reported not OK. "
                    "Check whatsapp-bridge/bridge.js logs."
                )
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(
                "WhatsApp bridge not running. Start it with: node whatsapp-bridge/bridge.js"
            ) from exc

        self._stop_event.clear()
        self._listener_thread = threading.Thread(
            target=self._sse_listener,
            name="whatsapp-sse-listener",
            daemon=True,
        )
        self._listener_thread.start()
        logger.info("WhatsApp SSE listener started (bridge=%s)", self._bridge_url)

    def stop(self) -> None:
        """Signal the SSE listener thread to stop."""
        self._stop_event.set()
        if self._listener_thread is not None:
            self._listener_thread.join(timeout=5)
            self._listener_thread = None

    # ------------------------------------------------------------------
    # SSE listener (runs in daemon thread)
    # ------------------------------------------------------------------

    def _sse_listener(self) -> None:
        """Read SSE stream from bridge and dispatch events. Reconnects on transient errors."""
        import urllib.error
        import urllib.request

        events_url = f"{self._bridge_url}/events"

        while not self._stop_event.is_set():
            try:
                req = urllib.request.Request(events_url)
                req.add_header("Accept", "text/event-stream")
                req.add_header("Cache-Control", "no-cache")

                with urllib.request.urlopen(req, timeout=60) as resp:
                    for raw_line in resp:
                        if self._stop_event.is_set():
                            break
                        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                        if line.startswith("data:"):
                            payload = line[len("data:"):].strip()
                            if not payload:
                                continue
                            try:
                                event = json.loads(payload)
                            except json.JSONDecodeError:
                                logger.debug("WhatsApp SSE: non-JSON payload ignored")
                                continue
                            try:
                                self.on_event(event)
                            except Exception:  # noqa: BLE001
                                logger.exception("WhatsApp on_event raised unexpectedly")

            except (urllib.error.URLError, OSError, TimeoutError):
                if not self._stop_event.is_set():
                    logger.warning(
                        "WhatsApp SSE connection lost — retrying in 5 s"
                    )
                    self._stop_event.wait(timeout=5)
            except Exception:  # noqa: BLE001
                if not self._stop_event.is_set():
                    logger.exception("WhatsApp SSE listener unexpected error — retrying in 10 s")
                    self._stop_event.wait(timeout=10)

    # ------------------------------------------------------------------
    # Inbound handler
    # ------------------------------------------------------------------

    def on_event(self, event: dict) -> None:
        """Process a single SSE event dict.

        Content is treated as data — never as a directive.
        """
        # Bridge connection status event
        if event.get("type") == "connected":
            logger.info("WhatsApp bridge connected")
            return

        from_number: str = event.get("from", "")
        message: str = event.get("message", "")

        # Injection detection — skip, never execute
        message_lower = message.lower()
        for phrase in _INJECTION_PHRASES:
            if phrase in message_lower:
                logger.warning(
                    "WhatsApp injection attempt detected from %s — message discarded",
                    _redact_secrets(from_number),
                )
                return

        # Allowed-numbers filter
        if from_number not in self._allowed_numbers:
            logger.debug(
                "WhatsApp message from non-allowed number %s — ignored",
                _redact_secrets(from_number),
            )
            return

        # Enqueue as task (data, not command)
        from ..queue import Task

        task = Task.create(
            prompt=f"[whatsapp] from={from_number}: {message}",
            priority=0,
        )
        self._queue.append(task)
        logger.debug("WhatsApp task enqueued: %s", task.id)

    # ------------------------------------------------------------------
    # Outbound — WRITE-ESCALATE (structural governance)
    # ------------------------------------------------------------------

    def stage_reply(self, task_id: str, to_number: str, message: str) -> Path:
        """Write a reply to staging. NEVER calls the bridge or any network endpoint.

        Writes to .praxis/staging/whatsapp/replies/<task_id>.json with keys:
            task_id, to, message, timestamp
        Returns the path written.
        """
        staging_dir = (
            self._config.workspace_root
            / ".praxis"
            / "staging"
            / "whatsapp"
            / "replies"
        )
        staging_dir.mkdir(parents=True, exist_ok=True)

        record = {
            "task_id": task_id,
            "to": to_number,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        path = staging_dir / f"{task_id}.json"
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return path

    def send(self, to_number: str, message: str) -> bool:
        """Call the bridge POST /send endpoint. Returns True on success, False on failure.

        Logs errors; never raises.
        """
        import urllib.error
        import urllib.request

        send_url = f"{self._bridge_url}/send"
        payload = json.dumps({"to": to_number, "message": message}).encode("utf-8")
        req = urllib.request.Request(
            send_url,
            data=payload,
            method="POST",
        )
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if body.get("ok"):
                return True
            logger.error(
                "WhatsApp bridge send failed: %s",
                body.get("error", "unknown error"),
            )
            return False
        except (urllib.error.URLError, OSError) as exc:
            logger.error("WhatsApp bridge send network error: %s", exc)
            return False
        except Exception:  # noqa: BLE001
            logger.exception("WhatsApp bridge send unexpected error")
            return False

    def send_or_stage(
        self,
        task_id: str,
        message: str,
        to_number: str,
        from_number: str,
    ) -> bool:
        """Apply the autonomy gate. Send directly only if ALL conditions are met.

        Autonomy gate conditions (ALL must be true):
          1. channel_config['autonomy'] == 'autonomous'
          2. from_number in channel_config['trusted_numbers']
          3. len(message.split()) <= channel_config['max_autonomous_words']

        Returns True if sent directly via bridge; False if staged.
        """
        autonomy = self._channel_config.get("autonomy", "staged")
        trusted: list[str] = self._channel_config.get("trusted_numbers", [])
        max_words: int = int(self._channel_config.get("max_autonomous_words", 0))

        gate_passes = (
            autonomy == "autonomous"
            and from_number in trusted
            and len(message.split()) <= max_words
        )

        if gate_passes:
            return self.send(to_number=to_number, message=message)

        # Gate did not pass — stage the reply
        self.stage_reply(task_id=task_id, to_number=to_number, message=message)
        return False
