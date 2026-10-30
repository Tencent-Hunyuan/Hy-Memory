import { MEMORY_STATUSES } from "./core-responses.js";
import {
  EXIT_FAILED,
  EXIT_OK,
  EXIT_PLAN_BLOCKED,
  EXIT_RUNTIME_STOPPED,
  EXIT_USAGE,
} from "./exit-codes.js";

export type CommandOption = {
  readonly flag: string;
  readonly summary: string;
};

export type CommandDescriptor = {
  readonly name: string;
  readonly summary: string;
  readonly usage: string;
  readonly alternative_usage?: string;
  readonly capability_aliases?: readonly string[];
  readonly options: readonly CommandOption[];
  readonly exit_codes: readonly number[];
};

/**
 * Product-level names that are intentionally represented by an existing
 * command rather than by a second command with duplicate Runtime semantics.
 * Keep this machine-readable so package acceptance can compare the declared
 * CLI surface to the product lifecycle requirements.
 */
export const CAPABILITY_ALIASES: Readonly<Record<string, readonly string[]>> = {
  daemon: ["start", "stop", "restart", "status"],
  "startup-service": ["service-plan", "service-install", "service-uninstall"],
};

const PLATFORM_OPTION: CommandOption = {
  flag: "--platform <darwin|linux|win32>",
  summary: "目标平台，默认当前平台。",
};
const HOME_OPTION: CommandOption = {
  flag: "--home <path>",
  summary: "用户主目录，默认当前用户主目录。",
};
const DATA_DIR_OPTION: CommandOption = {
  flag: "--data-dir <path>",
  summary:
    "MemHub 数据目录绝对路径；默认沿用 ONE_MEMORY_DATA_DIR，不改写已有数据。",
};
const UID_OPTION: CommandOption = {
  flag: "--uid <id>",
  summary: "macOS LaunchAgent 所属用户 ID，默认当前用户。",
};
const XDG_OPTION: CommandOption = {
  flag: "--xdg-config-home <path>",
  summary: "Linux systemd user 配置根目录。",
};
const CONFIRM_OPTION: CommandOption = {
  flag: "--yes",
  summary: "确认执行；不加该参数时只输出计划，不做任何改动。",
};
/** The one confirmation convention, for irreversible operations only. */
const DESTRUCTIVE_CONFIRM_OPTION: CommandOption = {
  flag: "--yes",
  summary:
    "确认这次不可逆操作；不加时只说明将要做什么，不做任何改动，exit 0。（旧的 --confirm 仍作为别名接受。）",
};
const JSON_OPTION: CommandOption = {
  flag: "--json",
  summary: "以 JSON 输出到 stdout，供脚本与 Agent 消费。",
};
const WATCH_OPTION: CommandOption = {
  flag: "--watch",
  summary: "串行持续刷新；Ctrl-C 只停止观察，不停止 Runtime。",
};
const INTERVAL_OPTION: CommandOption = {
  flag: "--interval <seconds>",
  summary: "刷新间隔，默认 5 秒，范围 1..300；必须与 --watch 一起使用。",
};
const SERVICE_OPTIONS: readonly CommandOption[] = [
  PLATFORM_OPTION,
  HOME_OPTION,
  {
    flag: "--install-root <path>",
    summary: "受管安装根目录（必填）。",
  },
  DATA_DIR_OPTION,
  UID_OPTION,
  XDG_OPTION,
];

const SERVICE_USAGE =
  "--install-root <path> [--platform <id>] [--home <path>] [--data-dir <path>] [--uid <id>] [--xdg-config-home <path>]";

