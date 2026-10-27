"""Least-privilege guard for Hermes command and worker layers."""

from __future__ import annotations

from enum import StrEnum


class PermissionLevel(StrEnum):
    READ_ONLY = "read-only"
    DRAFT = "draft"
    EXECUTE = "execute"


class PermissionDenied(PermissionError):
    pass


def level_for(kind: str, *, approved: bool = False) -> PermissionLevel:
    if kind in {"template", "mutation_blocked", "local_action_proposal", "engineering_security_task"}:
        return PermissionLevel.EXECUTE if approved else PermissionLevel.DRAFT
    if kind in {"approve", "approved", "external_approve", "job_run"}:
        return PermissionLevel.EXECUTE
    return PermissionLevel.READ_ONLY


def require(level: PermissionLevel, allowed: PermissionLevel) -> None:
    order = {PermissionLevel.READ_ONLY: 0, PermissionLevel.DRAFT: 1, PermissionLevel.EXECUTE: 2}
    if order[level] > order[allowed]:
        raise PermissionDenied(f"permission level {level} requires {allowed}")


def can_request(kind: str, level: PermissionLevel, *, approved: bool = False) -> bool:
    required = level_for(kind, approved=approved)
    order = {PermissionLevel.READ_ONLY: 0, PermissionLevel.DRAFT: 1, PermissionLevel.EXECUTE: 2}
    return order[level] >= order[required]
