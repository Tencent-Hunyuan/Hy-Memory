/** The one shared implementation of the UI/CLI write contracts.
 *
 * Scope: request normalisation, receipt parsing, and the write
 * operation contract. It deliberately does **not** carry runtime transport,
 * Core routing, UI state, or service lifecycle — the surfaces keep thin
 * adapters for those. Both the web renderer and the CLI import from here so a
 * single parser judges the same body on both sides instead of two hand-written
 * copies drifting apart.
 */

export * from "./memory-add-operation.js";
export * from "./memory-processing-operation.js";
export * from "./model-operation.js";
export * from "./model-profile-operation.js";
export * from "./source-import-operation.js";
export * from "./source-sync-operation.js";