const INSTALL_OPTIONS: readonly CommandOption[] = [
  {
    flag: "--source <path>",
    summary:
      "离线发行目录；提供时完全不访问网络。完整 Runtime 内的 CLI 默认推导同目录，npm CLI 默认下载同版本 Release。",
  },
  {
    flag: "--release-manifest-url <https-url>",
    summary:
      "远程 release-manifest.json；仅在未提供 --source 时使用。发行构建可注入同版本默认模板，源码构建不假定私有下载源。",
  },
  {
    flag: "--channel <stable|prerelease>",
    summary:
      "远程发布通道；SemVer prerelease 默认 prerelease，其余默认 stable。不会静默跨通道。",
  },
  {
    flag: "--install-root <path>",
    summary: "受管安装根目录，默认按平台推导。",
  },
  HOME_OPTION,
  {
    flag: "--bin-dir <path>",
    summary: "受管 CLI 链接所在目录，POSIX 默认 ~/.local/bin。",
  },
  DATA_DIR_OPTION,
  UID_OPTION,
  XDG_OPTION,
  {
    flag: "--no-shell-path",
    summary:
      "不改登录 shell profile（隔离/脚本安装用）；短指令需自行把 --bin-dir 加入 PATH。",
  },
  {
    flag: "--shell-profile <path>",
    summary:
      "把 PATH 区块写到指定文件而不是默认 ~/.zprofile / ~/.profile，便于隔离安装。",
  },
  {
    flag: "--preserve-data-dir",
    summary:
      "uninstall 用：当 --data-dir 位于 <install-root>/versions/ 内时，显式声明本次不把该目录视为受管子目录；只改计划保护范围，永不删除数据。",
  },
  {
    flag: "--allow-downgrade",
    summary:
      "install/upgrade 用：显式允许安装低于当前 build_number 的发行包；默认拒绝。不会由环境变量或配置默认开启。",
  },
  CONFIRM_OPTION,
];

const INSTALL_USAGE =
  "[--source <path> | --release-manifest-url <https-url>] [--channel <stable|prerelease>] [--install-root <path>] [--home <path>] [--bin-dir <path>] [--data-dir <path>] [--uid <id>] [--xdg-config-home <path>] [--no-shell-path] [--shell-profile <path>] [--preserve-data-dir] [--allow-downgrade] [--yes]";

const USAGE_EXITS = [EXIT_OK, EXIT_FAILED, EXIT_USAGE] as const;
const RUNTIME_EXITS = [
  EXIT_OK,
  EXIT_FAILED,
  EXIT_USAGE,
  EXIT_RUNTIME_STOPPED,
] as const;

const KIND_OPTION: CommandOption = {
  flag: "--kind <chat|experience|fact>",
  summary:
    "记忆所在的命名空间；工作/编码场景的经验和事实需要同时给 --variant。list 输出里每条都带 kind/variant。",
};
const VARIANT_OPTION: CommandOption = {
  flag: "--variant <working|coding>",
  summary:
    "经验或事实属于工作还是编码场景；必须和 --kind experience|fact 一起用。",
};

