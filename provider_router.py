"""Credential-safe multi-provider routing for Hermes.

The router is deliberately dependency-free and OpenAI-compatible where possible.
It keeps provider health in memory, applies bounded retries, cooldowns and daily
call budgets, and never includes credentials in exceptions or status output.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable


class ProviderUnavailable(RuntimeError):
    """A provider is not configured or is temporarily unavailable."""


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    key_env: str
    model_env: str
    default_model: str
    endpoint: str
    kind: str = "openai"


@dataclass
class ProviderState:
    failures: int = 0
    cooldown_until: float = 0.0
    calls_today: int = 0
    day: str = ""
    latency_ms: float = 1000.0


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec("groq", "GROQ_API_KEY", "GROQ_MODEL", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1/chat/completions"),
    ProviderSpec("gemini", "GOOGLE_API_KEY", "GEMINI_MODEL", "gemini-flash-latest", "https://generativelanguage.googleapis.com/v1beta/models", "gemini"),
    ProviderSpec("openrouter", "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "openai/gpt-oss-20b:free", "https://openrouter.ai/api/v1/chat/completions"),
    ProviderSpec("cerebras", "CEREBRAS_API_KEY", "CEREBRAS_MODEL", "llama-3.3-70b", "https://api.cerebras.ai/v1/chat/completions"),
    ProviderSpec("cloudflare", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_WORKERS_AI_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "", "cloudflare"),
)
SPEC_BY_NAME = {item.name: item for item in PROVIDERS}
_states: dict[str, ProviderState] = {item.name: ProviderState() for item in PROVIDERS}


def _configured(spec: ProviderSpec) -> bool:
    if spec.name == "gemini":
        return bool(os.getenv("GOOGLE_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "").strip())
    if spec.name == "cloudflare":
        return bool(os.getenv(spec.key_env, "").strip() and os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip())
    return bool(os.getenv(spec.key_env, "").strip())


def configured_providers() -> list[str]:
    return [spec.name for spec in PROVIDERS if _configured(spec)]


def _order() -> list[str]:
    raw = os.getenv("HERMES_PROVIDER_ORDER", "groq,gemini,openrouter,cerebras,cloudflare")
    requested = [part.strip().lower() for part in raw.split(",") if part.strip()]
    return [name for name in requested if name in SPEC_BY_NAME]


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _state(name: str) -> ProviderState:
    state = _states[name]
    today = _today()
    if state.day != today:
        state.day = today
        state.calls_today = 0
    return state


def _max_calls() -> int:
    try:
        return max(0, int(os.getenv("HERMES_PROVIDER_MAX_CALLS_PER_DAY", "0")))
    except ValueError:
        return 0


def _cooldown_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("HERMES_PROVIDER_COOLDOWN_SECONDS", "60")))
    except ValueError:
        return 60.0


def _timeout() -> float:
    try:
        return max(1.0, min(120.0, float(os.getenv("HERMES_PROVIDER_TIMEOUT_SECONDS", "30"))))
    except ValueError:
        return 30.0


def _retries() -> int:
    try:
        return max(0, min(2, int(os.getenv("HERMES_PROVIDER_RETRIES", "1"))))
    except ValueError:
        return 1


def _headers(spec: ProviderSpec, key: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {key}"}
    if spec.name == "gemini":
        headers = {"x-goog-api-key": key}
    if spec.name == "openrouter":
        headers.update({"HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://office.kasapink.com"), "X-Title": "Henka Hermes"})
    return headers


def _post(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "HermesMonitor/1.0", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_timeout()) as response:
        return json.loads(response.read().decode("utf-8"))


def _openai_call(spec: ProviderSpec, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, bool]:
    key = os.getenv(spec.key_env, "").strip()
    payload = {"model": os.getenv(spec.model_env, spec.default_model), "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    data = _post(spec.endpoint, payload, _headers(spec, key))
    choice = data["choices"][0]
    return str(choice["message"]["content"]).strip(), choice.get("finish_reason") == "length"


def _gemini_call(spec: ProviderSpec, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, bool]:
    key = os.getenv("GOOGLE_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()
    contents = [{"role": "model" if item["role"] == "assistant" else "user", "parts": [{"text": item["content"]}]} for item in messages if item["role"] != "system"]
    model = urllib.parse.quote(os.getenv(spec.model_env, spec.default_model), safe="@")
    data = _post(f"{spec.endpoint}/{model}:generateContent", {"systemInstruction": {"parts": [{"text": messages[0]["content"]}]}, "contents": contents, "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens}}, _headers(spec, key))
    candidate = data["candidates"][0]
    text = "".join(part.get("text", "") for part in candidate["content"]["parts"]).strip()
    return text, candidate.get("finishReason") == "MAX_TOKENS"


def _cloudflare_call(spec: ProviderSpec, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, bool]:
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    key = os.getenv(spec.key_env, "").strip()
    model = urllib.parse.quote(os.getenv(spec.model_env, spec.default_model), safe="@/:.-")
    data = _post(f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}", {"messages": messages, "max_tokens": max_tokens, "temperature": 0.2}, {"Authorization": f"Bearer {key}"})
    result = data.get("result") or {}
    text = result.get("response") or result.get("text") or result.get("content")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Cloudflare AI returned no text")
    return text.strip(), False


def _call(spec: ProviderSpec, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, bool]:
    if spec.kind == "gemini":
        return _gemini_call(spec, messages, max_tokens)
    if spec.kind == "cloudflare":
        return _cloudflare_call(spec, messages, max_tokens)
    return _openai_call(spec, messages, max_tokens)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, urllib.error.URLError, OSError, ValueError, KeyError)):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    return False


def _ranked() -> list[ProviderSpec]:
    now = time.monotonic()
    ranked: list[tuple[float, ProviderSpec]] = []
    for index, name in enumerate(_order()):
        spec = SPEC_BY_NAME[name]
        if not _configured(spec):
            continue
        state = _state(name)
        if state.cooldown_until > now:
            continue
        limit = _max_calls()
        if limit and state.calls_today >= limit:
            continue
        score = state.latency_ms + state.failures * 1000 + index * 25
        ranked.append((score, spec))
    return [spec for _, spec in sorted(ranked, key=lambda item: item[0])]


def generate(messages: list[dict[str, str]], max_tokens: int) -> tuple[str, str, bool]:
    """Generate with intelligent ranking and bounded fallback."""
    last_error: BaseException | None = None
    for spec in _ranked():
        state = _state(spec.name)
        attempts = _retries() + 1
        for attempt in range(attempts):
            started = time.monotonic()
            try:
                text, truncated = _call(spec, messages, max_tokens)
                state.calls_today += 1
                state.latency_ms = (state.latency_ms * 0.7) + ((time.monotonic() - started) * 1000 * 0.3)
                state.failures = 0
                state.cooldown_until = 0.0
                return text, spec.name, truncated
            except Exception as exc:
                last_error = exc
                if attempt + 1 < attempts and _retryable(exc):
                    continue
                state.failures += 1
                state.cooldown_until = time.monotonic() + _cooldown_seconds()
                break
    if last_error:
        raise ProviderUnavailable("Semua provider AI yang tersedia sedang gagal atau terkena cooldown") from None
    raise ProviderUnavailable("Belum ada provider AI yang dikonfigurasi")


def reset_state() -> None:
    """Test hook; does not touch credentials or persistent configuration."""
    for state in _states.values():
        state.failures = 0
        state.cooldown_until = 0.0
        state.calls_today = 0
        state.day = _today()
        state.latency_ms = 1000.0


def status(check: bool = False) -> dict[str, str]:
    """Return safe provider status; optional live checks never expose response bodies."""
    result: dict[str, str] = {}
    for spec in PROVIDERS:
        if not _configured(spec):
            result["google_ai_studio" if spec.name == "gemini" else spec.name] = "NOT_CONFIGURED"
            continue
        state = _state(spec.name)
        if state.cooldown_until > time.monotonic():
            result[spec.name] = "COOLDOWN"
        elif check:
            try:
                _call(spec, [{"role": "system", "content": "Reply with OK."}, {"role": "user", "content": "OK"}], 8)
                result[spec.name] = "VALID"
            except Exception as exc:
                code = getattr(exc, "code", None)
                result[spec.name] = f"HTTP_{code}" if isinstance(code, int) else "ERROR"
        else:
            result[spec.name] = "CONFIGURED"
    return result
