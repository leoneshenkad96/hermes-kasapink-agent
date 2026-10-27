"""Bounded, read-only Discord DNS preflight for clearer reconnect behavior."""

from __future__ import annotations

import socket
import time
from typing import Any


DEFAULT_HOSTS = ("discord.com", "gateway.discord.gg")


def resolve_hosts(hosts: tuple[str, ...] = DEFAULT_HOSTS) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for host in hosts:
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            result[host] = True
        except OSError:
            result[host] = False
    return result


def wait_for_dns(attempts: int = 3, delay_seconds: float = 1.0) -> dict[str, Any]:
    """Retry DNS briefly before gateway login; never changes DNS settings."""
    attempts = max(1, min(3, int(attempts)))
    last: dict[str, bool] = {}
    for attempt in range(1, attempts + 1):
        last = resolve_hosts()
        if all(last.values()):
            return {"ready": True, "attempt": attempt, "hosts": last}
        if attempt < attempts:
            time.sleep(max(0.0, min(5.0, delay_seconds * attempt)))
    return {"ready": False, "attempt": attempts, "hosts": last}
