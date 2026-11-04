# Daily memory model usage

Approved scope: count physical provider requests (including retries), retain provider-reported input, cached-input, output and reasoning tokens, and show daily memory usage in the Memory page. Extend this to other vendors with editable per-model pricing, currency and a billing-console URL.

## Recording

Reuse the content-free model_request_audit table and host HTTP transport. Establish the audit context around SDK writes and opt SDK transports into recording other SDK model calls (retrieval, evolve and graph building). Preserve missing usage as unknown. Keep diagnostics working without detailed logging. Do not save prompts, answers or credentials. Streaming memory answers must remain streaming and request a final usage chunk where supported.

## Reporting and pricing

Add an authenticated daily report over all matching audit rows, independent of diagnostic detail limits. Bucket by an explicit IANA timezone (browser default), with today, seven-day and thirty-day views. Include per-model breakdown and unknown/in-flight counts. Empty days before recording are not historical zero-cost claims.

Use a local JSON pricing configuration keyed by profile and model, with input, cached-input and output rates per million tokens, currency and an optional HTTPS billing URL. Built-in DeepSeek official pricing is versioned and restricted to its official endpoint and known models. Peak-period holiday ambiguity is displayed as a cost range. Custom prices are applied as current-price estimates; different currencies are never added together. Missing prices or usage are shown as partial/unknown, never as free. Official billing is authoritative; no balance is inferred from token counts.

## UI and verification

Add a compact expandable usage panel on the Memory page, matching existing controls. Include daily rows, models, pricing controls, billing links, loading/error/retry and a historical coverage notice. Poll only while the page/panel is active, through the shared scheduler. Verify transport retries, worker/SDK context propagation, missing usage, timezones, all-row aggregation, pricing and mixed currencies, API auth/validation, UI ranges/errors/configuration, then run lint, typechecks and a production build. Use mocked providers, isolated data directories and no paid live calls.

## Implementation sequence

1. Repair SDK auditing and streaming-safe response accounting.
2. Add local pricing settings and daily usage service/API.
3. Add the Memory page panel, shared API types and translations.
4. Extend existing relevant tests, verify in isolation, review the diff and open a PR against main.

## Official balance query

Read DeepSeek's documented `GET https://api.deepseek.com/user/balance` from Core
using the configured secure credential. Display official available, granted and
top-up amounts separately from local cost estimates. Restrict the adapter to
the official HTTPS host/port, reject redirects, cache successful reads for 60s
and failures for 10s, and rate-limit manual refresh to 5s. Bind cache entries to
profile revision, never persist keys or error bodies, and show unknown on error.
The balance is account-wide; do not label its changes as daily MemHub charges.
