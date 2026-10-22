import { type GuiLocale, translate } from "@one-memory/i18n";

// Log export is offered from two places (the health view and the settings
// help card). Both must report the same outcome wording, so the desktop
// bridge call and the message live here instead of being copied.
export type DiagnosticsExportResult = {
  ok: boolean;
  message: string;
};

export async function exportDiagnosticsFile(
  locale: GuiLocale = "zh-CN",
): Promise<DiagnosticsExportResult> {
  if (!window.oneMemory?.exportDiagnostics) {
    return { ok: false, message: translate(locale, "diagnosticExportDesktop") };
  }
  try {
    const receipt = await window.oneMemory.exportDiagnostics();
    return {
      ok: true,
      message: translate(locale, "diagnosticExported", {
        filename: receipt.filename ?? translate(locale, "diagnosticFile"),
      }),
    };
  } catch {
    return { ok: false, message: translate(locale, "diagnosticExportFailed") };
  }
}
