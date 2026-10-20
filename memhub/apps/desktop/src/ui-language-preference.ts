import { randomUUID } from "node:crypto";
import {
  lstatSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { isUiLanguage, type UiLanguage } from "@one-memory/contracts";

export class UiLanguagePreference {
  private readonly filename: string;
  private current: UiLanguage = "zh-CN";

  constructor(userData: string) {
    this.filename = path.join(userData, "ui-language.json");
    try {
      const info = lstatSync(this.filename);
      if (!info.isFile() || info.size > 128) return;
      const saved: unknown = JSON.parse(readFileSync(this.filename, "utf8"));
      if (isUiLanguage(saved)) this.current = saved;
    } catch {
      // Missing or invalid preference does not block startup.
    }
  }

  get(): UiLanguage {
    return this.current;
  }

  set(value: unknown): UiLanguage {
    if (!isUiLanguage(value)) throw new Error("invalid_ui_language");
    if (value === this.current) return value;
    const temporary = `${this.filename}.${randomUUID()}.tmp`;
    writeFileSync(temporary, JSON.stringify(value), {
      encoding: "utf8",
      mode: 0o600,
      flag: "wx",
    });
    try {
      renameSync(temporary, this.filename);
      this.current = value;
    } finally {
      rmSync(temporary, { force: true });
    }
    return value;
  }
}
