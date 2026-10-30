import {
  CAPABILITY_ALIASES,
  COMMANDS,
  type CommandDescriptor,
  type CommandOption,
} from "./command-catalog.js";
import { EXIT_CODES, EXIT_USAGE } from "./exit-codes.js";
import { CLI_VERSION, PRODUCT } from "./version.js";

const GLOBAL_OPTIONS: readonly CommandOption[] = [
  {
    flag: "--help, -h",
    summary: "显示帮助；可与 --json 组合输出机器可读目录。",
  },
  { flag: "--version, -V", summary: "显示 CLI 版本。" },
  {
    flag: "--json",
    summary:
      "以 JSON 输出到 stdout，供脚本与 Agent 消费；结果与失败都在 stdout，只有参数错误写 stderr。",
  },
  {
    flag: "--data-dir <path>",
    summary:
      "本次使用的 MemHub 数据目录（绝对路径）；默认 ONE_MEMORY_DATA_DIR > 安装回执 > ~/.one-memory。",
  },
];

export function renderGeneralHelp(): string {
  return [
    `MemHub CLI ${CLI_VERSION}`,
    "",
    `Usage: ${PRODUCT} <command> [options]`,
    "",
    "Commands:",
    ...COMMANDS.map((command) => `  ${pad(command.name)}${command.summary}`),
    "",
    "Global options:",
    ...GLOBAL_OPTIONS.map((option) => `  ${pad(option.flag)}${option.summary}`),
    "",
    "Exit codes:",
    ...EXIT_CODES.map(
      (exit) => `  ${exit.code}  ${pad(exit.name, 17)}${exit.summary}`,
    ),
    "",
    "确认约定：只有不可逆操作（memories delete、models remove、delete-data、backup restore、graph-width --decision rebuild_with_new）需要 --yes；不加时只说明将要做什么，不做任何改动，exit 0。delete-data 与 backup restore 还要用 --confirm <path> 再写一遍目标路径。install/upgrade/uninstall/service-install/service-uninstall 不加 --yes 时只出计划。其它写命令直接执行。",
    "异步操作返回 ID，用对应命令查进度：sources receipt、models receipt、memories add-status、write status。",
    "目前仅支持 macOS 与 Linux 连接本机 Runtime。",
    "stdout 只输出数据，进度与诊断写入 stderr。",
    `运行 ${PRODUCT} help <command> 查看单个命令的参数。`,
    "",
  ].join("\n");
}

export function renderCommandHelp(command: CommandDescriptor): string {
  const lines = [`Usage: ${command.usage}`, ""];
  if (command.alternative_usage) {
    lines.push(command.alternative_usage, "");
  }
  lines.push(command.summary, "");
  if (command.options.length > 0) {
    lines.push(
      "Options:",
      ...command.options.map(
        (option) => `  ${pad(option.flag, 34)}${option.summary}`,
      ),
      "",
    );
  }
  lines.push(
    "Global options: --json、--data-dir <path>（见 memhub help）。",
    "",
    "Exit codes:",
    ...EXIT_CODES.filter((exit) => command.exit_codes.includes(exit.code)).map(
      (exit) => `  ${exit.code}  ${pad(exit.name, 17)}${exit.summary}`,
    ),
    "",
  );
  return lines.join("\n");
}

export function renderCatalogDocument(command?: CommandDescriptor): string {
  const identity = {
    schema_version: 1,
    product: PRODUCT,
    cli_version: CLI_VERSION,
  };
  return `${JSON.stringify(
    command
      ? { ...identity, command }
      : {
          ...identity,
          commands: COMMANDS,
          capability_aliases: CAPABILITY_ALIASES,
          exit_codes: EXIT_CODES,
        },
  )}\n`;
}

export function renderVersion(): string {
  return `${PRODUCT} ${CLI_VERSION}\n`;
}

export function renderVersionDocument(): string {
  return `${JSON.stringify({
    schema_version: 1,
    product: PRODUCT,
    cli_version: CLI_VERSION,
  })}\n`;
}

export function renderUsageError(
  command: CommandDescriptor | undefined,
  message: string,
): string {
  const nextStep = usageNextStep(command);
  const lines = [`${PRODUCT}: ${message}`];
  if (command) lines.push(`Usage: ${command.usage}`);
  lines.push(`下一步：${nextStep}`, "");
  return lines.join("\n");
}

export function renderUsageErrorDocument(
  command: CommandDescriptor | undefined,
  message: string,
): string {
  return `${JSON.stringify({
    schema_version: 1,
    product: PRODUCT,
    status: "usage_error",
    command: command?.name ?? null,
    usage: command?.usage ?? null,
    exit_code: EXIT_USAGE,
    message,
    next_step: usageNextStep(command),
  })}\n`;
}

function usageNextStep(command: CommandDescriptor | undefined): string {
  return command
    ? `运行 ${PRODUCT} help ${command.name} 查看该命令的参数。`
    : `运行 ${PRODUCT} help 查看可用命令。`;
}

function pad(value: string, width = 21): string {
  return value.length >= width ? `${value} ` : value.padEnd(width);
}
