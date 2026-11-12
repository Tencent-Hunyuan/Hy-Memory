/**
 * The Core's port-in-use refusals (`core_port_in_use*`, exit status 75) and
 * the localized sentences the App shows for them. The Core prints the code
 * only; the port is an interpolation parameter, never copy.
 */
export const coreRefusalChineseMessages = {
  "core.portInUse.byRuntime":
    "端口 {port} 已被本数据目录的 MemHub Core 占用（可能仍在启动中）。等待它就绪，或先停止它（如 memhub stop / 重启系统服务），或更换 ONE_MEMORY_PORT。",
  "core.portInUse.foreign":
    "端口 {port} 被其他进程占用，不是本数据目录的 MemHub Core。找出占用该端口的进程，或更换 ONE_MEMORY_PORT。",
} as const;

export const coreRefusalEnglishMessages: Record<
  keyof typeof coreRefusalChineseMessages,
  string
> = {
  "core.portInUse.byRuntime":
    "Port {port} is held by this data directory's MemHub Core (it may still be starting). Wait for it to be ready, stop it first (memhub stop / restart the system service), or change ONE_MEMORY_PORT.",
  "core.portInUse.foreign":
    "Port {port} is held by another process, not this data directory's MemHub Core. Find the process holding it, or change ONE_MEMORY_PORT.",
};

export const coreRefusalSpanishMessages: Record<
  keyof typeof coreRefusalChineseMessages,
  string
> = {
  "core.portInUse.byRuntime":
    "El puerto {port} lo ocupa el MemHub Core de este directorio de datos (puede que siga iniciándose). Espera a que esté listo, deténlo antes (memhub stop / reinicia el servicio del sistema) o cambia ONE_MEMORY_PORT.",
  "core.portInUse.foreign":
    "El puerto {port} lo ocupa otro proceso, no el MemHub Core de este directorio de datos. Encuentra el proceso que lo ocupa o cambia ONE_MEMORY_PORT.",
};
