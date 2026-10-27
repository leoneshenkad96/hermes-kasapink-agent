"""Read-only Kasapink/MikroTik snapshot collector with local baseline diffing.

Default behavior is dry-run and makes no network calls. Live mode performs only
HTTP GET requests to explicitly configured endpoints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import routeros_probe


HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36 HermesKasapinkMonitor/0.1",
    "Accept": "application/json",
}


def load_local_env() -> None:
    """Load simple KEY=VALUE entries from the collector's adjacent .env file."""
    env_path = Path(__file__).with_name(".env")
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


VOLATILE_KEYS = {
    "captured_at", "capturedAt", "generatedAt",
    "uptime", "cpu-load", "free-memory", "free-hdd-space",
    "write-sect-since-reboot", "write-sect-total",
    "rx-byte", "tx-byte", "rx-packet", "tx-packet",
    "fp-rx-byte", "fp-tx-byte", "fp-rx-packet", "fp-tx-packet",
    "tx-queue-drop", "tx-drop", "tx-error", "fp-rps-drop",
    "last-link-up-time", "link-downs", "cpu-frequency",
}


def comparable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: comparable(item)
            for key, item in value.items()
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [comparable(item) for item in value]
    return value


def stable_hash(value: Any) -> str:
    raw = json.dumps(comparable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _detail_value(value: Any) -> str:
    if value is None:
        return "tidak ada"
    if isinstance(value, (dict, list)):
        value = json.dumps(comparable(value), ensure_ascii=False, sort_keys=True)
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text[:180] + ("…" if len(text) > 180 else "")


def _item_key(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    for key in ("id", "key", "name", "code", "sku"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _collect_detail_changes(before: Any, after: Any, path: str, changes: list[str], limit: int) -> None:
    if len(changes) >= limit:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(set(before) | set(after), key=str)
        for key in keys:
            if key in VOLATILE_KEYS:
                continue
            child = f"{path} › {key}" if path else str(key)
            _collect_detail_changes(before.get(key), after.get(key), child, changes, limit)
            if len(changes) >= limit:
                return
        return
    if isinstance(before, list) and isinstance(after, list):
        old_items = {_item_key(item): item for item in before if _item_key(item) is not None}
        new_items = {_item_key(item): item for item in after if _item_key(item) is not None}
        if old_items or new_items:
            for key in sorted(set(old_items) | set(new_items)):
                child = f"{path} › {key}" if path else key
                if key not in old_items:
                    changes.append(f"{child}: ditambahkan")
                elif key not in new_items:
                    changes.append(f"{child}: dihapus")
                else:
                    _collect_detail_changes(old_items[key], new_items[key], child, changes, limit)
                if len(changes) >= limit:
                    return
            return
        if comparable(before) != comparable(after):
            changes.append(f"{path}: {_detail_value(before)} → {_detail_value(after)}")
        return
    if comparable(before) != comparable(after):
        changes.append(f"{path}: {_detail_value(before)} → {_detail_value(after)}")


def describe_changes(previous: dict[str, Any] | None, current: dict[str, Any], limit: int = 24) -> list[str]:
    """Return bounded, human-readable before/after details for a snapshot change."""
    if not previous:
        return []
    old_systems = previous.get("systems", {}) if isinstance(previous, dict) else {}
    new_systems = current.get("systems", {}) if isinstance(current, dict) else {}
    changes: list[str] = []
    labels = {"kasapink": "ERP Henka", "core_gateway": "Core Gateway", "lan_controller": "LAN Controller"}
    for name in sorted(set(old_systems) | set(new_systems)):
        old = old_systems.get(name) or {}
        new = new_systems.get(name) or {}
        label = labels.get(name, name)
        if old.get("status") != new.get("status"):
            changes.append(f"{label} › status: {_detail_value(old.get('status'))} → {_detail_value(new.get('status'))}")
        if len(changes) >= limit:
            break
        _collect_detail_changes(old.get("state", {}), new.get("state", {}), f"{label} › data", changes, limit)
        if len(changes) >= limit:
            break
        _collect_detail_changes(old.get("snapshot", {}), new.get("snapshot", {}), f"{label} › snapshot", changes, limit)
    return changes[:limit]


def request_json(
    url: str,
    headers: dict[str, str],
    auth: tuple[str, str] | None = None,
    verify_tls: bool = True,
    opener: urllib.request.OpenerDirector | None = None,
) -> Any:
    # urllib does not provide a custom TLS context here; reject insecure mode
    # rather than silently weakening transport security.
    if not verify_tls and url.lower().startswith("https://"):
        raise RuntimeError("MIKROTIK_VERIFY_TLS=false is not supported by this safe collector")
    request_headers = {**HTTP_HEADERS, **headers}
    request = urllib.request.Request(url, headers=request_headers, method="GET")
    if auth:
        import base64

        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    attempts = max(1, min(4, int(os.getenv("HERMES_GET_RETRIES", "3"))))
    for attempt in range(attempts):
        try:
            response_context = opener.open(request, timeout=15) if opener else urllib.request.urlopen(request, timeout=15)
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                raise
            time.sleep(min(2.0, 0.5 * (2 ** attempt)))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(2.0, 0.5 * (2 ** attempt)))
    with response_context as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"HTTP {response.status} from configured endpoint")
        return json.loads(response.read().decode("utf-8"))


def secure_cookie_opener() -> urllib.request.OpenerDirector:
    """Use a normal verified TLS context with a portable CA bundle when available."""
    handlers: list[Any] = [urllib.request.HTTPCookieProcessor()]
    try:
        import certifi
    except ImportError:
        return urllib.request.build_opener(*handlers)
    context = ssl.create_default_context(cafile=certifi.where())
    handlers.insert(0, urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


def login_kasapink(base_url: str, username: str, password: str) -> tuple[urllib.request.OpenerDirector, dict[str, str]]:
    """Create a cookie-backed ERP session without exposing the session token."""
    opener = secure_cookie_opener()
    payload = json.dumps({"username": username, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/login",
        data=payload,
        headers={**HTTP_HEADERS, "Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(request, timeout=15) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"HTTP {response.status} from ERP login")
        data = json.loads(response.read().decode("utf-8"))

    headers = {"Accept": "application/json"}
    # Cookie auth is preferred. Support bearer auth too, without persisting it.
    token = data.get("token") if isinstance(data, dict) else None
    if isinstance(token, str) and token:
        headers["Authorization"] = f"Bearer {token}"
    return opener, headers


def collect(dry_run: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"captured_at": now(), "systems": {}}
    if dry_run:
        result["mode"] = "dry-run"
        result["systems"] = {
            "kasapink": {"status": "NOT_CHECKED", "reason": "dry-run"},
            "mikrotik": {"status": "NOT_CHECKED", "reason": "dry-run"},
        }
        return result

    erp_base_url = os.getenv("KASAPINK_BASE_URL", "").strip().rstrip("/")
    erp_health_url = os.getenv("KASAPINK_HEALTH_URL", "").strip()
    erp_state_url = os.getenv("KASAPINK_STATE_URL", "").strip()
    erp_username = os.getenv("KASAPINK_USERNAME", "").strip()
    erp_password = os.getenv("KASAPINK_PASSWORD", "")
    if erp_base_url:
        try:
            if not erp_username or not erp_password:
                raise RuntimeError("Kasapink credentials not configured")
            opener, headers = login_kasapink(erp_base_url, erp_username, erp_password)
            health_url = erp_health_url or f"{erp_base_url}/api/healthz"
            state_url = erp_state_url or f"{erp_base_url}/api/erp/state"
            result["systems"]["kasapink"] = {
                "status": "OK",
                "health": request_json(health_url, headers, opener=opener),
                "state": request_json(state_url, headers, opener=opener),
            }
        except Exception as exc:  # network/API failures are reported, not fatal
            result["systems"]["kasapink"] = {"status": "ERROR", "error": type(exc).__name__}
    elif erp_health_url:
        try:
            headers = {"Accept": "application/json"}
            token = os.getenv("KASAPINK_BEARER_TOKEN", "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            result["systems"]["kasapink"] = {"status": "OK", "health": request_json(erp_health_url, headers)}
        except Exception as exc:  # network/API failures are reported, not fatal
            result["systems"]["kasapink"] = {"status": "ERROR", "error": type(exc).__name__}
    else:
        result["systems"]["kasapink"] = {"status": "NOT_CONFIGURED"}

    username = os.getenv("MIKROTIK_USERNAME", "").strip()
    password = os.getenv("MIKROTIK_PASSWORD", "")
    targets = {
        "core_gateway": {
            "host": os.getenv("MIKROTIK_CORE_HOST", os.getenv("MIKROTIK_HOST", "10.10.10.1")).strip(),
            "port": os.getenv("MIKROTIK_CORE_PORT", os.getenv("MIKROTIK_API_PORT", "8728")),
            "tls": os.getenv("MIKROTIK_CORE_TLS", os.getenv("MIKROTIK_API_TLS", "false")).lower() in {"1", "true", "yes", "on"},
        },
        "lan_controller": {
            "host": os.getenv("MIKROTIK_LAN_HOST", "192.168.80.1").strip(),
            "port": os.getenv("MIKROTIK_LAN_PORT", "8728"),
            "tls": os.getenv("MIKROTIK_LAN_TLS", "false").lower() in {"1", "true", "yes", "on"},
        },
    }
    for name, target in targets.items():
        try:
            port = int(target["port"])
            if not 1 <= port <= 65535:
                raise ValueError("port out of range")
            result["systems"][name] = routeros_probe.probe_routeros(
                target["host"], port, target["tls"], username, password,
                os.getenv("MIKROTIK_TLS_CA_FILE", "").strip(),
            )
        except Exception as exc:
            result["systems"][name] = {"status": "ERROR", "error": type(exc).__name__}
    return result


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="do not make network calls")
    parser.add_argument("--live", action="store_true", help="perform configured read-only GET requests")
    parser.add_argument("--state", default=os.getenv("MONITOR_STATE_FILE", "./data/baseline.json"))
    args = parser.parse_args()
    if args.dry_run == args.live:
        parser.error("choose exactly one of --dry-run or --live")

    state_path = Path(args.state)
    previous = load_json(state_path)
    current = collect(dry_run=args.dry_run)
    current_hash = stable_hash(current.get("systems", {}))
    previous_hash = stable_hash(previous.get("systems", {})) if previous else None
    errors = [
        name for name, system in current.get("systems", {}).items()
        if system.get("status") == "ERROR"
    ]
    previous_invalid = bool(previous and any(
        system.get("status") == "ERROR"
        for system in previous.get("systems", {}).values()
        if isinstance(system, dict)
    ))
    if current.get("mode") == "dry-run":
        event = "DRY_RUN"
    elif errors:
        event = "ERROR"
    elif previous is None or previous_invalid:
        event = "BASELINE_CREATED"
    elif current_hash == previous_hash:
        event = "NO_CHANGE"
    else:
        event = "CHANGE_DETECTED"

    output = {"event": event, "current_hash": current_hash, "previous_hash": previous_hash, "snapshot": current}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if args.live and event in {"BASELINE_CREATED", "CHANGE_DETECTED", "NO_CHANGE"}:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if event != "ERROR" else 2


if __name__ == "__main__":
    sys.exit(main())

