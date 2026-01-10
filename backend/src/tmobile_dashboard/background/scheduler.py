"""Background task scheduler with proper lifecycle management.

This module provides a scheduler for long-running background tasks with:
- Graceful startup and shutdown
- Task state tracking
- Error handling and recovery
- Monitoring hooks
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

import structlog

log = structlog.get_logger()


class TaskState(str, Enum):
    """Background task lifecycle states."""

    PENDING = "pending"  # Not yet started
    RUNNING = "running"  # Actively running
    STOPPING = "stopping"  # Graceful shutdown initiated
    STOPPED = "stopped"  # Fully stopped
    FAILED = "failed"  # Stopped due to error


@dataclass
class TaskStatus:
    """Status information for a background task."""

    name: str
    state: TaskState = TaskState.PENDING
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    last_run_at: datetime | None = None
    run_count: int = 0
    error_count: int = 0
    last_error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


class BackgroundTask(ABC):
    """Abstract base class for background tasks.

    Subclass this to create tasks that run periodically or continuously.
    """

    def __init__(self, name: str, interval_seconds: float = 1.0):
        self.name = name
        self.interval_seconds = interval_seconds
        self._status = TaskStatus(name=name)
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

    @property
    def status(self) -> TaskStatus:
        return self._status

    @property
    def is_running(self) -> bool:
        return self._status.state == TaskState.RUNNING

    @abstractmethod
    async def run_once(self) -> None:
        """Execute one iteration of the task.

        Override this to implement task logic.
        """
        pass

    async def on_start(self) -> None:
        """Called when task starts. Override for setup logic."""
        pass

    async def on_stop(self) -> None:
        """Called when task stops. Override for cleanup logic."""
        pass

    async def _run_loop(self) -> None:
        """Internal run loop with error handling."""
        assert self._stop_event is not None

        try:
            await self.on_start()
            self._status.state = TaskState.RUNNING
            self._status.started_at = datetime.utcnow()
            log.info("background_task_started", task=self.name)

            while not self._stop_event.is_set():
                try:
                    await self.run_once()
                    self._status.run_count += 1
                    self._status.last_run_at = datetime.utcnow()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._status.error_count += 1
                    self._status.last_error = str(e)
                    log.error(
                        "background_task_error",
                        task=self.name,
                        error=str(e),
                        error_count=self._status.error_count,
                    )

                # Wait for next iteration or stop signal
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.interval_seconds
                    )
                except asyncio.TimeoutError:
                    pass  # Normal timeout, continue loop

        except asyncio.CancelledError:
            log.info("background_task_cancelled", task=self.name)
        finally:
            self._status.state = TaskState.STOPPED
            self._status.stopped_at = datetime.utcnow()
            await self.on_stop()
            log.info(
                "background_task_stopped",
                task=self.name,
                run_count=self._status.run_count,
                error_count=self._status.error_count,
            )

    async def start(self) -> None:
        """Start the background task."""
        if self._status.state == TaskState.RUNNING:
            log.warning("background_task_already_running", task=self.name)
            return

        self._stop_event = asyncio.Event()
        self._status.state = TaskState.PENDING
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the background task gracefully."""
        if self._stop_event is None or self._task is None:
            return

        self._status.state = TaskState.STOPPING
        self._stop_event.set()

        try:
            await asyncio.wait_for(self._task, timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("background_task_force_cancel", task=self.name)
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


class BackgroundScheduler:
    """Manages multiple background tasks with coordinated lifecycle.

    Usage:
        scheduler = BackgroundScheduler()
        scheduler.add_task(SignalPollingTask())
        scheduler.add_task(DataCleanupTask())

        async with scheduler.lifespan():
            # Tasks run here
            await some_work()

        # Or manually:
        await scheduler.start_all()
        try:
            ...
        finally:
            await scheduler.stop_all()
    """

    def __init__(self):
        self._tasks: dict[str, BackgroundTask] = {}
        self._started = False
        self._on_status_change: Callable[[str, TaskStatus], None] | None = None

    def add_task(self, task: BackgroundTask) -> None:
        """Register a background task."""
        if task.name in self._tasks:
            raise ValueError(f"Task '{task.name}' already registered")
        self._tasks[task.name] = task
        log.info("background_task_registered", task=task.name)

    def remove_task(self, name: str) -> None:
        """Remove a registered task (must be stopped first)."""
        if name in self._tasks:
            if self._tasks[name].is_running:
                raise RuntimeError(f"Cannot remove running task '{name}'")
            del self._tasks[name]

    def get_task(self, name: str) -> BackgroundTask | None:
        """Get a task by name."""
        return self._tasks.get(name)

    def get_all_status(self) -> dict[str, TaskStatus]:
        """Get status of all tasks."""
        return {name: task.status for name, task in self._tasks.items()}

    async def start_all(self) -> None:
        """Start all registered tasks."""
        if self._started:
            log.warning("scheduler_already_started")
            return

        log.info("scheduler_starting", task_count=len(self._tasks))
        for task in self._tasks.values():
            await task.start()
        self._started = True
        log.info("scheduler_started")

    async def stop_all(self, timeout: float = 10.0) -> None:
        """Stop all tasks gracefully."""
        if not self._started:
            return

        log.info("scheduler_stopping", task_count=len(self._tasks))

        # Calculate per-task timeout
        per_task_timeout = timeout / max(len(self._tasks), 1)

        # Stop all tasks concurrently
        await asyncio.gather(
            *[task.stop(per_task_timeout) for task in self._tasks.values()],
            return_exceptions=True,
        )

        self._started = False
        log.info("scheduler_stopped")

    async def lifespan(self):
        """Context manager for scheduler lifecycle.

        Usage with FastAPI:
            @asynccontextmanager
            async def lifespan(app: FastAPI):
                async with scheduler.lifespan():
                    yield
        """
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _lifespan():
            await self.start_all()
            try:
                yield self
            finally:
                await self.stop_all()

        return _lifespan()

    @property
    def is_healthy(self) -> bool:
        """Check if all tasks are running normally."""
        if not self._started:
            return False
        return all(
            task.status.state == TaskState.RUNNING for task in self._tasks.values()
        )
