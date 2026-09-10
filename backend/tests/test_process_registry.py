import asyncio

import pytest

from app.services.process_registry import ProcessRegistry


@pytest.fixture()
def reg():
    return ProcessRegistry()


def test_not_busy_initially(reg):
    assert not reg.is_busy("c1")


@pytest.mark.asyncio
async def test_busy_while_task_registered_even_before_process_starts(reg):
    """Regression test: a real bug let a second `send` slip past is_busy()
    during the window between task creation and the subprocess actually
    spawning (DB lookups, attachment resolution, etc. all await first)."""

    async def pending_forever():
        await asyncio.sleep(10)

    task = asyncio.ensure_future(pending_forever())
    reg.register_task("c1", task)
    try:
        assert reg.is_busy("c1")  # no process registered yet — must still report busy
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_not_busy_after_task_completes(reg):
    async def quick():
        return "done"

    task = asyncio.ensure_future(quick())
    reg.register_task("c1", task)
    await task
    assert not reg.is_busy("c1")


def test_stop_with_nothing_running_returns_false(reg):
    assert reg.stop("nonexistent") is False


@pytest.mark.asyncio
async def test_stop_cancels_registered_task(reg):
    async def pending_forever():
        await asyncio.sleep(10)

    task = asyncio.ensure_future(pending_forever())
    reg.register_task("c1", task)
    await asyncio.sleep(0)  # let the task actually start

    stopped = reg.stop("c1")
    assert stopped is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_clear_removes_both_proc_and_task(reg):
    task = asyncio.ensure_future(asyncio.sleep(0))
    reg.register_task("c1", task)
    await task
    reg.clear("c1")
    assert not reg.is_busy("c1")
