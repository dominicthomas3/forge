"""Forge Event System — lightweight observer pattern for dashboard integration.

The orchestrator emits events at key pipeline points. The dashboard (or any
other consumer) subscribes via callbacks. Events are fire-and-forget — the
pipeline runs identically whether anyone is listening or not.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("forge.events")


class EventType(str, Enum):
    """All event types emitted by the orchestrator."""
    CYCLE_STARTED = "cycle_started"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    STAGE_SKIPPED = "stage_skipped"      # Resumed from checkpoint
    VERDICT = "verdict"
    CODEBASE_LOADED = "codebase_loaded"
    GIT_CHECKPOINT = "git_checkpoint"
    CYCLE_COMPLETED = "cycle_completed"
    PIPELINE_STARTED = "pipeline_started"
    PIPELINE_FINISHED = "pipeline_finished"
    LOG = "log"
    ERROR = "error"


# Stage names in pipeline order — maps stage number to human-readable name
STAGE_NAMES: dict[int, str] = {
    1: "Jim Analysis",
    2: "Deep Think",
    3: "Claude Implement",
    4: "Claude Review",
    5: "Consensus",
    6: "Apply Fixes",
    7: "Stress Test",
}


@dataclass
class ForgeEvent:
    """A single pipeline event."""
    event_type: EventType
    cycle: int = 0
    stage: int | None = None
    stage_name: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        if self.stage is not None and not self.stage_name:
            self.stage_name = STAGE_NAMES.get(self.stage, f"Stage {self.stage}")


class EventBus:
    """Thread-safe event bus with sync and async callback support.

    Sync callbacks are invoked directly (for logging, file writing, etc.).
    Async callbacks are scheduled on the event loop (for NiceGUI WebSocket push).
    An asyncio.Queue is also maintained for consumers that prefer pull-based access.

    The event loop is captured via set_loop() before cross-thread use,
    avoiding deprecated asyncio.get_event_loop() calls from worker threads.
    """

    def __init__(self):
        self._sync_callbacks: list[Callable[[ForgeEvent], None]] = []
        self._async_callbacks: list[Callable[[ForgeEvent], Any]] = []
        self._queue: asyncio.Queue[ForgeEvent] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the event loop for cross-thread scheduling.

        Must be called from the main/asyncio thread before the orchestrator
        starts emitting events from its executor thread.
        """
        self._loop = loop

    def subscribe(self, callback: Callable[[ForgeEvent], None]) -> None:
        """Register a synchronous callback. Called on every emit."""
        self._sync_callbacks.append(callback)

    def subscribe_async(self, callback: Callable[[ForgeEvent], Any]) -> None:
        """Register an async callback. Scheduled on the running event loop."""
        self._async_callbacks.append(callback)

    def enable_queue(self) -> asyncio.Queue[ForgeEvent]:
        """Enable the pull-based queue and return it. Dashboard can await queue.get()."""
        self._queue = asyncio.Queue(maxsize=1000)
        return self._queue

    def emit(self, event: ForgeEvent) -> None:
        """Emit an event to all subscribers. Safe to call from any thread.

        Iterates snapshot copies of callback lists to avoid race conditions
        if subscribe() is called concurrently from another thread.
        """
        # Sync callbacks — direct invocation (snapshot for thread safety)
        for cb in list(self._sync_callbacks):
            try:
                cb(event)
            except Exception as e:
                logger.debug("Event callback error: %s", e)

        # Async callbacks — schedule on the captured event loop
        loop = self._loop
        if loop and loop.is_running():
            for cb in list(self._async_callbacks):
                try:
                    loop.call_soon_threadsafe(loop.create_task, cb(event))
                except RuntimeError:
                    pass  # Loop closed — skip

        # Queue — non-blocking put (bounded to 1000)
        if self._queue is not None:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Drop newest if full — dashboard can handle gaps

    def emit_simple(
        self,
        event_type: EventType,
        cycle: int = 0,
        stage: int | None = None,
        **data: Any,
    ) -> None:
        """Convenience method — constructs ForgeEvent and emits it."""
        self.emit(ForgeEvent(
            event_type=event_type,
            cycle=cycle,
            stage=stage,
            data=data,
        ))
