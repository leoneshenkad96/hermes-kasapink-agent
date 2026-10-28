# Hermes free-tier multi-provider — staging (28 Oct 2026)

This extends the **custom Discord Python bot**, not the installed NousResearch Hermes CLI or the Virtual Office worker. Its existing Groq-first/Gemini-fallback behavior remains unchanged until explicitly enabled.

## Validated paths
- Groq / Gemini: existing code; legacy primary+fallback preserved.
- OpenRouter: `https://openrouter.ai/api/v1/chat/completions`, only model IDs ending in `:free`. OpenRouter free accounts currently advertise **50 requests/day**; local conservative cap is **40 HTTP calls/day**, including continuations and failed attempts.
- Cerebras: `https://api.cerebras.ai/v1/chat/completions`. Select a model available to *your free-tier account*. Requires `CEREBRAS_FREE_TIER_CONFIRMED=true`; local cap **60 HTTP calls/day**.
- Cloudflare Workers AI: `https://api.cloudflare.com/client/v4/accounts/<account_id>/ai/v1/chat/completions`. Only reviewed `@cf/meta/llama-3.1-8b-instruct` is accepted and requires `CLOUDFLARE_FREE_PLAN_CONFIRMED=true`; local cap **20 HTTP calls/day**. Cloudflare's free allocation is measured in **Neurons**, not request counts. Use Workers **Free** (not Paid).
- Ollama: **localhost only** `http://127.0.0.1:11434/v1/chat/completions`; `:cloud` models rejected. Requires downloaded local model and `HERMES_OLLAMA_ENABLED=true`.
- OpenCode Free: **EXCLUDED**. OpenCode docs explicitly say free chat models are only available through OpenCode; Hermes requests get HTTP 403. OpenCode paid/Go should NOT be enabled in this free-tier project.

## Rollout: no production changes until QA/approval
1. Work on this branch and test with `python -m unittest discover -s tests -p 'test_multi_provider.py' -v`. Run existing `python -m unittest discover -s tests -v` from Python environment with requirements installed.
2. Back up existing local `.env` securely; never commit it. Add only the new variables from `.env.example`. Keep `HERMES_MULTI_PROVIDER_ENABLED=false`.
3. Create and verify OpenRouter, Cerebras, Cloudflare accounts/keys on **free** plans. Fetch exact current free OpenRouter model ID ending `:free` from the provider catalog. Ollama local hardware availability must be checked before enabling it.
4. Set API keys in the local, ignored `.env`, **never in chat or Github**. Confirm `HERMES_NEW_CLOUD_DATA_ALLOWED=true` only after deciding what Discord context is allowed to be transmitted to NEW vendors. This router does **not** automatically redact user messages; restrict sensitive ERP/RouterOS content. Prefer sample/synthetic prompts in initial test.
5. Dry-run on non-production laptop using a synthetic prompt; verify route and actual provider attribution in existing Discord audit trail. Test 429/401/timeout via mocks; test when all new providers are unavailable and when Groq/Gemini succeed.
6. Only after regression and owner approval, set `HERMES_MULTI_PROVIDER_ENABLED=true` and `HERMES_MULTI_PROVIDER_TASK_ROUTING=true`, restart *only* the bot process through its normal supervised procedure. Do not modify `~/.hermes/config.yaml`, database, credentials, ERP, or MikroTik.
7. Rollback: set `HERMES_MULTI_PROVIDER_ENABLED=false` and restart bot; Groq → Gemini legacy sequence restored.

## Task routing (heuristic, not independent autonomous agents)
- General conversation → Groq, Gemini, Ollama, OpenRouter, Cerebras, Cloudflare.
- Coding → OpenRouter, Gemini, Groq, Cerebras, Ollama, Cloudflare.
- Network/ops logs → Cerebras, Groq, Gemini, Cloudflare, Ollama, OpenRouter.
- Security → Cloudflare, Gemini, Groq, Cerebras, Ollama, OpenRouter.
- Analysis → Gemini, OpenRouter, Groq, Cerebras, Ollama, Cloudflare.

Only configured, consented providers are candidates. On request failure the router tries the next configured provider. No request triggers ERP writes, shell tools, or automatic external action: this route handles **natural-language replies only**. `summarize()`, monitoring and deterministic commands retain their previous implementations.

## Important limits
The JSON usage counter is a local **best-effort** throttle, **not** an enforceable hard spending limit under multiple processes/restarts, nor does it know Cloudflare Neuron expenditure. Keep all cloud provider accounts strictly on free plans, disable automatic billing upgrades, and verify actual usage from each provider dashboard. Re-check model availability and free-tier terms before activation.

The Virtual Office `scripts/hermes-cloud-worker.mjs` uses a separate Hermes Python bridge and **is not modified** by this branch. Its provider routing must be integrated/tested independently before claiming Virtual Office multi-provider support.

## Source references
- https://openrouter.ai/pricing
- https://inference-docs.cerebras.ai/support/pricing
- https://developers.cloudflare.com/workers-ai/platform/pricing/
- https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/
- https://docs.ollama.com/api/openai-compatibility
- https://dev.opencode.ai/v2/docs/console/harnesses/hermes
