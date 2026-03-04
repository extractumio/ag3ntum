"""Base class for background processing loops.

Provides start/stop lifecycle management with configurable intervals
and error recovery. Subclasses implement only ``_tick()``.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class BaseProcessor:
    """Background asyncio task that calls ``_tick()`` at a fixed interval."""

    def __init__(
        self,
        name: str,
        interval_seconds: int,
        error_sleep_seconds: int = 60,
    ) -> None:
        self._name = name
        self._interval = interval_seconds
        self._error_sleep = error_sleep_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            logger.warning("%s already running", self._name)
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("%s started (interval=%ds)", self._name, self._interval)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("%s stopped", self._name)

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                if not self._running:
                    break
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("%s error: %s", self._name, e)
                await asyncio.sleep(self._error_sleep)

    async def _tick(self) -> None:
        """Override this method with the work to perform each cycle."""
        raise NotImplementedError
