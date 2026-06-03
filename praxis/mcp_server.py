"""MCP Gateway for Praxis — HTTP/SSE transport.

Exposes all Praxis tools (TOOL_SCHEMAS + INTEGRATION_SCHEMAS) as MCP tools
over HTTP/SSE. Every tool call passes through escalation-boundary.py (§5).

Transport: HTTP/SSE via starlette + uvicorn.
NOT using FastMCP: FastMCP auto-generates inputSchema from Python type
annotations; we need to pass Praxis's pre-built JSON schemas verbatim.
Uses low-level mcp.server.lowlevel.server.Server instead.

Convergence routing does NOT apply: MCP calls bypass Orchestrator.run()
and dispatch directly to tool implementations.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable

try:
    import uvicorn
    from mcp.server.lowlevel.server import Server as MCPLowLevelServer
    from mcp.server.lowlevel.helper_types import ReadResourceContents
    from mcp.server.sse import SseServerTransport
    from mcp import types as mcp_types
    from pydantic import AnyUrl
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Mount, Route, WebSocketRoute
    from starlette.websockets import WebSocket, WebSocketDisconnect
except ImportError as exc:
    raise ImportError(
        "[praxis] MCP gateway requires additional dependencies.\n"
        "  Install with: pip install praxis[mcp]\n"
        f"  Missing: {exc}"
    ) from exc

from .config import Config
from .hooks import run_pretool_hook
from .integrations import INTEGRATION_IMPLEMENTATIONS, INTEGRATION_SCHEMAS
from .tools import TOOL_IMPLEMENTATIONS, TOOL_SCHEMAS


class MCPServer:
    """Praxis MCP gateway over HTTP/SSE transport.

    Exposes all Praxis tools as MCP tools. Every tool call passes through
    escalation-boundary.py (§5 boundary) before execution.

    Convergence routing does NOT apply here: tool calls bypass Orchestrator.run()
    and dispatch directly to TOOL_IMPLEMENTATIONS / INTEGRATION_IMPLEMENTATIONS.
    §5 hook applies to every call via _make_handler().
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._all_schemas: dict[str, dict[str, Any]] = {
            **TOOL_SCHEMAS,
            **INTEGRATION_SCHEMAS,
        }
        self._all_impls: dict[str, Callable[[dict[str, Any], Config], str]] = {
            **TOOL_IMPLEMENTATIONS,
            **INTEGRATION_IMPLEMENTATIONS,
        }
        self._mcp = MCPLowLevelServer(name="praxis-mcp")
        self._register_tools()
        self._register_resources()

    def _register_tools(self) -> None:
        """Register list_tools and call_tool handlers on the low-level server."""
        all_schemas = self._all_schemas
        all_impls = self._all_impls
        make_handler = self._make_handler

        @self._mcp.list_tools()
        async def handle_list_tools() -> list[mcp_types.Tool]:
            return [
                mcp_types.Tool(
                    name=schema["name"],
                    description=schema.get("description", ""),
                    inputSchema=schema["input_schema"],
                )
                for schema in all_schemas.values()
            ]

        @self._mcp.call_tool()
        async def handle_call_tool(
            name: str, arguments: dict[str, Any]
        ) -> list[mcp_types.TextContent]:
            impl_fn = all_impls.get(name)
            if impl_fn is None:
                return [
                    mcp_types.TextContent(
                        type="text", text=f"[praxis] Unknown tool: {name}"
                    )
                ]
            handler = make_handler(name, impl_fn)
            # Run sync impl in thread pool to avoid blocking the event loop.
            loop = asyncio.get_event_loop()
            result: str = await loop.run_in_executor(
                None, lambda: handler(**arguments)
            )
            return [mcp_types.TextContent(type="text", text=result)]

    def _register_resources(self) -> None:
        """Expose wiki/pages/ as read-only MCP resources at wiki://pages/{slug}."""
        config = self._config

        @self._mcp.list_resources()
        async def handle_list_resources() -> list[mcp_types.Resource]:
            wiki_pages_dir = Path(config.workspace_root) / "wiki" / "pages"
            if not wiki_pages_dir.is_dir():
                return []
            return [
                mcp_types.Resource(
                    uri=f"wiki://pages/{md_file.stem}",
                    name=md_file.stem,
                    description=f"Wiki page: {md_file.stem}",
                    mimeType="text/markdown",
                )
                for md_file in sorted(wiki_pages_dir.glob("*.md"))
                if not md_file.name.startswith(".")
            ]

        @self._mcp.read_resource()
        async def handle_read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
            uri_str = str(uri)
            prefix = "wiki://pages/"
            if not uri_str.startswith(prefix):
                raise ValueError(f"[praxis] Unknown resource URI scheme: {uri_str}")
            slug = uri_str[len(prefix):]
            # Guard: slug must be safe (no path traversal)
            if "/" in slug or "\\" in slug or slug.startswith("."):
                raise ValueError(f"[praxis] Invalid resource slug: {slug!r}")
            page_path = (
                Path(config.workspace_root) / "wiki" / "pages" / f"{slug}.md"
            )
            if not page_path.exists():
                raise FileNotFoundError(f"[praxis] Wiki page not found: {slug}")
            return [
                ReadResourceContents(
                    content=page_path.read_text(encoding="utf-8"),
                    mime_type="text/markdown",
                )
            ]

    def _make_handler(
        self, tool_name: str, impl_fn: Callable[[dict[str, Any], Config], str]
    ) -> Callable[..., str]:
        """Factory returning a sync (**kwargs) -> str closure.

        The closure calls the §5 escalation hook before executing the
        implementation. Blocked calls return a BLOCKED message; impl is
        never called.
        """
        config = self._config

        def handler(**kwargs: Any) -> str:
            tool_input = dict(kwargs)
            # §5 HOOK: must fire before any impl executes.
            hook_result = run_pretool_hook(config, tool_name, tool_input)
            if not hook_result.allowed:
                return (
                    f"BLOCKED by §5 escalation boundary: {hook_result.reason}"
                )
            # Hook approved. impl_fn calls _redact_secrets() internally.
            return impl_fn(tool_input, config)

        return handler

    def start(self, port: int | None = None) -> None:
        """Start the HTTP/SSE MCP server.

        Port is resolved in order:
        1. port argument (if not None)
        2. PRAXIS_MCP_PORT env var
        3. default 8765
        """
        if port is None:
            port = int(os.environ.get("PRAXIS_MCP_PORT", "8765"))

        mcp_server = self._mcp
        message_path = "/messages"
        sse_transport = SseServerTransport(message_path)

        async def handle_sse(scope: Any, receive: Any, send: Any) -> Response:
            async with sse_transport.connect_sse(scope, receive, send) as streams:
                await mcp_server.run(
                    streams[0],
                    streams[1],
                    mcp_server.create_initialization_options(),
                )
            return Response()

        async def sse_endpoint(request: Request) -> Response:
            return await handle_sse(
                request.scope, request.receive, request._send  # type: ignore[attr-defined]
            )

        async def dashboard_endpoint(request: Request) -> Response:
            """Return a read-only HTML observability dashboard."""
            import json as _json
            from datetime import datetime, timezone

            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            workspace = self._config.workspace_root

            # --- Telemetry data ---
            try:
                from .runtime.telemetry import TelemetryStore
                store = TelemetryStore.get_global()
                counts = store.get_counts()
                recent_events = store.get_recent(50)
            except Exception:
                counts = {"tool_call_count": "n/a", "hook_block_count": "n/a", "circuit_breaker_trips": "n/a"}
                recent_events = []
                telemetry_error = "Telemetry unavailable"
            else:
                telemetry_error = None

            # Compute p50/p95/p99 from last 50 events
            latencies = sorted(e.latency_ms for e in recent_events)
            def _pct(data: list, q: float) -> str:
                if not data:
                    return "n/a"
                idx = min(int(q * len(data)), len(data) - 1)
                return f"{data[idx]:.1f}"

            p50 = _pct(latencies, 0.50)
            p95 = _pct(latencies, 0.95)
            p99 = _pct(latencies, 0.99)

            # --- Queue depth ---
            queue_pending = 0
            queue_running = 0
            queue_error = None
            try:
                tasks_file = workspace / ".praxis" / "queue" / "tasks.jsonl"
                if tasks_file.exists():
                    for raw_line in tasks_file.read_text(encoding="utf-8").splitlines():
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        try:
                            task_obj = _json.loads(raw_line)
                            status = task_obj.get("status", "")
                            if status == "pending":
                                queue_pending += 1
                            elif status == "running":
                                queue_running += 1
                        except Exception:
                            pass
            except Exception as exc:
                queue_error = str(exc)

            # --- Credentials ---
            cred_rows: list[dict] = []
            cred_error = None
            try:
                cred_file = workspace / ".praxis" / "security" / "credentials.json"
                if cred_file.exists():
                    cred_data = _json.loads(cred_file.read_text(encoding="utf-8"))
                    # credentials.json is a dict of name -> metadata
                    if isinstance(cred_data, dict):
                        for name, meta in cred_data.items():
                            if isinstance(meta, dict):
                                cred_rows.append({
                                    "name": name,
                                    "configured": meta.get("configured", False),
                                    "near_expiry": meta.get("near_expiry", False),
                                    "expires_at": meta.get("expires_at") or "unknown",
                                })
                else:
                    cred_error = "credentials.json not found — run praxis to generate"
            except Exception as exc:
                cred_error = f"Could not read credentials.json: {exc}"

            # --- Build HTML ---
            def _esc(v: object) -> str:
                return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

            # Tool calls table rows
            tool_rows_html = ""
            if recent_events:
                for ev in reversed(recent_events):  # newest first
                    tool_rows_html += (
                        f"<tr>"
                        f"<td>{_esc(ev.tool_name)}</td>"
                        f"<td>{_esc(f'{ev.latency_ms:.1f}')}</td>"
                        f"<td>{_esc(ev.hook_result)}</td>"
                        f"<td>{_esc(ev.caller)}</td>"
                        f"<td>{_esc(ev.timestamp)}</td>"
                        f"</tr>\n"
                    )
            elif telemetry_error:
                tool_rows_html = f'<tr><td colspan="5">{_esc(telemetry_error)}</td></tr>\n'
            else:
                tool_rows_html = '<tr><td colspan="5">No tool calls recorded yet</td></tr>\n'

            # Credential table rows
            cred_rows_html = ""
            if cred_error:
                cred_rows_html = f'<tr><td colspan="4">{_esc(cred_error)}</td></tr>\n'
            elif cred_rows:
                for row in cred_rows:
                    near = "YES" if row["near_expiry"] else "no"
                    conf = "yes" if row["configured"] else "NO"
                    cred_rows_html += (
                        f"<tr>"
                        f"<td>{_esc(row['name'])}</td>"
                        f"<td>{conf}</td>"
                        f"<td class=\"{'warn' if row['near_expiry'] else ''}\">{near}</td>"
                        f"<td>{_esc(row['expires_at'])}</td>"
                        f"</tr>\n"
                    )
            else:
                cred_rows_html = '<tr><td colspan="4">No credentials found</td></tr>\n'

            queue_err_html = f'<p class="err">Queue read error: {_esc(queue_error)}</p>' if queue_error else ""

            html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="10">
