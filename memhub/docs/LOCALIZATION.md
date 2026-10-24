# MemHub Localization

MemHub ships Simplified Chinese (`zh-CN`), English (`en`), and Spanish (`es`).
Interface language is independent from conversation, memory, extraction, and
search language.

## Architecture

- `@one-memory/i18n` owns supported locales, catalogs, locale resolution,
  interpolation, plural rules, and formatting helpers.
- Web owns the active UI preference and reacts immediately without remounting
  product state.
- Electron persists the preference before Web starts so startup screens,
  dialogs, menus, and tray items use the same locale.
- `system` is a preference, not a locale. It resolves a supported system
  language and falls back to English.
- User-authored text, memory content, raw conversations, paths, source names,
  model names, endpoints, identifiers, and external error bodies are never
  translated.

The runtime uses `i18next` for CLDR plural selection, fallback, and
interpolation. Date, number, list, and relative-time formatting use the
platform `Intl` APIs.

## Catalog rules

1. Use stable, domain-qualified keys such as `settings.language.title` and
   `sdk.memory.delete.confirm`.
2. Keep the same key and placeholders in every locale.
3. Do not use Chinese or English source text as a new key.
4. Do not concatenate translated fragments when one message with parameters
   can express the sentence.
5. Keep technical identifiers outside translated messages unless they are
   explicit interpolation parameters.
6. Use the language's own name in selectors: `简体中文`, `English`, `Español`.
7. Do not use national flags to represent languages.

Existing compatibility keys are retained while screens move to semantic keys;
new code must not add another source-text key.

## Adding a language

1. Add its BCP 47 locale to `SUPPORTED_LOCALES`.
2. Add a complete catalog with placeholder parity.
3. Add locale resolution and fallback tests.
4. Add the self-name to the language selector.
5. Verify Web and Electron-native surfaces.
6. Test long text with a pseudo-locale or deliberately expanded translations.
7. For right-to-left languages, set `dir` and use logical CSS properties.

A locale contribution should primarily change catalog and locale metadata.
Large unrelated component or product-behavior changes belong in separate PRs.

## Verification

Run focused tests while developing:

```bash
bash scripts/run-tests-isolated.sh pnpm \
  --filter @one-memory/i18n exec vitest run
bash scripts/run-tests-isolated.sh pnpm \
  --filter @one-memory/web exec vitest run <affected tests>
bash scripts/run-tests-isolated.sh pnpm \
  --filter @one-memory/desktop exec vitest run <affected tests>
```

Catalog tests enforce key coverage, placeholder parity, plural behavior, and
safe fallback. Component tests must verify that changing locale does not repeat
network requests, lose form state, broaden authorization, or modify user data.