export const COMMANDS: readonly CommandDescriptor[] = [
  {
    name: "serve",
    summary: "在前台启动完整 Runtime（Core 与 Host Gateway），不注册系统服务。",
    usage: "memhub serve",
    options: [],
    exit_codes: USAGE_EXITS,
  },
  {
    name: "start",
    summary:
      "启动本机 Runtime；已有通过身份校验的实例时只连接，不重复启动。受管服务会同时恢复登录时自动启动。",
    usage: "memhub start [--json]",
    options: [JSON_OPTION],
    exit_codes: USAGE_EXITS,
  },
  {
    name: "stop",
    summary:
      "停止通过身份校验的本机 Runtime；身份不明确时不发送信号。受管服务（launchd/systemd、App）停止后登录时也不会自动启动，直到再次运行 memhub start。",
    usage: "memhub stop [--json]",
    options: [JSON_OPTION],
    exit_codes: USAGE_EXITS,
  },
  {
    name: "restart",
    summary: "先停止再启动本机 Runtime，两步串行执行。",
    usage: "memhub restart [--json]",
    options: [JSON_OPTION],
    exit_codes: USAGE_EXITS,
  },
  {
    name: "status",
    summary: "只读查询 Runtime 运行状态；不启动服务、不迁移数据、不改配置。",
    usage: "memhub status [--watch] [--interval <seconds>] [--json]",
    options: [WATCH_OPTION, INTERVAL_OPTION, JSON_OPTION],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "doctor",
    summary:
      "按诊断口径做只读检查并给出修复建议；默认不修复任何东西。Runtime 未运行时 exit 3。",
    usage: "memhub doctor [--json]",
    options: [JSON_OPTION],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "logs",
    summary:
      "读取本机脱敏诊断日志；logs export 会把排查包（含系统崩溃报告）直接存到下载目录。默认显示最近一次会话的 200 条事件。",
    usage:
      "memhub logs export [--json]\n            memhub logs [--since <15m|2h|7d|RFC3339>] [--session <id>] [--trace <id>] [--incident <id>] [--operation <id>] [--request <id>] [--job <id>] [--task <id>] [--component <name>] [--event <name>] [--errors-only] [--tail <1..5000>] [--follow] [--json]",
    options: [
      {
        flag: "--since <time>",
        summary: "从相对时长或 RFC3339 时间开始读取。",
      },
      { flag: "--session <id>", summary: "只看一个运行会话。" },
      { flag: "--trace <id>", summary: "只看一次跨组件操作。" },
      { flag: "--incident <id>", summary: "查看问题时间点前后同实例的事件。" },
      { flag: "--operation <id>", summary: "只看一个持久操作。" },
      { flag: "--request <id>", summary: "只看一个模型或写入请求。" },
      { flag: "--job <id>", summary: "只看一个导入任务。" },
      { flag: "--task <id>", summary: "只看一个记忆处理任务。" },
      { flag: "--component <name>", summary: "按组件过滤。" },
      { flag: "--event <name>", summary: "按事件名过滤。" },
      { flag: "--errors-only", summary: "只显示错误事件。" },
      { flag: "--tail <n>", summary: "最多返回多少条，默认 200，上限 5000。" },
      { flag: "--follow", summary: "持续读取新事件；Ctrl-C 仅停止观察。" },
      {
        flag: "--json",
        summary:
          "以 NDJSON 输出到 stdout（每行一个脱敏事件；非单 JSON 文档），供脚本与 Agent 消费。",
      },
    ],
    exit_codes: USAGE_EXITS,
  },
  {
    name: "init",
    summary:
      "首次接入引导。在终端里（未加 --json）进入交互向导：选择来源 → 是否开启处理 → 配置模型。非终端或加 --json 时不读 stdin，只列出可接入的来源和下一步命令。脚本接入请用 sources connect。",
    usage: "memhub init [--json]",
    options: [JSON_OPTION],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "sources",
    summary:
      "列出来源，直接接入来源，或按 request id 查接入回执。connect 不加 --import 只授权（处理保持冻结：不导入历史、不扫描新会话）；加 --import 同时导入历史并开启持续同步，返回 request id。目录来自环境变量覆盖的来源必须加 --confirm-overrides。",
    usage:
      "memhub sources list [--json]\n            memhub sources connect <id> [<id> ...] [--import] [--confirm-overrides] [--request-id <uuid>] [--json]\n            memhub sources connect --all-detected [--import] [--confirm-overrides] [--json]\n            memhub sources receipt <request-id> [--json]",
    options: [
      {
        flag: "--import",
        summary:
          "接入后立即导入历史会话并开启持续同步；不加时只授权，处理保持冻结。",
      },
      {
        flag: "--all-detected",
        summary:
          "接入全部已检测到（location_state=found）的来源；与具体来源互斥。",
      },
      {
        flag: "--confirm-overrides",
        summary:
          "确认接受目录来自环境变量覆盖的来源（如 DSH_HOME 指定的目录）；不加时这些来源被拒绝（指定来源时）或跳过并列出（--all-detected 时）。",
      },
      {
        flag: "--request-id <uuid>",
        summary:
          "--import 的幂等键；用同一个 id 重试只会读回第一次的回执，不会重复导入。",
      },
      JSON_OPTION,
    ],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "sync",
    summary: "查看 Harness 处理状态；直接 Freeze/Thaw 某个来源。",
    usage:
      "memhub sync status [--json]\n            memhub sync <pause|resume> --source <id> [--json]",
    options: [
      {
        flag: "--source <id>",
        summary: "暂停或恢复的目标来源。",
      },
      JSON_OPTION,
    ],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "progress",
    summary: "只读查看导入与生成进度；状态和计数分开呈现，不编造完成百分比。",
    usage:
      "memhub progress [--job <job-id>] [--issues] [--limit <n>] [--watch] [--interval <seconds>] [--json]",
    options: [
      { flag: "--job <job-id>", summary: "只看一个导入任务。" },
      {
        flag: "--issues",
        summary: "同时读取该导入任务的问题；必须与 --job 一起使用。",
      },
      {
        flag: "--limit <n>",
        summary: "每个任务/问题列表最多输出多少条，默认 50，上限 100。",
      },
      WATCH_OPTION,
      INTERVAL_OPTION,
      JSON_OPTION,
    ],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "jobs",
    summary:
      "查看或控制导入任务：start 创建 history/sync 任务；pause/resume/cancel 控制已有任务。都直接执行。",
    usage:
      "memhub jobs list [--json]\n" +
      "            memhub jobs show <job-id> [--json]\n" +
      "            memhub jobs start --source <id> [--operation <history|sync>] [--json]\n" +
      "            memhub jobs <pause|resume|cancel> --job <id> [--json]",
    options: [
      {
        flag: "--source <id>",
        summary: "start 要导入的来源；必须是已连接的来源。",
      },
      {
        flag: "--operation <history|sync>",
        summary:
          "start 的导入类型，默认 history；sync 与 scan --source 相同，立即增量同步一次。",
      },
      {
        flag: "--job <id>",
        summary: "pause/resume/cancel 的目标导入任务。",
      },
      JSON_OPTION,
    ],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "models",
    summary:
      "管理生成模型配置。add/test 直接提交并返回操作 ID（异步，用 receipt 查结果）；edit/activate 直接执行；remove 不可逆，需 --yes。<profile-id>/<operation-id> 都是 UUID；脚本模式的凭据只从受控文件读取。",
    usage:
      "memhub models list [--json]\n            memhub models show <profile-id> [--json]\n            memhub models add --config-file <path> [--api-key-file <path>] [--activate] [--json]\n            memhub models test <profile-id> [--json]\n            memhub models receipt <operation-id> [--json]\n            memhub models edit <profile-id> --config-file <path> [--api-key-file <path>] [--json]\n            memhub models activate <profile-id> [--json]\n            memhub models remove <profile-id> [--yes] [--json]",
    alternative_usage: "memhub models setup",
    options: [
      {
        flag: "--config-file <path>",
        summary:
          "有界 JSON 模型配置，不能含 api_key；edit 的补丁必须带 expected_revision。",
      },
      {
        flag: "--api-key-file <path>",
        summary: "从属主专用的普通文件读取凭据；内容不进入参数、输出或错误。",
      },
      {
        flag: "--activate",
        summary: "add 成功后把新配置设为当前使用。",
      },
      DESTRUCTIVE_CONFIRM_OPTION,
      JSON_OPTION,
    ],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "memories",
    summary:
      "列出、搜索、查看、添加、修改或物理删除记忆（对话、工作、编码三个场景）。add 和 App 的「添加记忆」是同一个入口；delete 不可逆，需 --yes。",
    usage:
      "memhub memories list [--scene <all|chat|working|coding>] [--source <id>] [--query <text>] [--status <state>] [--offset <n>] [--limit <n>] [--json]\n            memhub memories show <memory-id> [--kind <kind>] [--variant <variant>] [--json]\n            memhub memories add <text> [--request-id <uuid>] [--json]\n            memhub memories add --content-file <path> [--request-id <uuid>] [--json]\n            memhub memories add-status <request-id> [--json]\n            memhub memories update <memory-id> --content-file <path> [--kind <kind>] [--variant <variant>] [--task <text>] [--json]\n            memhub memories delete <memory-id> [--kind <kind>] [--variant <variant>] [--yes] [--json]",
    options: [
      {
        flag: "--scene <all|chat|working|coding>",
        summary: "只列一个场景，默认 all。working/coding 列的是该场景的经验。",
      },
      { flag: "--source <id>", summary: "只列某个来源写入的记忆。" },
      {
        flag: "--query <text>",
        summary:
          "关键词搜索（只搜 active 记忆，在最相关的一批里匹配所有关键词，输出 search_window 时表示可能少计）。",
      },
      {
        flag: "--status <state>",
        summary: `记忆状态，取值 ${MEMORY_STATUSES.join("、")}，默认 active。`,
      },
      { flag: "--offset <n>", summary: "跳过前 n 条，默认 0。" },
      {
        flag: "--limit <n>",
        summary: "本页最多返回多少条，默认 100，上限 500。",
      },
      KIND_OPTION,
      VARIANT_OPTION,
      {
        flag: "--content-file <path>",
        summary:
          "从有界普通文件读取正文：add 的新记忆（≤12000 字），或 update 的新内容（经验为 solution）。",
      },
      {
        flag: "--task <text>",
        summary: "update 经验（--kind experience）时同时改写 task。",
      },
      {
        flag: "--request-id <uuid>",
        summary:
          "add 的幂等键；用同一个 id 重试只会读回第一次的结果，不会重复添加。不给时自动生成并输出。",
      },
      DESTRUCTIVE_CONFIRM_OPTION,
      JSON_OPTION,
    ],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "profile",
    summary: "读取 SDK 组装后的用户长期画像（L0 + 已启用的长期画像补充）。",
    usage: "memhub profile [--json]",
    options: [JSON_OPTION],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "graph-width",
    summary:
      "嵌入维度变化、图谱宽度不匹配时的处置决定。keep_existing 与 export_first 直接执行；rebuild_with_new 会归档旧图谱并重建，需 --yes。",
    usage:
      "memhub graph-width --decision <keep_existing|export_first|rebuild_with_new> [--yes] [--json]",
    options: [
      {
        flag: "--decision <decision>",
        summary:
          "keep_existing（保持现有图谱）、export_first（先导出）、rebuild_with_new（导出后归档旧图谱并按新维度重建）。",
      },
      {
        ...DESTRUCTIVE_CONFIRM_OPTION,
        summary: "rebuild_with_new 必须加；其它决定不需要。",
      },
      JSON_OPTION,
    ],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "host",
    summary:
      "宿主接入与被调用：列出本机宿主、查看接入状态、预览并安装/卸载召回注入。安装与卸载分两步：先预览出 fingerprint，再带 --approve 提交。",
    usage:
      "memhub host list [--json]\n            memhub host status --host <id> [--json]\n            memhub host install --host <id> [--json]\n            memhub host install --host <id> --approve <fingerprint> [--json]\n            memhub host uninstall --host <id> [--json]\n            memhub host uninstall --host <id> --approve <fingerprint> [--json]\n            memhub host sync skill --host <id> [--approve <fingerprint>] [--overwrite-modified] [--json]",
    alternative_usage:
      "memhub host install <id> [--approve <fingerprint>] [--json]\n            memhub host status <id> [--json]\n            memhub host uninstall <id> [--approve <fingerprint>] [--json]",
    options: [
      {
        flag: "--host <id>",
        summary:
          "目标宿主，如 cursor、claude-code、codex；也可直接写在动词后（memhub host install codex）。完整列表见 host list。",
      },
      {
        flag: "--approve <fingerprint>",
        summary:
          "提交预览给出的 fingerprint；缺少它 install/uninstall 只预览，不改动任何宿主文件。",
      },
      {
        flag: "--overwrite-modified",
        summary:
          "host sync skill 提交时允许覆盖本地被改过的 skill；默认不覆盖。",
      },
      JSON_OPTION,
    ],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "recall",
    summary:
      "从原始对话与已沉淀记忆统一检索上下文；本地只读，不调用模型、不触发补索引。文本输出标明每条是「原始对话摘录」还是「记忆」（JSON 里是 kind: raw|memory）。",
    usage:
      "memhub recall <query> [--kind <raw|memory>] [--source <id>] [--limit <n>] [--character-budget <n>] [--timeout-ms <n>] [--json]\n            memhub recall --query-file <path> [...]\n            memhub recall --stdin [...]",
    options: [
      {
        flag: "--query-file <path>",
        summary: "从有界普通文件读取查询，不进入 shell 历史。",
      },
      {
        flag: "--stdin",
        summary: "显式从管道读取一条有界查询；交互式 TTY 会拒绝，不等待输入。",
      },
      {
        flag: "--kind <raw|memory>",
        summary: "查询路径，可重复；默认同时查询 raw 与 memory。",
      },
      {
        flag: "--source <id>",
        summary: "只收窄 raw 原文来源，可重复；未请求 raw 时拒绝。",
      },
      { flag: "--limit <n>", summary: "最多返回 1..20 项，默认 8。" },
      {
        flag: "--character-budget <n>",
        summary: "总正文预算 200..20000 字符，默认 4000。",
      },
      {
        flag: "--timeout-ms <n>",
        summary: "Core 查询时限 50..30000 毫秒，默认 2000。",
      },
      JSON_OPTION,
    ],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "service-plan",
    summary: "生成用户级服务计划；只输出计划，不写文件也不执行命令。",
    usage: `memhub service-plan ${SERVICE_USAGE}`,
    options: SERVICE_OPTIONS,
    exit_codes: [EXIT_OK, EXIT_USAGE, EXIT_PLAN_BLOCKED],
  },
  {
    name: "service-install",
    summary: "预览用户级服务安装；只有 --yes 才写入服务文件并注册。",
    usage: `memhub service-install ${SERVICE_USAGE} [--yes]`,
    options: [...SERVICE_OPTIONS, CONFIRM_OPTION],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PLAN_BLOCKED],
  },
  {
    name: "service-uninstall",
    summary:
      "预览用户级服务卸载；只有 --yes 才注销并删除逐字匹配的受管服务文件，不删数据。",
    usage: `memhub service-uninstall ${SERVICE_USAGE} [--yes]`,
    options: [...SERVICE_OPTIONS, CONFIRM_OPTION],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PLAN_BLOCKED],
  },
  {
    name: "install",
    summary:
      "预览发行目录安装、memhub/memhub-cli/one-memory 短链接、PATH 条目与用户服务计划；只有 --yes 才执行。记忆数据迁移开始前失败会回滚；迁移开始后失败保留新版本、拒绝启动，并输出迁移日志与反馈地址。",
    usage: `memhub install ${INSTALL_USAGE}`,
    options: INSTALL_OPTIONS,
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PLAN_BLOCKED],
  },
  {
    name: "uninstall",
    summary:
      "卸载产品本身：停止服务、移除受管 CLI 链接与已安装版本文件；**不删数据**（dataDir 原样保留）。只有 --yes 才执行，默认只出计划。",
    usage: `memhub uninstall ${INSTALL_USAGE}`,
    options: INSTALL_OPTIONS,
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PLAN_BLOCKED],
  },
  {
    name: "upgrade",
    summary:
      "预览升级；只有 --yes 才切换受管版本并重启服务，不删数据。记忆数据迁移开始前失败会恢复旧版本；迁移开始后失败保留新版本、拒绝启动，并输出迁移日志与反馈地址（不会回滚到旧版本）。",
    usage: `memhub upgrade ${INSTALL_USAGE}`,
    options: INSTALL_OPTIONS,
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PLAN_BLOCKED],
  },
  {
    name: "delete-data",
    summary:
      "删除 MemHub 数据目录（记忆、来源、配置），不可逆。这是唯一会删除数据的命令；uninstall 不会。需要 Runtime 已停止；不加参数时只预览要删除的目录；删除需要 --yes 并用 --confirm 再写一遍同一个目录。",
    usage:
      "memhub delete-data [--data-dir <path>] [--yes --confirm <path>] [--json]",
    options: [
      {
        flag: "--data-dir <path>",
        summary: "要删除的数据目录绝对路径；默认当前使用的数据目录。",
      },
      {
        ...DESTRUCTIVE_CONFIRM_OPTION,
        summary:
          "删除时必须加，且要同时给出 --confirm <path>；不加时只预览，exit 0。",
      },
      {
        flag: "--confirm <path>",
        summary:
          "删除时必须加：再写一遍要删除的数据目录，与预览中的目录完全一致才执行。",
      },
      JSON_OPTION,
    ],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PLAN_BLOCKED],
  },
  {
    name: "onboarding",
    summary:
      "onboarding reset 回到首次运行的引导流程。只清除引导完成状态：不删记忆数据，也不需要卸载重装。",
    usage: "memhub onboarding reset [--json]",
    options: [JSON_OPTION],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "help",
    summary: "显示总帮助或单个命令的用法。",
    usage: "memhub help [command] [--json]",
    options: [JSON_OPTION],
    exit_codes: [EXIT_OK, EXIT_USAGE],
  },
  {
    name: "version",
    summary: "显示 CLI 版本。",
    usage: "memhub version [--json]",
    options: [JSON_OPTION],
    exit_codes: [EXIT_OK, EXIT_USAGE],
  },
  {
    name: "scan-roots",
    summary:
      "查看或登记被扫描的目录（scan roots）。add/remove 直接执行。（旧名 path 仍可用。）",
    usage:
      "memhub scan-roots list [--json]\n" +
      "            memhub scan-roots add --root <path> [--source <id>] [--no-recursive] [--json]\n" +
      "            memhub scan-roots remove --root <path> [--json]",
    options: [
      { flag: "--root <path>", summary: "要登记或移除的目录。" },
      {
        flag: "--source <id>",
        summary: "把目录绑定到某个来源；不给时由 Runtime 自动识别。",
      },
      { flag: "--no-recursive", summary: "只扫描该目录本身，不进入子目录。" },
      JSON_OPTION,
    ],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "adapter",
    summary:
      "查看 Harness Adapter 的就绪状态与最近一次探测结果（list），或现场探测某个来源（probe --source <id>）；两者均为只读。",
    usage:
      "memhub adapter list [--json]\n" +
      "            memhub adapter probe --source <id> [--json]",
    options: [
      { flag: "--source <id>", summary: "probe 的目标来源。" },
      JSON_OPTION,
    ],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "scan",
    summary:
      "立即增量扫描一个已连接并开启同步的来源，返回导入任务（与 jobs start --operation sync 相同）。接入全部来源请用 sources connect --all-detected --import。",
    usage: "memhub scan --source <id> [--json]",
    options: [
      {
        flag: "--source <id>",
        summary: "要扫描的来源。",
      },
      JSON_OPTION,
    ],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "write",
    summary:
      "提交一段 OpenAI 格式的对话（messages）到写入队列，异步加工成记忆；或按 request id 查询状态。request id 是幂等键：不给时自动生成并输出，重试请带上同一个。新增一条结论式记忆请用 memories add。",
    usage:
      "memhub write submit --messages-file <path> --session <id> [--source <id>] [--request-id <id>] [--memory-at <RFC3339>] [--live] [--json]\n            memhub write status <request-id> [--json]",
    options: [
      {
        flag: "--messages-file <path>",
        summary:
          "OpenAI messages 数组（或 {messages:[…]}），1..200 条，role 为 user/assistant/tool，至少一条 user。",
      },
      { flag: "--session <id>", summary: "会话 ID。" },
      { flag: "--source <id>", summary: "来源应用，默认 memhub-cli。" },
      {
        flag: "--request-id <id>",
        summary:
          "幂等键：同 id 同内容不会重复入队，同 id 不同内容被拒绝（409）。",
      },
      { flag: "--memory-at <RFC3339>", summary: "对话发生时间，必须带时区。" },
      { flag: "--live", summary: "优先处理。" },
      JSON_OPTION,
    ],
    exit_codes: RUNTIME_EXITS,
  },
  {
    name: "backup",
    summary:
      "备份与恢复主库（来源、写入队列、配置）。注意：不包含记忆本身——SDK 记忆库 hy-memory/ 不在备份里，restore 也不会替换它。create 的目标要放在 <数据目录>/backups/ 下才能被 restore 使用；restore 覆盖现有主库，需 Runtime 已停止，并加 --yes 与 --confirm <同一个备份路径>。",
    usage:
      "memhub backup create --target <path> [--json]\n" +
      "            memhub backup restore --source <path> [--yes --confirm <path>] [--json]",
    options: [
      {
        flag: "--target <path>",
        summary: "create 的备份文件路径，必须在数据目录内。",
      },
      {
        flag: "--source <path>",
        summary:
          "restore 的备份文件，必须是 <数据目录>/backups/ 下的受管备份。",
      },
      {
        ...DESTRUCTIVE_CONFIRM_OPTION,
        summary:
          "restore 必须加，且要同时给出 --confirm <path>；create 不需要。",
      },
      {
        flag: "--confirm <path>",
        summary:
          "restore 必须加：再写一遍 --source 的备份路径，完全一致才执行。",
      },
      JSON_OPTION,
    ],
    exit_codes: [...RUNTIME_EXITS, EXIT_PLAN_BLOCKED],
  },
  {
    name: "ui",
    summary:
      "用系统默认浏览器打开 Runtime 托管的 Web Console：先向 Core 换取一次性 loopback 登录码，再交给系统浏览器由 Core 置 HttpOnly/SameSite=Strict session；CLI 不向浏览器暴露裸 Core token。Runtime 未运行时 exit 3；Core 未提供该合同时如实返回 ui_session_contract_unavailable（exit 4）。",
    usage: "memhub ui [--json]",
    options: [JSON_OPTION],
    exit_codes: [...RUNTIME_EXITS, EXIT_PLAN_BLOCKED],
  },
];

/** Old names that still run but are not listed: `path` → `scan-roots`,
 * `task` → `write status`, `graph width` → `graph-width`. */
export const HIDDEN_COMMAND_ALIASES: Readonly<Record<string, string>> = {
  path: "scan-roots",
  task: "write",
  graph: "graph-width",
};

export function findCommand(name: string): CommandDescriptor | undefined {
  return COMMANDS.find((command) => command.name === name);
}

/** A listed command, or the command a hidden alias now runs as. */
export function findCommandOrAlias(
  name: string,
): CommandDescriptor | undefined {
  return findCommand(HIDDEN_COMMAND_ALIASES[name] ?? name);
}
