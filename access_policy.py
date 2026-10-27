"""Discord role checks: shared Hermes features vs owner-only MikroTik data."""

from __future__ import annotations


def parse_user_ids(raw: str) -> set[str]:
    return {part.strip() for part in raw.replace(";", ",").split(",") if part.strip().isdigit()}


def role_user_ids(owner_id: str, configured_ids: str) -> set[str]:
    """An unconfigured privileged role defaults to the owner only."""
    return {owner_id} | parse_user_ids(configured_ids) if owner_id.isdigit() else set()


def mikrotik_only_command(kind: str, target: str = "") -> bool:
    """Whether a routed request can expose any MikroTik-specific data."""
    if kind == "diff":
        return True  # baseline diff includes router changes
    if kind == "status":
        return target in {"core_gateway", "lan_controller", "core", "lan", "all", ""}
    if kind == "isp_status":
        return True
    return False

