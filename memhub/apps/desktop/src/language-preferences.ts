import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import {
  isLanguagePreference,
  type LanguagePreference,
  type LanguageSnapshot,
  resolveGuiLocale,
} from "@one-memory/i18n";

interface Storage {
  read(file: string): Promise<string>;
  save(file: string, content: string): Promise<void>;
}
const storage: Storage = {
  read: (file) => readFile(file, "utf8"),
  async save(file, content) {
    await mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
    const temporary = `${file}.${randomUUID()}.tmp`;
    try {
      await writeFile(temporary, content, {
        encoding: "utf8",
        mode: 0o600,
        flag: "wx",
      });
      await rename(temporary, file);
    } finally {
      await rm(temporary, { force: true }).catch(() => {});
    }
  },
};

/** GUI-only preferences, scoped to the active Electron userData profile. */
export class LanguagePreferences {
  private queue: Promise<unknown> = Promise.resolve();
  private readonly listeners = new Set<(snapshot: LanguageSnapshot) => void>();
  private snapshot: LanguageSnapshot;

  private constructor(
    private readonly file: string,
    private readonly systemLanguage: string | undefined,
    private readonly io: Storage,
    preference: LanguagePreference,
  ) {
    this.snapshot = {
      preference,
      locale: resolveGuiLocale(preference, systemLanguage),
      revision: 0,
    };
  }

  static async open(
    userData: string,
    systemLanguage?: string,
    io: Storage = storage,
  ): Promise<LanguagePreferences> {
    const file = path.join(userData, "gui-language.json");
    let preference: LanguagePreference = "system";
    try {
      const data: unknown = JSON.parse(await io.read(file));
      if (
        data &&
        typeof data === "object" &&
        "preference" in data &&
        isLanguagePreference(data.preference)
      ) {
        preference = data.preference;
      }
    } catch {
      // Missing, unreadable or corrupt preferences never block startup or get rewritten.
    }
    return new LanguagePreferences(file, systemLanguage, io, preference);
  }

  read(): LanguageSnapshot {
    return { ...this.snapshot };
  }

  set(preference: LanguagePreference): Promise<LanguageSnapshot> {
    if (!isLanguagePreference(preference))
      return Promise.reject(Error("invalid_language_preference"));
    const operation = this.queue.then(async () => {
      if (preference === this.snapshot.preference) return this.read();
      try {
        await this.io.save(this.file, `${JSON.stringify({ preference })}\n`);
      } catch {
        throw Error("language_preference_save_failed");
      }
      this.snapshot = {
        preference,
        locale: resolveGuiLocale(preference, this.systemLanguage),
        revision: this.snapshot.revision + 1,
      };
      for (const listener of this.listeners) {
        try {
          listener(this.read());
        } catch {
          /* A closed consumer cannot undo a committed save. */
        }
      }
      return this.read();
    });
    this.queue = operation.catch(() => {});
    return operation;
  }

  subscribe(listener: (snapshot: LanguageSnapshot) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  async drain(): Promise<void> {
    await this.queue;
  }
}

/** Call only after Electron readiness: later preferred languages are not fallbacks. */
export function preferredSystemLanguage(app: {
  getPreferredSystemLanguages(): string[];
  getLocale(): string;
}): string | undefined {
  try {
    const primary = app.getPreferredSystemLanguages()[0];
    if (primary) return primary;
  } catch {
    /* Older/unavailable platform API: use Electron's locale. */
  }
  try {
    return app.getLocale() || undefined;
  } catch {
    return undefined;
  }
}
