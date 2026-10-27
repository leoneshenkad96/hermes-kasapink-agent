"""Async worker dispatcher with explicit registration and a shared-state lock."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


Worker = Callable[..., Awaitable[Any] | Any]


class UnknownWorker(KeyError):
    pass


class WorkerDispatcher:
    def __init__(self) -> None:
        self._workers: dict[str, Worker] = {}
        self._lock = asyncio.Lock()

    def register(self, name: str, worker: Worker) -> None:
        key = name.strip().lower()
        if not key or key in self._workers:
            raise ValueError(f"worker name is invalid or already registered: {name!r}")
        self._workers[key] = worker

    def has(self, name: str) -> bool:
        return name.strip().lower() in self._workers

    async def dispatch(self, name: str, *args: Any, **kwargs: Any) -> Any:
        worker = self._workers.get(name.strip().lower())
        if worker is None:
            raise UnknownWorker(name)
        async with self._lock:
            result = worker(*args, **kwargs)
            return await result if asyncio.iscoroutine(result) else result