<title>Praxis Observability Dashboard</title>
<style>
  body {{ font-family: monospace; background: #1a1a2e; color: #e0e0e0; margin: 0; padding: 1rem 2rem; }}
  h1 {{ color: #a78bfa; border-bottom: 1px solid #444; padding-bottom: .4rem; }}
  h2 {{ color: #7dd3fc; margin-top: 2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 1rem; }}
  th {{ background: #2d2d4e; color: #c4b5fd; padding: .4rem .6rem; text-align: left; }}
  td {{ padding: .3rem .6rem; border-bottom: 1px solid #2d2d4e; }}
  tr:hover td {{ background: #23233a; }}
  .stat-grid {{ display: flex; gap: 2rem; flex-wrap: wrap; margin-bottom: 1rem; }}
  .stat {{ background: #2d2d4e; border-radius: 6px; padding: .8rem 1.2rem; min-width: 160px; }}
  .stat-label {{ font-size: .75rem; color: #94a3b8; text-transform: uppercase; }}
  .stat-value {{ font-size: 1.5rem; color: #a78bfa; font-weight: bold; }}
  .warn {{ color: #f87171; font-weight: bold; }}
  .err {{ color: #f87171; }}
  footer {{ margin-top: 2rem; font-size: .8rem; color: #64748b; border-top: 1px solid #333; padding-top: .5rem; }}
</style>
</head>
<body>
<h1>Praxis Observability Dashboard</h1>

<h2>Counters</h2>
<div class="stat-grid">
  <div class="stat"><div class="stat-label">Tool Calls Total</div><div class="stat-value">{_esc(counts['tool_call_count'])}</div></div>
  <div class="stat"><div class="stat-label">Hook Blocks Total</div><div class="stat-value">{_esc(counts['hook_block_count'])}</div></div>
  <div class="stat"><div class="stat-label">Circuit Breaker Trips</div><div class="stat-value">{_esc(counts['circuit_breaker_trips'])}</div></div>
</div>

<h2>Latency (last 50 tool calls)</h2>
<div class="stat-grid">
  <div class="stat"><div class="stat-label">p50 (ms)</div><div class="stat-value">{_esc(p50)}</div></div>
  <div class="stat"><div class="stat-label">p95 (ms)</div><div class="stat-value">{_esc(p95)}</div></div>
  <div class="stat"><div class="stat-label">p99 (ms)</div><div class="stat-value">{_esc(p99)}</div></div>
</div>

<h2>Queue</h2>
{queue_err_html}
<div class="stat-grid">
  <div class="stat"><div class="stat-label">Pending</div><div class="stat-value">{queue_pending}</div></div>
  <div class="stat"><div class="stat-label">Running</div><div class="stat-value">{queue_running}</div></div>
</div>

<h2>Credentials</h2>
<table>
  <thead><tr><th>Name</th><th>Configured</th><th>Near Expiry</th><th>Expires At</th></tr></thead>
  <tbody>{cred_rows_html}</tbody>
</table>

<h2>Tool Calls (last 50, newest first)</h2>
<table>
  <thead><tr><th>Tool</th><th>Latency (ms)</th><th>Hook Result</th><th>Caller</th><th>Timestamp</th></tr></thead>
  <tbody>{tool_rows_html}</tbody>
</table>

<footer>Last refreshed: {now_utc} &nbsp;|&nbsp; Auto-refresh: 10s</footer>
</body>
</html>
"""
            return Response(content=html, media_type="text/html")

        async def metrics_endpoint(request: Request) -> Response:
            """Return Prometheus text-format metrics from the global TelemetryStore."""
            try:
                from .runtime.telemetry import TelemetryStore
                store = TelemetryStore.get_global()
                counts = store.get_counts()
                recent = store.get_recent(1000)

                # Compute latency stats
                latencies = [e.latency_ms / 1000.0 for e in recent]  # convert ms → seconds
                count_total = len(latencies)
                sum_total = sum(latencies) if latencies else 0.0

                # Compute quantiles (p50, p95, p99)
                def _quantile(sorted_data: list, q: float) -> float:
                    if not sorted_data:
                        return 0.0
                    idx = int(q * len(sorted_data))
                    idx = min(idx, len(sorted_data) - 1)
                    return sorted_data[idx]

                sorted_lat = sorted(latencies)
                p50 = _quantile(sorted_lat, 0.5)
                p95 = _quantile(sorted_lat, 0.95)
                p99 = _quantile(sorted_lat, 0.99)

                lines = [
                    "# HELP praxis_tool_calls_total Total tool calls processed",
                    "# TYPE praxis_tool_calls_total counter",
                    f"praxis_tool_calls_total {counts['tool_call_count']}",
                    "",
                    "# HELP praxis_hook_blocks_total Total §5 hook blocks",
                    "# TYPE praxis_hook_blocks_total counter",
                    f"praxis_hook_blocks_total {counts['hook_block_count']}",
                    "",
                    "# HELP praxis_circuit_breaker_trips_total Circuit breaker trip count",
                    "# TYPE praxis_circuit_breaker_trips_total counter",
                    f"praxis_circuit_breaker_trips_total {counts['circuit_breaker_trips']}",
                    "",
                    "# HELP praxis_tool_latency_seconds Tool call latency in seconds",
                    "# TYPE praxis_tool_latency_seconds summary",
                    f'praxis_tool_latency_seconds{{quantile="0.5"}} {p50:.6f}',
                    f'praxis_tool_latency_seconds{{quantile="0.95"}} {p95:.6f}',
                    f'praxis_tool_latency_seconds{{quantile="0.99"}} {p99:.6f}',
                    f"praxis_tool_latency_seconds_count {count_total}",
                    f"praxis_tool_latency_seconds_sum {sum_total:.6f}",
                    "",
                ]
                body = "\n".join(lines)
            except Exception:
                body = "# telemetry unavailable\n"

            return Response(
                content=body,
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )

        from .api import (
            ws_endpoint as _ws_endpoint,
            get_status,
            get_queue,
            post_queue,
            get_queue_task,
            delete_queue_task,
            get_approvals,
            post_approval_action,
            post_approvals_bulk,
            get_schedule,
            post_schedule,
            put_schedule_task,
            delete_schedule_task,
            post_schedule_enable,
            post_schedule_disable,
            post_schedule_run_now,
            get_wiki_search,
            get_wiki_pages,
            get_wiki_page_detail,
            get_memory,
            get_integrations,
            post_integrations_validate,
            get_soul,
            put_soul,
            get_heartbeat,
            put_heartbeat,
        )

        ui_dist = Path(__file__).parent / "ui" / "dist"

        extra_routes: list = []
        if ui_dist.exists():
            from starlette.staticfiles import StaticFiles
            extra_routes.append(
                Mount("/ui", app=StaticFiles(directory=str(ui_dist), html=True))
            )

        from .hooks_engine import make_webhook_routes
        extra_routes.extend(make_webhook_routes())

        app = Starlette(
            routes=[
                Route("/sse", endpoint=sse_endpoint, methods=["GET"]),
                Route("/metrics", endpoint=metrics_endpoint, methods=["GET"]),
                Route("/dashboard", endpoint=dashboard_endpoint, methods=["GET"]),
                WebSocketRoute("/ws", endpoint=_ws_endpoint),
                Mount(message_path, app=sse_transport.handle_post_message),
                # REST API routes
                Route("/api/status", endpoint=get_status, methods=["GET"]),
                Route("/api/queue", endpoint=get_queue, methods=["GET"]),
                Route("/api/queue", endpoint=post_queue, methods=["POST"]),
                Route("/api/queue/{task_id}", endpoint=get_queue_task, methods=["GET"]),
                Route("/api/queue/{task_id}", endpoint=delete_queue_task, methods=["DELETE"]),
                Route("/api/approvals", endpoint=get_approvals, methods=["GET"]),
                Route("/api/approvals/bulk", endpoint=post_approvals_bulk, methods=["POST"]),
                Route("/api/approvals/{approval_id}/{action}", endpoint=post_approval_action, methods=["POST"]),
                Route("/api/schedule", endpoint=get_schedule, methods=["GET"]),
                Route("/api/schedule", endpoint=post_schedule, methods=["POST"]),
                Route("/api/schedule/{task_id}/enable", endpoint=post_schedule_enable, methods=["POST"]),
                Route("/api/schedule/{task_id}/disable", endpoint=post_schedule_disable, methods=["POST"]),
                Route("/api/schedule/{task_id}/run-now", endpoint=post_schedule_run_now, methods=["POST"]),
                Route("/api/schedule/{task_id}", endpoint=put_schedule_task, methods=["PUT"]),
                Route("/api/schedule/{task_id}", endpoint=delete_schedule_task, methods=["DELETE"]),
                Route("/api/wiki/search", endpoint=get_wiki_search, methods=["GET"]),
                Route("/api/wiki/pages", endpoint=get_wiki_pages, methods=["GET"]),
                Route("/api/wiki/pages/{slug:path}", endpoint=get_wiki_page_detail, methods=["GET"]),
                Route("/api/memory", endpoint=get_memory, methods=["GET"]),
                Route("/api/integrations", endpoint=get_integrations, methods=["GET"]),
                Route("/api/integrations/validate", endpoint=post_integrations_validate, methods=["POST"]),
                Route("/api/soul", endpoint=get_soul, methods=["GET"]),
                Route("/api/soul", endpoint=put_soul, methods=["PUT"]),
                Route("/api/heartbeat", endpoint=get_heartbeat, methods=["GET"]),
                Route("/api/heartbeat", endpoint=put_heartbeat, methods=["PUT"]),
                *extra_routes,
            ]
        )

        bind_host = os.environ.get("PRAXIS_MCP_BIND", "127.0.0.1")

        import sys
        ui_url = f"http://{bind_host}:{port}/ui/" if ui_dist.exists() else "(not built)"
        sys.stderr.write(
            f"[praxis] MCP gateway listening on http://{bind_host}:{port}/sse\n"
            f"[praxis] MCP tools: {len(self._all_schemas)} registered\n"
            f"[praxis] Metrics: http://{bind_host}:{port}/metrics\n"
            f"[praxis] Dashboard: http://{bind_host}:{port}/dashboard\n"
            f"[praxis] API: http://{bind_host}:{port}/api/status\n"
            f"[praxis] WebSocket: ws://{bind_host}:{port}/ws\n"
            f"[praxis] UI: {ui_url}\n"
        )
        uvicorn.run(app, host=bind_host, port=port, log_level="warning")
