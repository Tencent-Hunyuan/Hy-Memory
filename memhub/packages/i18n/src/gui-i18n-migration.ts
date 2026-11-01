/**
 * The migration failure dialog and its GitHub issue prefill (Desktop).
 * Product names, codes, versions and paths are placeholders, never copy.
 */
export const migrationChineseMessages = {
  "migration.dialogMessage": "MemHub 升级时数据迁移没有完成。",
  "migration.dialogDetail":
    "为保护你的数据，MemHub 不会启动。请把迁移日志附在 GitHub 反馈里发给我们。（错误码：{code}）",
  "migration.dataRestored": "记忆数据已恢复到升级前的状态。",
  "migration.logPath": "日志：{path}",
  "migration.openLog": "打开日志",
  "migration.quit": "退出",
  "migration.issueHeading": "## 升级迁移失败",
  "migration.issueUpgrade":
    "- 从 MemHub {fromVersion}（build {fromBuild}）升级到 {toVersion}（build {toBuild}），迁移数据失败。",
  "migration.issueCode": "- 错误码：{code}",
  "migration.issueStep": "- 失败步骤：{step}",
  "migration.issueRestored": "- 数据已恢复到升级前状态：{restored}",
  "migration.issueSystem": "- 系统：{system}",
  "migration.issueArch": "（{arch}）",
  "migration.issueLog": "- 迁移日志（本地）：{path}",
  "migration.issueAttach": "请把上面这个日志文件拖到这里作为附件，再提交。",
  "migration.unknown": "未知",
  "migration.yes": "是",
  "migration.no": "否",
} as const;

export const migrationSpanishMessages: Record<
  keyof typeof migrationChineseMessages,
  string
> = {
  "migration.dialogMessage":
    "La migración de datos de esta actualización de MemHub no se completó.",
  "migration.dialogDetail":
    "Para proteger tus datos, MemHub no se iniciará. Adjunta el registro de migración a una incidencia de GitHub y envíanoslo. (Código de error: {code})",
  "migration.dataRestored":
    "Tus datos se restauraron al estado anterior a la actualización.",
  "migration.logPath": "Registro: {path}",
  "migration.openLog": "Abrir registro",
  "migration.quit": "Salir",
  "migration.issueHeading": "## Falló la migración de la actualización",
  "migration.issueUpgrade":
    "- Al actualizar MemHub de {fromVersion} (build {fromBuild}) a {toVersion} (build {toBuild}), falló la migración de datos.",
  "migration.issueCode": "- Código de error: {code}",
  "migration.issueStep": "- Paso fallido: {step}",
  "migration.issueRestored":
    "- Datos restaurados al estado anterior a la actualización: {restored}",
  "migration.issueSystem": "- Sistema: {system}",
  "migration.issueArch": " ({arch})",
  "migration.issueLog": "- Registro de migración (local): {path}",
  "migration.issueAttach":
    "Arrastra aquí el archivo de registro indicado arriba como adjunto y luego envía la incidencia.",
  "migration.unknown": "desconocido",
  "migration.yes": "sí",
  "migration.no": "no",
};
