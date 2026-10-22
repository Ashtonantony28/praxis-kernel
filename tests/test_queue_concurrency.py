"""SG6: concurrent appends + claims across processes lose and duplicate nothing."""

from __future__ import annotations

import multiprocessing as mp
from collections import Counter
from pathlib import Path

from praxis.queue import Task, TaskQueue

WRITERS = 4
PER_WRITER = 2500
CLAIMERS = 2


def _writer(queue_dir: str, n: int, tag: int) -> None:
    q = TaskQueue(Path(queue_dir))
    for i in range(n):
        q.append(Task.create(f"w{tag}-{i}", priority=i % 7))


def _claimer(queue_dir: str, out: str, writers_done) -> None:
    q = TaskQueue(Path(queue_dir))
    claimed = []
    while True:
        t = q.claim()
        if t is not None:
            claimed.append(t.id)
            q.update(t.id, status="done", result="ok")
        elif writers_done.is_set() and q.claim() is None:
            break
    Path(out).write_text("\n".join(claimed))


def test_concurrent_appends_and_claims(tmp_path: Path):
    ctx = mp.get_context("fork")
    queue_dir = tmp_path / "queue"
    TaskQueue(queue_dir).ensure_dirs()
    done = ctx.Event()
    outs = [tmp_path / f"claimed{i}.txt" for i in range(CLAIMERS)]
    claimers = [ctx.Process(target=_claimer, args=(str(queue_dir), str(o), done)) for o in outs]
    writers = [ctx.Process(target=_writer, args=(str(queue_dir), PER_WRITER, i)) for i in range(WRITERS)]
    for p in claimers + writers:
        p.start()
    for p in writers:
        p.join(60)
        assert p.exitcode == 0
    done.set()
    for p in claimers:
        p.join(60)
        assert p.exitcode == 0

    claimed = [i for o in outs for i in o.read_text().split("\n") if i]
    tasks = TaskQueue(queue_dir)._read_all()
    total = WRITERS * PER_WRITER
    assert len(tasks) == total
    assert len({t.id for t in tasks}) == total
    assert len(claimed) == total
    assert not [i for i, c in Counter(claimed).items() if c > 1]
    assert set(claimed) == {t.id for t in tasks}
    assert all(t.status == "done" for t in tasks)
