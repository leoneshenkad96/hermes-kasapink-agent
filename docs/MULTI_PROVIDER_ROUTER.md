# Hermes multi-provider router

Hermes keeps Groq and Google Gemini as the existing primary providers and adds
OpenRouter Free, Cerebras, and Cloudflare Workers AI as optional fallbacks.
Ollama and Claude are intentionally not part of this configuration.

## Routing behavior

`provider_router.py` ranks configured providers by the requested order, recent
latency, failure count, cooldown state, and daily call budget. A failed request
uses a bounded retry count, opens a short in-memory circuit cooldown, and then
continues to the next eligible provider. The router returns only the selected
provider name; credentials and response bodies are never included in status
output or error messages.

The default order is:

`groq,gemini,openrouter,cerebras,cloudflare`

Override it with `HERMES_PROVIDER_ORDER`. Provider model names, timeout,
retry, cooldown, and daily budget are environment settings documented in
`.env.example`.

## AI Employee compatibility

Hermes Discord commands continue to call `llm_chat.chat()` and therefore use
the shared router. The Cloudflare Office keeps employee identity, role, and
provider policy separate from the selected model. Failover changes the
provider, never the employee identity or authorization scope.

## Verification status

- Router deterministic tests: pass.
- Python syntax check: pass.
- Live provider checks: pending until the corresponding credentials are
  available in the staging environment.
- Discord integration suite: requires the repository's declared
  `discord.py` dependency in the test environment.
