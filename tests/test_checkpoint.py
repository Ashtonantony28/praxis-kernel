"""Tests for praxis/checkpoint.py — checkpoint write/resume for multi-stage tasks."""

from __future__ import annotations

from pathlib import Path

import pytest

from praxis.checkpoint import Checkpoint, CheckpointStore


@pytest.fixture
def store(tmp_path: Path) -> CheckpointStore:
    return CheckpointStore(tmp_path / "queue")


# ---------- Checkpoint dataclass ----------


def test_checkpoint_next_stage_index():
    cp = Checkpoint(task_id="t1", stages=["a", "b", "c"])
    assert cp.next_stage_index() == 0

    cp.mark_stage_done(0, "result-a")
    assert cp.next_stage_index() == 1

    cp.mark_stage_done(1, "result-b")
    assert cp.next_stage_index() == 2

    cp.mark_stage_done(2, "result-c")
    assert cp.next_stage_index() is None


def test_checkpoint_is_complete():
    cp = Checkpoint(task_id="t1", stages=["a", "b"])
    assert not cp.is_complete()

    cp.mark_stage_done(0, "r0")
    assert not cp.is_complete()

    cp.mark_stage_done(1, "r1")
    assert cp.is_complete()


def test_checkpoint_mark_stage_done_idempotent():
    cp = Checkpoint(task_id="t1", stages=["a"])
    cp.mark_stage_done(0, "r")
    cp.mark_stage_done(0, "r2")  # duplicate — should not add again
    assert cp.completed == [0]
    assert cp.results["0"] == "r2"  # result updated


def test_checkpoint_final_result():
    cp = Checkpoint(task_id="t1", stages=["scout", "builder"])
    cp.mark_stage_done(0, "scout output")
    cp.mark_stage_done(1, "builder output")

    result = cp.final_result()
    assert "scout output" in result
    assert "builder output" in result
    assert "[Stage 0: scout]" in result
    assert "[Stage 1: builder]" in result


def test_checkpoint_final_result_partial():
    cp = Checkpoint(task_id="t1", stages=["a", "b", "c"])
    cp.mark_stage_done(0, "r0")
    cp.mark_stage_done(2, "r2")  # skip stage 1

    result = cp.final_result()
    assert "r0" in result
    assert "r2" in result


def test_checkpoint_roundtrip():
    cp = Checkpoint(task_id="t1", stages=["a", "b"])
    cp.mark_stage_done(0, "done-a")
    data = cp.to_dict()
    cp2 = Checkpoint.from_dict(data)
    assert cp2.task_id == "t1"
    assert cp2.completed == [0]
    assert cp2.results["0"] == "done-a"


# ---------- CheckpointStore ----------


def test_store_save_and_load(store: CheckpointStore):
    cp = Checkpoint(task_id="xyz", stages=["s1", "s2"])
    cp.mark_stage_done(0, "result-s1")
    store.save(cp)

    loaded = store.load("xyz")
    assert loaded is not None
    assert loaded.task_id == "xyz"
    assert loaded.completed == [0]
    assert loaded.results["0"] == "result-s1"


def test_store_load_nonexistent(store: CheckpointStore):
    assert store.load("missing") is None


def test_store_exists(store: CheckpointStore):
    assert not store.exists("xyz")
    cp = Checkpoint(task_id="xyz", stages=["s1"])
    store.save(cp)
    assert store.exists("xyz")


def test_store_remove(store: CheckpointStore):
    cp = Checkpoint(task_id="xyz", stages=["s1"])
    store.save(cp)
    assert store.exists("xyz")

    store.remove("xyz")
    assert not store.exists("xyz")


def test_store_remove_nonexistent(store: CheckpointStore):
    store.remove("nonexistent")  # should not raise


def test_store_overwrite(store: CheckpointStore):
    cp = Checkpoint(task_id="xyz", stages=["s1", "s2"])
    store.save(cp)

    cp.mark_stage_done(0, "r0")
    store.save(cp)

    loaded = store.load("xyz")
    assert loaded.completed == [0]
