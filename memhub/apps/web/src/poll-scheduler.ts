/** One scheduler for every periodic read.
 *
 * Before this module each page opened its own `setInterval`, which produced two
 * problems: the same data surface was polled by several timers (a library read
 * could happen twice per tick from two different effects), and the
 * hidden-tab / backoff / cleanup rules were re-implemented at every call site
 * and drifted.
 *
 * The key is the data surface, not the caller. Two subscribers that name the
 * same key share **one** timer, running at the fastest interval anyone asked
 * for; when the last subscriber leaves, the timer stops. That is what makes
 * "one source per surface" structural rather than a review checklist.
 *
 * No `EventSource`/`WebSocket` and no callback bus: this only de-duplicates and
 * paces polling, which is what the measured experience gate needs (click
 * feedback and page switches are driven by user actions, not by poll cadence).
 */

export type PollKey =
  | "sources"
  | "imports"
  | "incremental-sync"
  | "processing"
  | "memory-library"
  | "memory-detail"
  | "map-library"
  | "conversations"
  | "applications"
  | "calls"
  | "model-profiles"
  | "note-evidence"
  | "note-processing"
  | "health";

export interface PollTask {
  key: PollKey;
  intervalMs: number;
  /** Fire-and-forget refresh for a surface only this caller polls. */
  run?(): void | Promise<void>;
  /** Shared read: the scheduler runs this **once per tick** for the key and
   * hands the value to every subscriber's `onValue`. Use this when several
   * pages read the same Core endpoint, so one tick cannot produce two reads. */
  fetch?(): Promise<unknown>;
  onValue?(value: unknown): void;
  /** Skip while the document is hidden. Defaults to true. */
  pauseWhenHidden?: boolean;
}

interface Entry {
  key: PollKey;
  intervalMs: number;
  timer: ReturnType<typeof setTimeout> | null;
  subscribers: Set<PollTask>;
  stopped: boolean;
}

const entries = new Map<PollKey, Entry>();
/** Per-task in-flight guard: a slow reader must not queue ticks on itself. */
const inFlight = new WeakSet<PollTask>();
/** Per-surface guard for the shared `fetch`, so one tick makes one request. */
const sharedInFlight = new Set<PollKey>();

/** Subscribe one caller to a surface. Returns the unsubscribe function. */
export function subscribePoll(task: PollTask): () => void {
  if (typeof window === "undefined") return () => {};
  const entry = entries.get(task.key) ?? {
    key: task.key,
    intervalMs: task.intervalMs,
    timer: null,
    subscribers: new Set<PollTask>(),
    stopped: false,
  };
  entries.set(task.key, entry);
  entry.subscribers.add(task);
  // Fastest subscriber wins: a surface that needs 3s must not be slowed to 12s
  // by another caller that is happy with 12s.
  const previousInterval = entry.intervalMs;
  entry.intervalMs = Math.min(previousInterval, task.intervalMs);
  entry.stopped = false;
  // A pending timer was armed with the old interval. When the faster
  // subscriber arrives (or the fastest one leaves and the interval grows),
  // that timer must be re-armed or the surface keeps the stale cadence.
  if (entry.timer !== null && entry.intervalMs !== previousInterval) {
    clearTimeout(entry.timer);
    entry.timer = null;
  }
  schedule(entry);
  return () => {
    const current = entries.get(task.key);
    if (!current) return;
    current.subscribers.delete(task);
    if (current.subscribers.size > 0) {
      // Recompute from what is left rather than remembering the leaver's value.
      const next = Math.min(
        ...[...current.subscribers].map((item) => item.intervalMs),
      );
      if (next !== current.intervalMs && current.timer !== null) {
        clearTimeout(current.timer);
        current.timer = null;
      }
      current.intervalMs = next;
      schedule(current);
      return;
    }
    current.stopped = true;
    if (current.timer !== null) clearTimeout(current.timer);
    current.timer = null;
    entries.delete(task.key);
  };
}

/** Run every subscriber of one surface once, in registration order. */
export async function pollNow(key: PollKey): Promise<void> {
  const entry = entries.get(key);
  if (!entry) return;
  await runSurface(entry);
}

function schedule(entry: Entry): void {
  if (entry.stopped || entry.timer !== null) return;
  entry.timer = setTimeout(() => {
    entry.timer = null;
    if (entry.stopped) return;
    void tick(entry);
  }, entry.intervalMs);
}

async function tick(entry: Entry): Promise<void> {
  // One tick per surface even if several subscribers exist: they are reading
  // the same data, so running them together is the point of sharing a key.
  const paused = [...entry.subscribers].every(
    (task) => (task.pauseWhenHidden ?? true) && document.hidden,
  );
  if (!paused) await runSurface(entry);
  schedule(entry);
}

async function runSurface(entry: Entry): Promise<void> {
  const tasks = [...entry.subscribers].filter(
    (task) => !((task.pauseWhenHidden ?? true) && document.hidden),
  );
  // Shared read first: several callers reading one endpoint must produce one
  // request per tick, not one per caller. `fetch` is de-duplicated by a single
  // in-flight promise per surface.
  const fetchers = tasks.filter((task) => task.fetch);
  if (fetchers.length > 0 && !sharedInFlight.has(entry.key)) {
    const promise = (async () => {
      try {
        const value = await fetchers[0].fetch?.();
        for (const task of fetchers) {
          try {
            task.onValue?.(value);
          } catch {
            // a subscriber's render error is its own to surface
          }
        }
      } catch {
        // The read failed; each subscriber keeps its last verified state.
      } finally {
        sharedInFlight.delete(entry.key);
      }
    })();
    sharedInFlight.add(entry.key);
    await promise;
  }
  for (const task of tasks) {
    if (!task.run) continue;
    // Each subscriber has its own in-flight guard, so a slow reader only skips
    // its own next turn rather than the whole surface.
    if (inFlight.has(task)) continue;
    inFlight.add(task);
    try {
      await task.run();
    } catch {
      // ignore: the subscriber renders its own error state
    } finally {
      inFlight.delete(task);
    }
  }
}

/** Test seam: number of live timers, so "one timer per surface" is assertable. */
export function scheduledPollCount(): number {
  return [...entries.values()].filter((entry) => entry.timer !== null).length;
}

/** Test seam: drop every entry and timer. */
export function resetPollScheduler(): void {
  for (const entry of entries.values()) {
    if (entry.timer !== null) clearTimeout(entry.timer);
    entry.timer = null;
    entry.stopped = true;
    entry.subscribers.clear();
  }
  entries.clear();
}
