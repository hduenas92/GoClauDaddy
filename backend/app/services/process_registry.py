"""Tracks the one live `claude` subprocess (and its asyncio.Task) per conversation.

Prevents two overlapping sends on the same conversation and gives /api/stop
something concrete to kill.
"""

import asyncio
import time

from app.logging_setup import get_logger

log = get_logger("process_registry")


class ConversationBusyError(Exception):
    pass


class ProcessRegistry:
    def __init__(self) -> None:
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._starts: dict[str, float] = {}

    def is_busy(self, conversation_id: str) -> bool:
        # A task can be registered well before its subprocess actually spawns
        # (DB lookups, attachment resolution, etc. all await first) - checking
        # only _procs leaves a real race window where a second send could slip
        # through before the first one's process registers.
        task = self._tasks.get(conversation_id)
        if task is not None and not task.done():
            return True
        return conversation_id in self._procs

    def register_process(self, conversation_id: str, proc: asyncio.subprocess.Process) -> None:
        self._procs[conversation_id] = proc

    def register_task(self, conversation_id: str, task: asyncio.Task) -> None:
        self._tasks[conversation_id] = task
        self._starts[conversation_id] = time.monotonic()

    def list_turns(self) -> list[dict[str, object]]:
        """In-flight turns: a registered, not-done task counts even before its subprocess spawns."""
        now = time.monotonic()
        turns: list[dict[str, object]] = []
        for conversation_id in set(self._tasks) | set(self._procs):
            task = self._tasks.get(conversation_id)
            if task is not None and task.done():
                continue
            turns.append({
                "conversation_id": conversation_id,
                "elapsed_s": max(0.0, now - self._starts.get(conversation_id, now)),
            })
        return turns

    def clear(self, conversation_id: str) -> None:
        self._procs.pop(conversation_id, None)
        self._tasks.pop(conversation_id, None)
        self._starts.pop(conversation_id, None)

    def stop(self, conversation_id: str) -> bool:
        """Kills the running subprocess/task for a conversation, if any. Returns True if something was stopped."""
        stopped = False
        proc = self._procs.get(conversation_id)
        if proc is not None:
            try:
                proc.kill()
                stopped = True
                log.info("Killed claude subprocess for conversation %s", conversation_id)
            except ProcessLookupError:
                pass
        task = self._tasks.get(conversation_id)
        if task is not None and not task.done():
            task.cancel()
            stopped = True
        return stopped

    def shutdown_all(self) -> None:
        """Called on app shutdown — terminate everything instead of leaving orphans."""
        for conv_id in list(self._procs):
            self.stop(conv_id)


registry = ProcessRegistry()
