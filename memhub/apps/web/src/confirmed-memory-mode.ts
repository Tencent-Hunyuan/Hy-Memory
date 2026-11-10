import type {
  HomeProjectionCapabilities,
  MemoryPipelineMode,
} from "@one-memory/contracts";
import { useSyncExternalStore } from "react";

let confirmedMode: MemoryPipelineMode | null = null;
let confirmedCapabilities: HomeProjectionCapabilities | null = null;
let revision = 0;
let issuedSequence = 0;
let committedSequence = 0;
const listeners = new Set<() => void>();

export interface ConfirmedModeTicket {
  owner: "home" | "settings" | "onboarding";
  sequence: number;
}

export function issueConfirmedModeTicket(
  owner: ConfirmedModeTicket["owner"],
): ConfirmedModeTicket {
  return { owner, sequence: ++issuedSequence };
}

export function isConfirmedModeTicketCurrent(
  ticket: ConfirmedModeTicket,
): boolean {
  return ticket.sequence >= committedSequence;
}

function capabilitiesFor(mode: MemoryPipelineMode): HomeProjectionCapabilities {
  return {
    content_plane: mode === "lite" ? "raw" : "memory",
    generation_required: mode !== "lite",
    graph_enabled: mode === "ultra",
    evolve_available: mode === "ultra",
  };
}

export function commitConfirmedMemoryMode(
  mode: MemoryPipelineMode,
  ticket: ConfirmedModeTicket,
  capabilities: HomeProjectionCapabilities = capabilitiesFor(mode),
): boolean {
  if (ticket.sequence < committedSequence) return false;
  committedSequence = ticket.sequence;
  if (
    mode === confirmedMode &&
    capabilities.content_plane === confirmedCapabilities?.content_plane &&
    capabilities.generation_required ===
      confirmedCapabilities.generation_required &&
    capabilities.graph_enabled === confirmedCapabilities.graph_enabled &&
    capabilities.evolve_available === confirmedCapabilities.evolve_available
  )
    return true;
  confirmedMode = mode;
  confirmedCapabilities = capabilities;
  revision += 1;
  for (const listener of listeners) listener();
  return true;
}

export function confirmedMemoryModeSnapshot() {
  return {
    mode: confirmedMode,
    capabilities: confirmedCapabilities,
    revision,
  };
}

let cached = confirmedMemoryModeSnapshot();
function snapshot() {
  if (
    cached.mode !== confirmedMode ||
    cached.capabilities !== confirmedCapabilities ||
    cached.revision !== revision
  )
    cached = confirmedMemoryModeSnapshot();
  return cached;
}

export function useConfirmedMemoryMode() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    snapshot,
    snapshot,
  );
}

export function resetConfirmedMemoryModeForTests() {
  confirmedMode = null;
  confirmedCapabilities = null;
  revision = 0;
  issuedSequence = 0;
  committedSequence = 0;
  cached = confirmedMemoryModeSnapshot();
  listeners.clear();
}
