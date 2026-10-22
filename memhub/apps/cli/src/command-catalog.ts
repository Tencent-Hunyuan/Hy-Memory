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
const PLAN_FILE_OPTION: CommandOption = {
  flag: "--plan-file <path>",
  summary: "读取先前 --json 保存的有界计划文件。",
};
const EXPLICIT_CONFIRM_OPTION: CommandOption = {
  flag: "--confirm",
  summary: "确认执行计划文件逐字描述的操作；缺少时绝不写入。",
};
// Same flag, but for commands that name their target directly instead of
// going through a saved plan file; the plan-file wording would be wrong here.
const DIRECT_CONFIRM_OPTION: CommandOption = {
  flag: "--confirm",
  summary: "确认执行上面预览的这次操作；缺少时绝不写入。",
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

export const COMMANDS: readonly CommandDescriptor[] = [
  {
    name: "serve",
    summary: "在前台启动完整 Runtime（Core 与 Host Gateway），不注册系统服务。",
    usage: "memhub serve",
    options: [],
    exit_codes: [EXIT_OK, EXIT_FAILED],
  },
  {
    name: "start",
    summary: "启动本机 Runtime；已有通过身份校验的实例时只连接，不重复启动。",
    usage: "memhub start [--json]",
    options: [JSON_OPTION],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "stop",
    summary: "停止通过身份校验的本机 Runtime；身份不明确时不发送信号。",
    usage: "memhub stop [--json]",
    options: [JSON_OPTION],
    exit_codes: [EXIT_OK, EXIT_FAILED],
  },
  {
    name: "restart",
    summary: "先停止再启动本机 Runtime，两步串行执行。",
    usage: "memhub restart [--json]",
    options: [JSON_OPTION],
    exit_codes: [EXIT_OK, EXIT_FAILED],
  },
  {
    name: "status",
    summary: "只读查询 Runtime 运行状态；不启动服务、不迁移数据、不改配置。",
    usage: "memhub status [--watch] [--interval <seconds>] [--json]",
    options: [WATCH_OPTION, INTERVAL_OPTION, JSON_OPTION],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "doctor",
    summary: "按诊断口径做同样的只读检查；默认不修复任何东西。",
    usage: "memhub doctor [--json]",
    options: [JSON_OPTION],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "logs",
    summary:
      "读取本机脱敏诊断日志；logs export 会把排查包直接存到下载目录。默认显示最近一次会话的 200 条事件。",
    usage:
      "memhub logs export [--json]\nmemhub logs [--since <15m|2h|7d|RFC3339>] [--session <id>] [--trace <id>] [--incident <id>] [--operation <id>] [--request <id>] [--job <id>] [--task <id>] [--component <name>] [--event <name>] [--errors-only] [--tail <1..5000>] [--follow] [--json]",
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
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE],
  },
  {
    name: "init",
    summary:
      "首次接入：选择来源 → 选择是否导入历史。接入即开启该 Harness 的处理；后续可用 sync pause/resume 冻结或恢复。在终端且不带 --source 时进入交互引导（默认全选）；非终端或加 --json 时不读 stdin、只列出可用来源和下一步。--all-detected 一次接入所有已检测来源。",
    usage:
      "memhub init [--json]\n            memhub init --source <id> [--source <id> ...] [--import] [--dry-run] [--full-rescan] [--confirm-overrides] [--json]\n            memhub init --all-detected [--import] [--dry-run] [--json]",
    options: [
      {
        flag: "--source <id>",
        summary: "要接入的来源，可重复；终端下不给则进入交互引导。",
      },
      {
        flag: "--all-detected",
        summary:
          "接入并（加 --import 时）导入全部 location_state=found 的来源。",
      },
      {
        flag: "--import",
        summary: "接入后立即导入该来源的历史会话（默认只接入不导入）。",
      },
      {
        flag: "--dry-run",
        summary: "只出计划，不接入也不导入；用于先看会做什么。",
      },
      {
        flag: "--full-rescan",
        summary: "导入时忽略既有游标做全量重扫。",
      },
      {
        flag: "--confirm-overrides",
        summary: "确认接受环境覆盖类来源（如 DSH_HOME 指定的目录）。",
      },
      JSON_OPTION,
    ],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "setup",
    summary: "生成无头首次导入计划；只读取已有来源，不探测目录、不写授权。",
    usage: "memhub setup plan --source <id> [--source <id> ...] [--json]",
    options: [
      {
        flag: "--source <id>",
        summary: "计划导入的来源，可重复 1..32 次。",
      },
      JSON_OPTION,
    ],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "sources",
    summary: "列出来源、执行已确认的导入计划。",
    usage:
      "memhub sources list [--json]\n            memhub sources import --plan-file <path> --confirm [--json]\n            memhub sources receipt <request-id> [--json]",
    options: [PLAN_FILE_OPTION, EXPLICIT_CONFIRM_OPTION, JSON_OPTION],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
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
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
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
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "jobs",
    summary:
      "启动或控制单个导入任务：start 创建 history/sync 作业；pause/resume/cancel 控制已有作业。所有写操作先出计划，必须显式 --confirm；list/show 为只读。",
    usage:
      "memhub jobs list [--json]\n" +
      "            memhub jobs show <job-id> [--json]\n" +
      "            memhub jobs start --source <id> [--operation <history|sync>] [--confirm] [--json]\n" +
      "            memhub jobs <pause|resume|cancel> --job <id> [--confirm] [--json]",
    options: [
      {
        flag: "--source <id>",
        summary: "start 要导入的来源；必须是已连接的来源。",
      },
      {
        flag: "--operation <history|sync>",
        summary: "start 的导入类型，默认 history。",
      },
      {
        flag: "--job <id>",
        summary: "pause/resume/cancel 的目标导入任务。",
      },
      DIRECT_CONFIRM_OPTION,
      JSON_OPTION,
    ],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "models",
    summary:
      "交互配置模型，或生成配置/探测计划、执行已确认操作与查询回执；脚本模式的凭据只从受控文件读取。",
    usage:
      "memhub models list [--json]\n            memhub models show <profile-id> [--json]\n            memhub models plan --config-file <path> [--json]\n            memhub models configure --plan-file <path> --confirm [--api-key-file <path>] [--json]\n            memhub models receipt <operation-id> [--json]\n            memhub models edit <profile-id> --config-file <path> [--confirm] [--api-key-file <path>] [--json]\n            memhub models activate <profile-id> [--confirm] [--json]\n            memhub models probe-plan <profile-id> [--json]\n            memhub models probe --plan-file <path> --confirm [--json]\n            memhub models probe-receipt <operation-id> [--json]\n            memhub models remove <profile-id> [--confirm] [--json]",
    alternative_usage: "memhub models setup",
    options: [
      {
        flag: "--config-file <path>",
        summary: "不含 api_key 的有界 JSON 模型配置。",
      },
      PLAN_FILE_OPTION,
      EXPLICIT_CONFIRM_OPTION,
      {
        flag: "--api-key-file <path>",
        summary: "从 regular file 读取凭据；内容不进入参数、输出或错误。",
      },
      JSON_OPTION,
    ],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "memories",
    summary: "通过 SDK 数据面列出、查看、修改或物理删除记忆。",
    usage:
      "memhub memories list [--status <state>] [--offset <n>] [--limit <n>] [--json]\n            memhub memories show <memory-id> [--json]\n            memhub memories update <memory-id> --content-file <path> [--confirm] [--json]\n            memhub memories delete <memory-id> [--confirm] [--json]",
    options: [
      {
        flag: "--status <state>",
        summary: `记忆状态，取值 ${MEMORY_STATUSES.join("、")}。`,
      },
      { flag: "--offset <n>", summary: "跳过前 n 条，默认 0。" },
      {
        flag: "--limit <n>",
        summary: "本页最多返回多少条，默认 100，上限 500。",
      },
      {
        flag: "--content-file <path>",
        summary:
          "从有界普通文件读取新增/纠正正文；计划只保存摘要，执行时再次校验。",
      },
      DIRECT_CONFIRM_OPTION,
      JSON_OPTION,
    ],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "profile",
    summary: "读取 SDK 组装后的用户长期画像（L0 + 已启用的长期画像补充）。",
    usage: "memhub profile [--json]",
    options: [DATA_DIR_OPTION, JSON_OPTION],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "calls",
    summary:
      "清除本机调用记录；不可逆删除，默认只出计划，必须显式 --confirm 才执行。",
    usage:
      "memhub calls clear --event <id> [--event <id> ...] [--confirm] [--json]\n            memhub calls clear --all [--confirm] [--json]",
    options: [
      {
        flag: "--event <id>",
        summary: "要清除的调用记录事件 ID，可重复，最多 100 条。",
      },
      {
        flag: "--all",
        summary: "清除全部调用记录；与 --event 互斥。",
      },
      DIRECT_CONFIRM_OPTION,
      JSON_OPTION,
    ],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "notes",
    summary:
      "端内新增记忆（memory-notes）：从普通文件新增，或显式确认后处理已有笔记。",
    usage:
      "memhub notes add --content-file <path> [--json]\n            memhub notes apply --plan-file <path> --content-file <path> --confirm [--json]\n            memhub notes process <note-id> [--confirm] [--json]",
    options: [
      {
        flag: "--content-file <path>",
        summary: "从有界普通文件读取新增正文；计划只保存摘要，执行时重新校验。",
      },
      PLAN_FILE_OPTION,
      DIRECT_CONFIRM_OPTION,
      JSON_OPTION,
    ],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "graph",
    summary: "图谱宽度不匹配时的用户决定；默认只出计划，--confirm 后才执行。",
    usage:
      "memhub graph width --decision <keep_existing|rebuild_with_new|export_first> [--confirm] [--json]",
    options: [
      {
        flag: "--decision <decision>",
        summary:
          "keep_existing（保持）、export_first（先导出）、rebuild_with_new（导出后重建）。",
      },
      DIRECT_CONFIRM_OPTION,
      JSON_OPTION,
    ],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
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
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "recall",
    summary:
      "从当前授权的原文与治理记忆统一检索上下文；本地只读，不调用模型、不触发补索引。",
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
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_RUNTIME_STOPPED],
  },
  {
    name: "service-plan",
    summary: "生成用户级服务计划；只输出计划，不写文件也不执行命令。",
    usage: `memhub service-plan ${SERVICE_USAGE}`,
    options: SERVICE_OPTIONS,
    exit_codes: [EXIT_OK, EXIT_PLAN_BLOCKED],
  },
  {
    name: "service-install",
    summary: "预览用户级服务安装；只有 --yes 才写入服务文件并注册。",
    usage: `memhub service-install ${SERVICE_USAGE} [--yes]`,
    options: [...SERVICE_OPTIONS, CONFIRM_OPTION],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_PLAN_BLOCKED],
  },
  {
    name: "service-uninstall",
    summary:
      "预览用户级服务卸载；只有 --yes 才注销并删除逐字匹配的受管服务文件，不删数据。",
    usage: `memhub service-uninstall ${SERVICE_USAGE} [--yes]`,
    options: [...SERVICE_OPTIONS, CONFIRM_OPTION],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_PLAN_BLOCKED],
  },
  {
    name: "install",
    summary:
      "预览发行目录安装、memhub/memhub-cli/one-memory 短链接、PATH 条目与用户服务计划；只有 --yes 才执行并在失败时回滚。",
    usage: `memhub install ${INSTALL_USAGE}`,
    options: INSTALL_OPTIONS,
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_PLAN_BLOCKED],
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
      "预览升级；只有 --yes 才切换受管版本并重启服务，失败时恢复旧版本，不删数据。",
    usage: `memhub upgrade ${INSTALL_USAGE}`,
    options: INSTALL_OPTIONS,
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_PLAN_BLOCKED],
  },
  {
    name: "delete-data",
    summary:
      "独立、二次确认地删除 One Memory Home（数据）。这是唯一会删除数据的命令；uninstall 不会。默认只出预览；需 Runtime 已停止，并显式 --confirm <path> 复述该目录。",
    usage:
      "memhub delete-data [--data-dir <path>] [--yes --confirm <path>] [--json]",
    options: [
      DATA_DIR_OPTION,
      CONFIRM_OPTION,
      {
        flag: "--confirm <path>",
        summary:
          "第二次确认：逐字复述预览里的数据目录；缺少它时即使 --yes 也不删除。",
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
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE],
  },
  {
    name: "help",
    summary: "显示总帮助或单个命令的用法。",
    usage: "memhub help [command] [--json]",
    options: [JSON_OPTION],
    exit_codes: [EXIT_OK],
  },
  {
    name: "version",
    summary: "显示 CLI 版本。",
    usage: "memhub version [--json]",
    options: [JSON_OPTION],
    exit_codes: [EXIT_OK],
  },
  {
    name: "path",
    summary:
      "查看或登记被扫描的目录（scan roots）。list 只读；add/remove 预览后加 --yes 才提交；均已接入 Runtime 的 scan-roots 端点。",
    usage:
      "memhub path list [--json]\n" +
      "            memhub path add --root <path> [--source <id>] [--no-recursive] [--yes]\n" +
      "            memhub path remove --root <path> [--yes]",
    options: [DATA_DIR_OPTION, JSON_OPTION, CONFIRM_OPTION],
    exit_codes: [EXIT_OK, EXIT_USAGE, EXIT_FAILED, EXIT_PLAN_BLOCKED],
  },
  {
    name: "adapter",
    summary:
      "查看 Harness Adapter 的就绪状态与最近一次探测结果（list），或现场探测某个来源（probe --source <id>）；两者均为只读，已接入 Runtime 的 adapters/探测端点。",
    usage:
      "memhub adapter list [--json]\n" +
      "            memhub adapter probe --source <id> [--json]",
    options: [DATA_DIR_OPTION, JSON_OPTION],
    exit_codes: [EXIT_OK, EXIT_USAGE, EXIT_FAILED],
  },
  {
    name: "scan",
    summary:
      "立即扫描；默认只输出计划，加 --yes 才真正执行。指定 --source 扫描单个来源，--all 一次扫描/导入全部已检测来源（Runtime 按来源寻址，不接受裸路径）。",
    usage:
      "memhub scan [--source <id>] [--full] [--yes] [--json]\n            memhub scan --all [--yes] [--json]",
    options: [
      {
        flag: "--source <id>",
        summary: "按单个来源扫描；与 --all 互斥。",
      },
      {
        flag: "--all",
        summary:
          "处理全部 location_state=found 的来源，按来源逐条回执；部分成功返回非 0。",
      },
      {
        flag: "--full",
        summary: "忽略既有游标做全量重扫；默认只读取增量。",
      },
      DATA_DIR_OPTION,
      JSON_OPTION,
      CONFIRM_OPTION,
    ],
    exit_codes: [EXIT_OK, EXIT_USAGE, EXIT_FAILED, EXIT_PLAN_BLOCKED],
  },
  {
    name: "write",
    summary: "提交 OpenAI messages 到 SDK 写入队列，或按 request id 查询状态。",
    usage:
      "memhub write submit --messages-file <path> --session <id> [--source <id>] [--request-id <id>] [--memory-at <RFC3339>] [--live] [--json]\n            memhub write status --id <request-id> [--json]",
    options: [DATA_DIR_OPTION, JSON_OPTION],
    exit_codes: [EXIT_OK, EXIT_USAGE, EXIT_FAILED],
  },
  {
    name: "task",
    summary:
      "兼容别名：按 request id 查询写入状态；新脚本请使用 write status。",
    usage: "memhub task --id <request-id> [--json]",
    options: [DATA_DIR_OPTION, JSON_OPTION],
    exit_codes: [EXIT_OK, EXIT_USAGE, EXIT_FAILED],
  },
  {
    name: "backup",
    summary:
      "备份与恢复 MemHub 数据（两阶段）。create 调用 Runtime 的备份端点；restore 会覆盖现有数据，要求 Runtime 已停止且 `--confirm` 逐字重复 `--source`，随后由非 Runtime 维护态 Core 完成受管校验、保护性备份和原子替换。",
    usage:
      "memhub backup create --target <path> [--yes] [--json]\n" +
      "            memhub backup restore --source <path> --yes --confirm <path> [--json]",
    options: [
      DATA_DIR_OPTION,
      JSON_OPTION,
      CONFIRM_OPTION,
      {
        flag: "--confirm <path>",
        summary: "restore 要求逐字重复 --source 路径；不一致时不做任何改动。",
      },
    ],
    exit_codes: [EXIT_OK, EXIT_USAGE, EXIT_FAILED, EXIT_PLAN_BLOCKED],
  },
  {
    name: "ui",
    summary:
      "用系统默认浏览器打开 Runtime 托管的 Web Console：先向 Core 换取一次性 loopback 登录码，再交给系统浏览器由 Core 置 HttpOnly/SameSite=Strict session；CLI 不向浏览器暴露裸 Core token。Core 未提供该合同时如实返回 ui_session_contract_unavailable（exit 4）。",
    usage: "memhub ui [--json]",
    options: [JSON_OPTION],
    exit_codes: [EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_PLAN_BLOCKED],
  },
];

export function findCommand(name: string): CommandDescriptor | undefined {
  return COMMANDS.find((command) => command.name === name);
}
