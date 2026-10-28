"""Opt-in free-tier-conscious routing for the existing Hermes Discord bot.

No changes to Hermes CLI config, Groq/Gemini keys, or default chat behavior.
Provider API destinations are fixed and never supplied by untrusted prompts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import urllib.error
import urllib.request

_LOCK = threading.RLock()
_NEW_CLOUD = ('openrouter', 'cerebras', 'cloudflare')
_ALL = ('groq', 'gemini', 'openrouter', 'cerebras', 'cloudflare', 'ollama')
_DAILY_CAPS = {'openrouter': 40, 'cerebras': 60, 'cloudflare': 20}
_URLS = {
    'openrouter': 'https://openrouter.ai/api/v1/chat/completions',
    'cerebras': 'https://api.cerebras.ai/v1/chat/completions',
    'ollama': 'http://127.0.0.1:11434/v1/chat/completions',
}


def _yes(name: str) -> bool:
    return os.getenv(name, '').strip().lower() in ('1', 'true', 'yes', 'on')


def enabled() -> bool:
    return _yes('HERMES_MULTI_PROVIDER_ENABLED')


def _spec(name: str):
    if name == 'groq':
        return (os.getenv('GROQ_API_KEY', '').strip(), os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b'))
    if name == 'gemini':
        return ((os.getenv('GOOGLE_API_KEY', '').strip() or os.getenv('GEMINI_API_KEY', '').strip()), os.getenv('GEMINI_MODEL', 'gemini-flash-latest'))
    if name == 'openrouter':
        key, model = os.getenv('OPENROUTER_API_KEY', '').strip(), os.getenv('OPENROUTER_MODEL', '').strip()
        return (key if model.endswith(':free') else '', model)
    if name == 'cerebras':
        key, model = os.getenv('CEREBRAS_API_KEY', '').strip(), os.getenv('CEREBRAS_MODEL', '').strip()
        return (key if model and _yes('CEREBRAS_FREE_TIER_CONFIRMED') else '', model)
    if name == 'cloudflare':
        key = os.getenv('CLOUDFLARE_API_TOKEN', '').strip()
        account = os.getenv('CLOUDFLARE_ACCOUNT_ID', '').strip()
        model = os.getenv('CLOUDFLARE_AI_MODEL', '').strip()
        valid_account = bool(re.fullmatch(r'[a-fA-F0-9]{32}', account))
        # Explicit free-plan model allowlist. Never send arbitrary paid-only models.
        safe_model = model in ('@cf/meta/llama-3.1-8b-instruct',)
        return (key if valid_account and safe_model and _yes('CLOUDFLARE_FREE_PLAN_CONFIRMED') else '', model)
    if name == 'ollama':
        model = os.getenv('OLLAMA_MODEL', '').strip()
        return ('local' if _yes('HERMES_OLLAMA_ENABLED') and model else '', model)
    return ('', '')


def configured() -> list[str]:
    if not enabled():
        return []
    allowed = _yes('HERMES_NEW_CLOUD_DATA_ALLOWED')
    return [name for name in _ALL if _spec(name)[0] and (name not in _NEW_CLOUD or allowed)]


def task_type(messages: list[dict]) -> str:
    text = next((str(m.get('content', '')) for m in reversed(messages) if m.get('role') == 'user'), '').lower()
    if re.search(r'\b(code|coding|python|typescript|react|github|pull request|patch|debug|program|bug|sql)\b', text):
        return 'coding'
    if re.search(r'\b(log|logs|routeros|mikrotik|latency|network|jaringan|dns|server|monitor|cpu|ram)\b', text):
        return 'operations'
    if re.search(r'\b(security|audit|vulnerab|cve|firewall|serangan|insiden|keamanan)\b', text):
        return 'security'
    if re.search(r'\b(analisis|analyse|analysis|strategi|laporan|reasoning|hitung)\b', text):
        return 'analysis'
    return 'general'


_PRIORITIES = {
    'coding': ('openrouter', 'gemini', 'groq', 'cerebras', 'ollama', 'cloudflare'),
    'operations': ('cerebras', 'groq', 'gemini', 'cloudflare', 'ollama', 'openrouter'),
    'security': ('cloudflare', 'gemini', 'groq', 'cerebras', 'ollama', 'openrouter'),
    'analysis': ('gemini', 'openrouter', 'groq', 'cerebras', 'ollama', 'cloudflare'),
    'general': ('groq', 'gemini', 'ollama', 'openrouter', 'cerebras', 'cloudflare'),
}


def route_for(messages: list[dict]) -> list[str]:
    available = set(configured())
    order = _PRIORITIES[task_type(messages)] if _yes('HERMES_MULTI_PROVIDER_TASK_ROUTING') else _ALL
    return [name for name in order if name in available]


def _claim_budget(name: str) -> bool:
    """Best-effort local quota; not a provider-side billing guarantee.

    Count failed requests and continuations. Provider account must remain on free tier.
    """
    if name not in _DAILY_CAPS:
        return True
    root = Path(os.getenv('HERMES_PROVIDER_USAGE_FILE', './data/provider-usage.json'))
    day = datetime.now(timezone.utc).date().isoformat()
    with _LOCK:
        try:
            data = json.loads(root.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        if data.get('day') != day:
            data = {'day': day, 'counts': {}}
        counts = data['counts']
        count = int(counts.get(name, 0))
        if count >= _DAILY_CAPS[name]:
            return False
        counts[name] = count + 1
        root.parent.mkdir(parents=True, exist_ok=True)
        temp = root.with_name(root.name + '.tmp')
        temp.write_text(json.dumps(data), encoding='utf-8')
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, root)
        return True


def _openai_complete(name: str, messages: list[dict], max_tokens: int) -> tuple[str, bool]:
    key, model = _spec(name)
    if not key or not model or not _claim_budget(name):
        raise RuntimeError('PROVIDER_UNAVAILABLE_OR_LIMITED')
    if name == 'cloudflare':
        account = os.getenv('CLOUDFLARE_ACCOUNT_ID', '').strip()
        url = f'https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/chat/completions'
    else:
        url = _URLS[name]
    headers = {'Content-Type': 'application/json', 'User-Agent': 'HermesFreeRouter/1.0'}
    if name != 'ollama':
        headers['Authorization'] = f'Bearer {key}'
    payload = {'model': model, 'messages': messages, 'max_tokens': max_tokens, 'temperature': 0.2}
    request = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.loads(response.read(1_000_000).decode('utf-8'))
    choice = result['choices'][0]
    content = choice['message'].get('content')
    if not isinstance(content, str) or not content.strip():
        raise ValueError('EMPTY_RESPONSE')
    return content.strip(), choice.get('finish_reason') == 'length'


def complete(messages: list[dict], continuation, groq, gemini, max_tokens: int = 1200):
    """Return (answer, provider) or (None, None) on exhaustion; no raw errors logged."""
    for name in route_for(messages):
        if name == 'groq':
            handler = lambda ms: groq(ms, max_tokens)
        elif name == 'gemini':
            handler = lambda ms: gemini(ms, max_tokens)
        else:
            handler = lambda ms, n=name: _openai_complete(n, ms, max_tokens)
        try:
            return continuation(handler, messages), name
        except (RuntimeError, OSError, ValueError, KeyError, IndexError, TypeError, urllib.error.HTTPError, urllib.error.URLError):
            continue
    return None, None
