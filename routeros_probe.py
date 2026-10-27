"""Minimal read-only RouterOS API-SSL probe.

Credentials are read from Hermes' local .env and never printed.
"""

from __future__ import annotations

import os
import socket
import ssl
from pathlib import Path
from typing import Dict, List


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    if length < 0x4000:
        return bytes([(length >> 8) | 0x80, length & 0xFF])
    if length < 0x200000:
        return bytes([(length >> 16) | 0xC0, (length >> 8) & 0xFF, length & 0xFF])
    if length < 0x10000000:
        return bytes([(length >> 24) | 0xE0, (length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF])
    return b"\xF0" + length.to_bytes(4, "big")


def read_length(sock: socket.socket) -> int:
    first = recv_exact(sock, 1)
    if not first:
        raise RuntimeError("connection closed")
    value = first[0]
    if value < 0x80:
        return value
    if value < 0xC0:
        return ((value & 0x3F) << 8) | recv_exact(sock, 1)[0]
    if value < 0xE0:
        return ((value & 0x1F) << 16) | int.from_bytes(recv_exact(sock, 2), "big")
    if value < 0xF0:
        return ((value & 0x0F) << 24) | int.from_bytes(recv_exact(sock, 3), "big")
    return int.from_bytes(recv_exact(sock, 4), "big")


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError("connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_sentence(sock: socket.socket, words: List[str]) -> None:
    payload = b"".join(encode_length(len(word.encode())) + word.encode() for word in words) + b"\x00"
    sock.sendall(payload)


def receive_sentence(sock: socket.socket) -> list[str]:
    words: list[str] = []
    while True:
        size = read_length(sock)
        if size == 0:
            return words
        words.append(recv_exact(sock, size).decode("utf-8", errors="replace"))


def read_reply(sock: socket.socket) -> list[dict[str, str]]:
    replies: list[dict[str, str]] = []
    while True:
        sentence = receive_sentence(sock)
        if not sentence:
            continue
        kind = sentence[0]
        row: dict[str, str] = {"!type": kind}
        for word in sentence[1:]:
            if word.startswith("=") and "=" in word[1:]:
                key, value = word[1:].split("=", 1)
                row[key] = value
        replies.append(row)
        if kind in {"!done", "!trap", "!fatal"}:
            return replies


def run_command(sock: socket.socket, command: str, properties: str = "") -> list[dict[str, str]]:
    words = [command]
    if properties:
        words.append(f"=.proplist={properties}")
    send_sentence(sock, words)
    return read_reply(sock)


def wrap_verified_tls(raw: socket.socket, host: str, ca_file: str = "") -> ssl.SSLSocket:
    """Wrap RouterOS API-SSL with normal certificate and hostname validation."""
    context = ssl.create_default_context(cafile=ca_file or None)
    return context.wrap_socket(raw, server_hostname=host)


def probe_routeros(
    host: str, port: int, tls: bool, username: str, password: str, ca_file: str = "",
    include_clients: bool = False,
) -> dict[str, object]:
    """Run only the approved read-only RouterOS commands and return their data."""
    if not username or not password:
        raise RuntimeError("RouterOS credentials not configured")
    with socket.create_connection((host, port), timeout=15) as raw:
        sock_context = wrap_verified_tls(raw, host, ca_file) if tls else raw
        with sock_context as sock:
            send_sentence(sock, ["/login", f"=name={username}", f"=password={password}"])
            auth = read_reply(sock)
            if any(row.get("!type") in {"!trap", "!fatal"} for row in auth):
                raise RuntimeError("RouterOS login failed")
            commands = {
                "resource": "/system/resource/print",
                "interfaces": "/interface/print",
                "routes": "/ip/route/print",
            }
            results = {name: run_command(sock, command) for name, command in commands.items()}
            traps = [name for name, rows in results.items() if any(row.get("!type") in {"!trap", "!fatal"} for row in rows)]
            if traps:
                raise RuntimeError("RouterOS read-only command failed")
            if include_clients:
                # These commands are invoked only for an explicit user query. Each
                # is independently best-effort because not every router uses DHCP
                # or a bridge. No configuration or traffic capture is performed.
                client_commands = {
                    "dhcp_leases": ("/ip/dhcp-server/lease/print", "address,mac-address,host-name,status,server,last-seen,dynamic,disabled,comment"),
                    "arp": ("/ip/arp/print", "address,mac-address,interface,complete,dynamic,disabled,comment"),
                    "bridge_hosts": ("/interface/bridge/host/print", "mac-address,on-interface,bridge,age,local,external,static,vid"),
                }
                client_results: dict[str, object] = {}
                for name, (command, properties) in client_commands.items():
                    try:
                        rows = run_command(sock, command, properties)
                        if any(row.get("!type") in {"!trap", "!fatal"} for row in rows):
                            client_results[name] = {"status": "UNAVAILABLE"}
                        else:
                            client_results[name] = {
                                "status": "OK",
                                "items": [row for row in rows if row.get("!type") == "!re"],
                            }
                    except (OSError, RuntimeError, TimeoutError):
                        client_results[name] = {"status": "UNAVAILABLE"}
                results["clients"] = client_results
            return {"status": "OK", "snapshot": results}


def main() -> int:
    env_path = Path(os.getenv("HERMES_ENV_FILE", str(Path.home() / "AppData/Local/hermes/.env")))
    env = load_env(env_path)
    host = os.getenv("MIKROTIK_HOST") or env.get("MIKROTIK_HOST", "172.16.254.1")
    port = int(os.getenv("MIKROTIK_API_PORT") or env.get("MIKROTIK_API_PORT", "8729"))
    tls = (os.getenv("MIKROTIK_API_TLS") or env.get("MIKROTIK_API_TLS", "true")).lower() in {"1", "true", "yes", "on"}
    ca_file = env.get("MIKROTIK_TLS_CA_FILE", "")
    username = env.get("MIKROTIK_USERNAME", "")
    password = env.get("MIKROTIK_PASSWORD", "")
    if not username or not password:
        print("MISSING_CREDENTIALS")
        return 2

    with socket.create_connection((host, port), timeout=15) as raw:
        sock_context = wrap_verified_tls(raw, host, ca_file) if tls else raw
        with sock_context as sock:
            send_sentence(sock, ["/login", f"=name={username}", f"=password={password}"])
            auth = read_reply(sock)
            if any(row.get("!type") in {"!trap", "!fatal"} for row in auth):
                print("LOGIN_FAILED")
                return 3

            commands = {
                "resource": "/system/resource/print",
                "interfaces": "/interface/print",
                "routes": "/ip/route/print",
            }
            results: Dict[str, list[dict[str, str]]] = {name: run_command(sock, command) for name, command in commands.items()}
            traps = [name for name, rows in results.items() if any(row.get("!type") in {"!trap", "!fatal"} for row in rows)]
            print("LOGIN_OK")
            print("READ_ONLY_COMMANDS_OK=" + str(not traps))
            print("COMMANDS=" + ",".join(commands))
            if traps:
                print("FAILED_COMMANDS=" + ",".join(traps))
                return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

