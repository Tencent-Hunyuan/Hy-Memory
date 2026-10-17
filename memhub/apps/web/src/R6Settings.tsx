import type { ModelProfile } from "@one-memory/contracts";
import { Download, RefreshCw } from "lucide-react";
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { getModelProfiles, getSedimentation, setSedimentation } from "./api";
import { ConnectionFallback } from "./ConnectionFallback";
import { exportDiagnosticsFile } from "./diagnostics-export";
import { ModelSettings, type ModelSettingsActions } from "./ModelSettings";
import { R6Button, R6Switch } from "./R6Controls";
import {
  refreshLoginPreference,
  setLoginPreference,
  useLoginPreference,
} from "./runtime-state";
import "./r6-settings.css";

type Group = "model" | "connections" | "help";

const labels: Record<Group, string> = {
  model: "模型",
  connections: "连接其他应用",
  help: "帮助与诊断",
};
const primaryGroups: Group[] = ["model", "connections"];

function safeEndpoint(value: string | null) {
  if (!value) return "本机 / 受管端点";
  try {
    const endpoint = new URL(value);
    return `${endpoint.origin}${endpoint.pathname.replace(/\/$/, "")}`;
  } catch {
    return "接口地址待核对";
  }
}

export function R6Settings({
  active = true,
  onDiagnostics: _onDiagnostics,
  showDiagnostics = import.meta.env.VITE_ENABLE_DIAGNOSTICS !== "false",
}: {
  onDiagnostics(): void;
  active?: boolean;
  /** Shown by default; a release build may still opt out with VITE_ENABLE_DIAGNOSTICS=false. */
  showDiagnostics?: boolean;
}) {
  const id = useId();
  const groups = showDiagnostics
    ? [...primaryGroups, "help" as const]
    : primaryGroups;
  const [group, setGroup] = useState<Group>("model");
  const [profiles, setProfiles] = useState<ModelProfile[] | null>(null);
  const [modelError, setModelError] = useState(false);
  const [configurationRefusal, setConfigurationRefusal] = useState(false);
  const [modelVersion, setModelVersion] = useState(0);
  const [configurationOpen, setConfigurationOpen] = useState(false);
  const [quitOpen, setQuitOpen] = useState(false);
  const [quitting, setQuitting] = useState(false);
  const [quitError, setQuitError] = useState("");
  const [exportingLogs, setExportingLogs] = useState(false);
  const [exportMessage, setExportMessage] = useState("");
  const [buildInfo] = useState(
    () =>
      window.oneMemory?.getBuildInfo?.() ?? {
        version: "development",
        buildNumber: "development",
        commit: "unknown",
      },
  );
  const [sedimentation, setSedimentationState] = useState<boolean | null>(null);
  const [sedimentationPending, setSedimentationPending] = useState(false);
  const [sedimentationError, setSedimentationError] = useState("");
  const [sedimentationUnavailable, setSedimentationUnavailable] =
    useState(false);
  const modelActions = useRef<ModelSettingsActions>(null);
  const quitPanel = useRef<HTMLElement>(null);
  const quitOpener = useRef<HTMLElement | null>(null);
  const currentModel = profiles?.find((profile) => profile.is_active) ?? null;

  useLayoutEffect(() => {
    if (!active) return;
    void group;
    const content = document.getElementById("main-content");
    if (content) content.scrollTop = 0;
  }, [active, group]);

  async function handleExportLogs() {
    setExportingLogs(true);
    setExportMessage("");
    try {
      const result = await exportDiagnosticsFile();
      setExportMessage(result.message);
    } finally {
      setExportingLogs(false);
    }
  }

  const readModels = useCallback(async () => {
    setModelError(false);
    try {
      setProfiles(await getModelProfiles());
      setConfigurationRefusal(false);
    } catch {
      setModelError(true);
    }
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: modelVersion is the explicit refresh trigger.
  useEffect(() => {
    if (!active) return;
    if (group !== "model") return;
    void readModels();
  }, [active, group, modelVersion, readModels]);

  const readSedimentation = useCallback(async () => {
    try {
      const state = await getSedimentation();
      setSedimentationState(state.enabled === true);
      setSedimentationUnavailable(false);
    } catch {
      // Left unknown rather than assumed on: showing "已停止" when the switch
      // could not be read would hide a running model.
      setSedimentationState(null);
      setSedimentationUnavailable(true);
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    if (group !== "model") return;
    void readSedimentation();
  }, [active, group, readSedimentation]);

  async function changeSedimentation(enabled: boolean) {
    setSedimentationPending(true);
    setSedimentationError("");
    try {
      const state = await setSedimentation(enabled);
      setSedimentationState(state.enabled === true);
    } catch {
      setSedimentationError(
        enabled
          ? "启用未能确认，仍显示上次确认的状态；请重试。"
          : "停止未能确认，模型可能仍在整理记忆，请重试。",
      );
      await readSedimentation();
    } finally {
      setSedimentationPending(false);
    }
  }

  const login = useLoginPreference() ?? null;
  const [dataReady, setDataReady] = useState(login !== null);
  const [loginPending, setLoginPending] = useState(false);
  const [dataError, setDataError] = useState("");
  const [readError, setReadError] = useState("");
  const [loginReadFailed, setLoginReadFailed] = useState(false);

  const readData = useCallback(async () => {
    try {
      await refreshLoginPreference();
      setReadError("");
      setLoginReadFailed(false);
    } catch {
      setLoginReadFailed(true);
      setReadError(
        "部分运行状态暂时无法读取，当前显示最后读到的状态，请重试。",
      );
    }
    setDataReady(true);
  }, []);

  useEffect(() => {
    if (!active) return;
    void readData();
  }, [active, readData]);

  async function changeLogin(enabled: boolean) {
    if (!window.oneMemory?.setLoginPreference) {
      setDataError("当前安装还不能设置登录后后台启动。");
      return;
    }
    setLoginPending(true);
    setDataError("");
    try {
      const nextLogin = await window.oneMemory.setLoginPreference({
        openAtLogin: enabled,
        userConfirmed: true,
      });
      setLoginPreference(nextLogin);
    } catch {
      setDataError("登录后后台启动设置未能确认，请重试。");
      await readData();
    } finally {
      setLoginPending(false);
    }
  }

  function openConfiguration() {
    setConfigurationRefusal(false);
    const opened = currentModel
      ? modelActions.current?.editProfile(currentModel)
      : modelActions.current?.add();
    if (opened) setConfigurationOpen(true);
    else setConfigurationRefusal(true);
  }

  async function quitApplication() {
    if (quitting) return;
    if (!window.oneMemory?.requestQuit) {
      setQuitError("当前安装还不能退出界面，请从菜单栏退出。");
      return;
    }
    setQuitting(true);
    setQuitError("");
    try {
      await window.oneMemory.requestQuit(true);
    } catch {
      setQuitError("退出界面未能确认，请重试。");
    } finally {
      setQuitting(false);
    }
  }

  useEffect(() => {
    if (!quitOpen) return;
    const before = quitOpener.current;
    quitPanel.current
      ?.querySelector<HTMLButtonElement>("button:not(:disabled)")
      ?.focus();
    return () => {
      if (before?.isConnected) before.focus();
    };
  }, [quitOpen]);

  function moveGroupFocus(event: React.KeyboardEvent<HTMLButtonElement>) {
    const focused = (event.currentTarget.id.split("-nav-").at(-1) ??
      group) as Group;
    const current = groups.indexOf(focused);
    let next: number | null = null;
    if (event.key === "ArrowDown") next = (current + 1) % groups.length;
    else if (event.key === "ArrowUp")
      next = (current - 1 + groups.length) % groups.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = groups.length - 1;
    if (next === null) return;
    event.preventDefault();
    document.getElementById(`${id}-nav-${groups[next]}`)?.focus();
  }

  return (
    <section className="r6-settings" aria-label="设置">
      <h1>设置</h1>
      <div className="r6-settings-layout">
        <nav aria-label="设置分组">
          {groups.map((key) => (
            <button
              type="button"
              key={key}
              id={`${id}-nav-${key}`}
              aria-controls={`${id}-panel-${key}`}
              aria-current={group === key ? "page" : undefined}
              onKeyDown={moveGroupFocus}
              onClick={() => setGroup(key)}
            >
              {labels[key]}
            </button>
          ))}
        </nav>
        <div className="r6-settings-content">
          <section
            id={`${id}-panel-model`}
            aria-labelledby={`${id}-nav-model`}
            hidden={group !== "model"}
            className="r6-settings-model"
          >
            <h2>记忆模型</h2>
            <div className="r6-model-overview">
              <p className="r6-model-caption">当前模型</p>
              <h3 className="r6-current-model">
                {currentModel?.name ??
                  (profiles === null
                    ? "正在读取模型…"
                    : modelError
                      ? "模型状态暂不可读"
                      : "尚未配置模型")}
              </h3>
              {currentModel && (
                <p className="r6-model-state">
                  {currentModel.model} · {safeEndpoint(currentModel.base_url)}
                </p>
              )}
              <div className="r6-model-actions">
                <R6Button
                  variant="primary"
                  disabled={profiles === null}
                  onClick={openConfiguration}
                >
                  {currentModel ? "修改模型" : "配置模型"}
                </R6Button>
                <R6Button
                  variant="quiet"
                  aria-label="刷新模型状态"
                  title="刷新模型状态"
                  onClick={() => setModelVersion((value) => value + 1)}
                >
                  <RefreshCw size={16} aria-hidden="true" />
                </R6Button>
              </div>
              {modelError && (
                <p role="alert" className="r6-settings-error">
                  {profiles === null
                    ? "模型状态未能读取，请重试。"
                    : "模型状态刷新未成功，仍显示上次确认的模型状态；请重试。"}
                </p>
              )}
              {configurationRefusal && (
                <p role="alert" className="r6-settings-error">
                  模型配置暂时无法打开：正在处理上一次创建或测试，或本机存储暂时不可用。
                  请等待当前操作结束后重试；现有配置和原文不会被改动。
                </p>
              )}
              {currentModel && (
                <div className="r6-setting-row">
                  <div>
                    <h3>用模型沉淀记忆</h3>
                    <p>
                      开启后，导入和同步到本机的会话会交给当前模型整理成记忆，会产生模型调用。
                      停止后不再调用模型、不再整理记忆，但会话仍会照常导入和保存；已排队的内容会留着，重新开启后继续。
                      模型配置始终保留，重新开启不需要重新填写。
                    </p>
                  </div>
                  <R6Switch
                    label="用模型沉淀记忆"
                    checked={sedimentation === true}
                    disabled={
                      sedimentation === null ||
                      sedimentationPending ||
                      sedimentationUnavailable
                    }
                    onCheckedChange={(enabled) =>
                      void changeSedimentation(enabled)
                    }
                  />
                </div>
              )}
              {sedimentationError && (
                <p role="alert" className="r6-settings-error">
                  {sedimentationError}
                </p>
              )}
              {sedimentationUnavailable && (
                <p role="alert" className="r6-settings-error">
                  当前安装还不能读取这个开关，请确认后台服务已启动后重试。
                </p>
              )}
            </div>
            <section
              className="r6-model-owner"
              hidden={!configurationOpen}
              aria-label="模型配置表单"
            >
              <ModelSettings
                compactSingle
                controlsRef={modelActions}
                onProfilesChanged={() => setModelVersion((value) => value + 1)}
                onSetupComplete={() => setConfigurationOpen(false)}
              />
            </section>
          </section>
          <section
            id={`${id}-panel-connections`}
            aria-labelledby={`${id}-nav-connections`}
            hidden={group !== "connections"}
            className="r6-settings-connections"
          >
            <h2>连接其他应用</h2>
            <ConnectionFallback />
          </section>
          {showDiagnostics && (
            <section
              id={`${id}-panel-help`}
              aria-labelledby={`${id}-nav-help`}
              hidden={group !== "help"}
              className="r6-settings-help"
            >
              <h2>帮助与诊断</h2>
              <div className="r6-setting-row">
                <div>
                  <h3>登录后启动后台服务</h3>
                  <p>无需打开界面，持续同步、整理和提供记忆。</p>
                </div>
                <R6Switch
                  label="登录后启动后台服务"
                  checked={login?.enabled === true}
                  disabled={
                    !dataReady ||
                    loginPending ||
                    loginReadFailed ||
                    login?.supported !== true
                  }
                  onCheckedChange={(enabled) => void changeLogin(enabled)}
                />
              </div>
              {(dataError || readError) && (
                <p role="alert" className="r6-settings-error">
                  {dataError || readError}
                </p>
              )}
              <div className="r6-setting-row">
                <div>
                  <h3>导出日志</h3>
                  <p>
                    {exportMessage ||
                      "把本机运行日志打包成一个文件，发给开发排查。"}
                  </p>
                </div>
                <R6Button
                  variant="quiet"
                  pending={exportingLogs}
                  onClick={() => void handleExportLogs()}
                >
                  <Download size={16} aria-hidden="true" />
                  导出日志
                </R6Button>
              </div>
              <div className="r6-setting-row r6-settings-about">
                <div>
                  <h3>关于</h3>
                  <dl>
                    <div>
                      <dt>版本</dt>
                      <dd>{buildInfo.version}</dd>
                    </div>
                    <div>
                      <dt>Build</dt>
                      <dd>{buildInfo.buildNumber}</dd>
                    </div>
                    <div>
                      <dt>Commit</dt>
                      <dd title={buildInfo.commit}>
                        {buildInfo.commit === "unknown"
                          ? "unknown"
                          : buildInfo.commit.slice(0, 12)}
                      </dd>
                    </div>
                  </dl>
                </div>
              </div>
            </section>
          )}
        </div>
      </div>
      {quitOpen && (
        <div className="r6-settings-dialog-layer" role="presentation">
          <section
            className="r6-settings-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby={`${id}-quit-title`}
            ref={quitPanel}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                if (!quitting) setQuitOpen(false);
                return;
              }
              if (event.key !== "Tab" || !quitPanel.current) return;
              const controls = [
                ...quitPanel.current.querySelectorAll<HTMLElement>(
                  "button:not(:disabled)",
                ),
              ];
              const first = controls[0];
              const last = controls.at(-1);
              if (!first || !last) return;
              if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
              } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
              }
            }}
          >
            <h2 id={`${id}-quit-title`}>确定退出 One Memory 界面吗？</h2>
            <p>后台服务会继续同步、整理和提供记忆。</p>
            {quitError && (
              <p role="alert" className="r6-settings-error">
                {quitError}
              </p>
            )}
            <footer>
              <R6Button disabled={quitting} onClick={() => setQuitOpen(false)}>
                取消
              </R6Button>
              <R6Button
                variant="primary"
                pending={quitting}
                onClick={() => void quitApplication()}
              >
                退出界面
              </R6Button>
            </footer>
          </section>
        </div>
      )}
    </section>
  );
}
