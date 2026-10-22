"""SG8: queue events cross processes via .praxis/events.jsonl to the gateway's /ws."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import WebSocketRoute

from praxis.api import ws_endpoint
from praxis.event_bus import TASK_COMPLETED, event_log_lifespan

REPO = Path(__file__).resolve().parent.parent

_QUEUE_PROCESS = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from praxis import queue_runner
    from praxis.event_bus import enable_event_log
    from praxis.queue import Task, TaskQueue

    ws = Path(sys.argv[1])
    enable_event_log(ws)
    q = TaskQueue(ws / ".praxis" / "queue")
    q.ensure_dirs()
    q.append(Task.create(prompt="cross-process"))
    task = q.claim()

    class Orch:
        def run(self, prompt, **kw):
            return "ok"

    queue_runner._run_atomic_task(task, Orch(), q)
    print(task.id)
    """
)


async def _gateway_round_trip(ws_root: Path, env: dict) -> tuple[str, list[dict]]:
    app = Starlette(
        routes=[WebSocketRoute("/ws", endpoint=ws_endpoint)],
        lifespan=event_log_lifespan(ws_root),
    )

    life_in: asyncio.Queue = asyncio.Queue()
    life_out: asyncio.Queue = asyncio.Queue()
    await life_in.put({"type": "lifespan.startup"})
    life = asyncio.create_task(
        app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, life_in.get, life_out.put)
    )
    assert (await asyncio.wait_for(life_out.get(), 5))["type"] == "lifespan.startup.complete"

    ws_in: asyncio.Queue = asyncio.Queue()
    ws_out: asyncio.Queue = asyncio.Queue()
    await ws_in.put({"type": "websocket.connect"})
    scope = {
        "type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "path": "/ws",
        "raw_path": b"/ws", "root_path": "", "query_string": b"", "headers": [],
        "server": ("testserver", 80), "client": ("127.0.0.1", 1), "subprotocols": [], "state": {},
    }
    conn = asyncio.create_task(app(scope, ws_in.get, ws_out.put))
    assert (await asyncio.wait_for(ws_out.get(), 5))["type"] == "websocket.accept"
    await asyncio.sleep(0.05)

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", _QUEUE_PROCESS, str(ws_root),
        env=env, cwd=str(REPO),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), 25)
    assert proc.returncode == 0, err.decode()[-2000:]
    task_id = out.decode().strip().splitlines()[-1]

    received: list[dict] = []
    try:
        while not any(e.get("type") == TASK_COMPLETED for e in received):
            msg = await asyncio.wait_for(ws_out.get(), 5)
            received.append(json.loads(msg["text"]))
    finally:
        conn.cancel()
        await asyncio.gather(conn, return_exceptions=True)
        await life_in.put({"type": "lifespan.shutdown"})
        await asyncio.wait_for(life_out.get(), 5)
        await asyncio.gather(life, return_exceptions=True)
    return task_id, received


def test_task_completed_reaches_ws_client_in_other_process(tmp_path, monkeypatch):
    monkeypatch.delenv("PRAXIS_UI_TOKEN", raising=False)
    monkeypatch.setenv("PRAXIS_WORKSPACE_ROOT", str(tmp_path))
    env = {
        **os.environ,
        "PRAXIS_WORKSPACE_ROOT": str(tmp_path),
        "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")])),
    }
    task_id, received = asyncio.run(_gateway_round_trip(tmp_path, env))
    completed = [e for e in received if e["type"] == TASK_COMPLETED]
    assert completed == [{"type": TASK_COMPLETED, "data": {"task_id": task_id}}]
    log = (tmp_path / ".praxis" / "events.jsonl").read_text().splitlines()
    assert any(json.loads(line)["type"] == TASK_COMPLETED for line in log)
