// Log export is offered from two places (the health view and the settings
// help card). Both must report the same outcome wording, so the desktop
// bridge call and the message live here instead of being copied.
export type DiagnosticsExportResult = {
  ok: boolean;
  message: string;
  filename?: string | null;
};

export async function exportDiagnosticsFile(): Promise<DiagnosticsExportResult> {
  if (!window.oneMemory?.exportDiagnostics) {
    return { ok: false, message: "请在桌面 App 中导出诊断信息。" };
  }
  try {
    const receipt = await window.oneMemory.exportDiagnostics();
    return {
      ok: true,
      message: `日志已导出：${receipt.filename ?? "诊断文件"}`,
      filename: receipt.filename,
    };
  } catch {
    return { ok: false, message: "诊断信息未能导出，请重试。" };
  }
}
