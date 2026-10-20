import type { SourceResponse, UiMessage } from "@one-memory/contracts";
import { RefreshCw, ShieldCheck, ShieldOff, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type {
  RecallHostStatus,
  RecallPermissionReceipt,
  RecallPermissionStatus,
  RecallPermissionTool,
  SetRecallPermissionInput,
} from "./global";
import { hostPermissionBridge } from "./host-state";
import {
  eligibleSource,
  fullGrantSelection,
  grantableSources,
  selectableTools,
  selectionIsValid,
} from "./recall-grant";
import "./recall-permission-control.css";
import { getUiLanguage, useTranslate } from "./ui-language";

export interface RecallPermissionControlProps {
  host: RecallHostStatus;
  sources: SourceResponse[];
  onChanged(): void;
  disabled?: boolean;
  /** Existing Core-issued scope IDs only. This control never derives a scope. */
  workspaceScopes?: readonly { scope: string; label: string }[];
  compact?: boolean;
}

type WorkspaceScope = { scope: string; label: string };
type Snapshot = {
  hostId: string;
  label: string;
  installed: boolean;
  permission: RecallPermissionStatus & { receipt: RecallPermissionReceipt };
  sources: SourceResponse[];
  workspaces: WorkspaceScope[];
  action: "grant" | "revoke";
  returnFocus: HTMLElement;
  fallbackFocus: HTMLElement | null;
};
type Pending = "check" | "write" | "refresh";
const changedMessage: UiMessage =
  "安装身份、授权版本或所选来源范围已变化，请关闭后重新打开并核对。不会把本次确认用于变化后的授权。";
const toolNames: Record<RecallPermissionTool, UiMessage> = {
  om_recall: "检索记忆",
  om_ask: "记忆问答（可选）",
  om_conversations: "读取原始对话",
  om_matters: "事项（已有授权）",
  om_diagnostic_trace: "读取脱敏诊断记录",
};
const focusSelector =
  'button:not(:disabled), input:not(:disabled), [tabindex="0"]';

export function RecallPermissionControl(props: RecallPermissionControlProps) {
  const {
    host,
    sources,
    disabled = false,
    workspaceScopes = [],
    compact = false,
  } = props;
  const [local, setLocal] = useState<{
    baseline: string;
    status: RecallPermissionStatus;
  } | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [pending, setPending] = useState(false);
  const [granting, setGranting] = useState(false);
  const [notice, setNotice] = useState<UiMessage | null>(null);
  const [error, setError] = useState<UiMessage | null>(null);
  const t = useTranslate();
  const generation = useRef(0);
  const flight = useRef(false);
  const mounted = useRef(false);
  const control = useRef<HTMLElement>(null);
  const currentHost = useRef(host.hostId);
  currentHost.current = host.hostId;
  const baseline = JSON.stringify(freezeStatus(host.permission) ?? null);
  const localIsNewer =
    local?.status.receipt &&
    host.permission?.receipt &&
    local.status.hostId === host.hostId &&
    local.status.receipt.installationId ===
      host.permission.receipt.installationId &&
    local.status.receipt.revision > host.permission.receipt.revision &&
    host.permission.reason === null &&
    ["approved", "revoked", "install-only"].includes(host.permission.state);
  const status = freezeStatus(
    local &&
      local.status.hostId === host.hostId &&
      (local.baseline === baseline || localIsNewer)
      ? local.status
      : host.permission,
  );
  const bridge = permissionBridge();
  const receipt = validReceipt(status, host.hostId);
  const canGrant = !!receipt && canApprove(status) && host.installed;
  const canRevoke =
    !!receipt &&
    receipt.status === "approved" &&
    ["approved", "relink-required"].includes(status?.state ?? "");

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      generation.current += 1;
    };
  }, []);

  function receive(value: RecallPermissionStatus) {
    if (value.hostId === currentHost.current)
      setLocal({ baseline, status: freezeStatus(value) });
  }

  async function refresh() {
    const api = permissionBridge();
    if (!api || disabled || flight.current || snapshot) return;
    const hostId = host.hostId;
    const token = ++generation.current;
    flight.current = true;
    setPending(true);
    setError(null);
    try {
      const value = await api.read(hostId);
      if (
        !mounted.current ||
        token !== generation.current ||
        currentHost.current !== hostId
      )
        return;
      if (value?.hostId !== hostId)
        throw new Error("host_permission_invalid_receipt");
      receive(value);
    } catch {
      if (
        mounted.current &&
        token === generation.current &&
        currentHost.current === hostId
      )
        setError("未能读取调用授权状态，请稍后刷新核对。未更改权限。");
    } finally {
      if (mounted.current && token === generation.current) {
        flight.current = false;
        setPending(false);
      }
    }
  }

  /** The frozen picture a write is checked against, taken the moment the user
   * commits to one. Built for both routes into a grant: the sheet, and the
   * one-click allow that skips it, so neither can drift from the other.
   */
  function pictureOf(
    action: Snapshot["action"],
    returnFocus: HTMLElement,
    fallbackFocus: HTMLElement | null,
  ): Snapshot | null {
    if (!status || !receipt) return null;
    return {
      hostId: host.hostId,
      label: host.label,
      installed: host.installed,
      permission: { ...status, receipt },
      sources: grantableSources(sources),
      workspaces: knownWorkspaces(workspaceScopes),
      action,
      returnFocus,
      fallbackFocus,
    };
  }

  function open(action: Snapshot["action"], trigger: HTMLElement) {
    if (
      !bridge ||
      !status ||
      !receipt ||
      disabled ||
      flight.current ||
      snapshot ||
      (action === "grant" ? !canGrant : !canRevoke) ||
      document.querySelector('[role="dialog"][aria-modal="true"]')
    )
      return;
    const picture = pictureOf(action, trigger, control.current);
    if (!picture) return;
    setError(null);
    setSnapshot(picture);
  }

  /** Allow everything, without making the user open the scope sheet first.
   *
   * The sheet opens with every box already ticked, so its only remaining
   * content was a second confirmation of a choice nobody had made. The row
   * button now commits the same selection the sheet would have submitted,
   * through the same guard; the sheet stays reachable for anyone who wants to
   * narrow the scope. Nothing is granted before the installation identity and
   * revision are re-read and matched, which is what stops an approval from
   * being spent on a connection that changed underneath it.
   */
  async function grantAll(trigger: HTMLElement) {
    const api = bridge;
    if (
      !api ||
      !status ||
      !receipt ||
      disabled ||
      flight.current ||
      snapshot ||
      granting ||
      !canGrant ||
      receipt.status === "approved"
    )
      return;
    const picture = pictureOf("grant", trigger, control.current);
    if (!picture) return;
    setGranting(true);
    setNotice(null);
    setError(null);
    const hostId = host.hostId;
    try {
      const selection = fullGrantSelection({
        sources,
        workspaces: picture.workspaces,
        keep: receipt.allowedTools,
      });
      if (!selectionIsValid(selection))
        throw new Error("host_permission_invalid_selection");
      const review: SetRecallPermissionInput = Object.freeze({
        installationId: receipt.installationId,
        expectedRevision: receipt.revision,
        allowedTools: Object.freeze([...selection.allowedTools]),
        allowedScopes: Object.freeze([...selection.allowedScopes].sort()),
        userConfirmed: true,
      });
      const latest = await api.read(hostId);
      if (!mounted.current || currentHost.current !== hostId) return;
      if (!sameStatus(picture.permission, latest)) {
        receive(latest);
        setError(changedMessage);
        return;
      }
      const saved = await api.grant(hostId, review);
      if (!mounted.current || currentHost.current !== hostId) return;
      if (!validMutationResult(saved, picture, review))
        throw new Error("host_permission_invalid_receipt");
      receive(saved);
      setNotice("已允许 {name} 使用记忆。");
      // The write already landed: a failed list refresh is reported as such,
      // never as a failed grant, so nobody re-submits an approval that stuck.
      try {
        await props.onChanged();
      } catch {
        if (mounted.current && currentHost.current === hostId)
          setError(refreshAfterCommitMessage("grant"));
      }
    } catch (reason) {
      if (!mounted.current || currentHost.current !== hostId) return;
      // No automatic re-read here: a refresh clears the inline error, and the
      // row has its own refresh control for finding out what actually landed.
      setError(
        isStaleError(reason)
          ? changedMessage
          : "未能确认这次授权。请刷新核对后再试，不要重复提交。",
      );
    } finally {
      if (mounted.current) setGranting(false);
    }
  }

  return (
    <section
      className={`recall-permission-control${compact ? " recall-permission-compact" : ""}`}
      aria-label={t("{name} 调用权限", { name: host.label })}
      ref={control}
      tabIndex={-1}
    >
      <p className="recall-permission-status" role="status" hidden={compact}>
        {t(statusLabel(status, host.hostId))}
      </p>
      {!bridge && (
        <p className="recall-permission-meta">
          {t("当前桌面连接不支持完整权限管理，不能在此授权或撤回。")}
        </p>
      )}
      {status?.reason === "installation_incomplete" && (
        <p className="recall-permission-meta">
          {t("连接安装尚未完成，不能授权使用记忆。")}
        </p>
      )}
      {compact ? (
        <label className="recall-permission-switch">
          <span>{t("允许使用记忆")}</span>
          <input
            type="checkbox"
            aria-label={t("允许使用记忆")}
            checked={receipt?.status === "approved"}
            disabled={
              disabled ||
              !bridge ||
              pending ||
              granting ||
              !!snapshot ||
              (!canGrant && !canRevoke)
            }
            onChange={(event) => {
              if (event.target.checked) void grantAll(event.currentTarget);
              else open("revoke", event.currentTarget);
            }}
          />
        </label>
      ) : (
        <div className="recall-permission-entry-actions">
          {receipt?.status === "approved" ? (
            <button
              type="button"
              className="secondary"
              disabled={
                disabled || !bridge || !canGrant || pending || !!snapshot
              }
              onClick={(event) => open("grant", event.currentTarget)}
            >
              <ShieldCheck size={16} aria-hidden="true" />
              {t("调整调用权限")}
            </button>
          ) : (
            <>
              {/* The row's allow is the whole flow: the sheet's default is every
                tool and every eligible source, so showing it only to have the
                same choice confirmed twice was two clicks for one decision. */}
              <button
                type="button"
                className="secondary"
                disabled={
                  disabled ||
                  !bridge ||
                  !canGrant ||
                  pending ||
                  granting ||
                  !!snapshot
                }
                onClick={(event) => void grantAll(event.currentTarget)}
              >
                <ShieldCheck size={16} aria-hidden="true" />
                {t(granting ? "正在允许…" : "允许使用记忆")}
              </button>
              <button
                type="button"
                className="quiet"
                disabled={
                  disabled ||
                  !bridge ||
                  !canGrant ||
                  pending ||
                  granting ||
                  !!snapshot
                }
                onClick={(event) => open("grant", event.currentTarget)}
              >
                {t("调整范围")}
              </button>
            </>
          )}
          {canRevoke && (
            <button
              type="button"
              className="quiet"
              disabled={disabled || !bridge || pending || !!snapshot}
              onClick={(event) => open("revoke", event.currentTarget)}
            >
              <ShieldOff size={16} aria-hidden="true" />
              {t("撤回调用权限")}
            </button>
          )}
          <button
            type="button"
            className="quiet recall-permission-icon"
            aria-label={t("刷新调用权限")}
            title={t("刷新调用权限")}
            disabled={disabled || !bridge || pending || granting || !!snapshot}
            onClick={() => void refresh()}
          >
            <RefreshCw size={16} aria-hidden="true" />
          </button>
        </div>
      )}
      {pending && <p role="status">{t("正在读取调用授权状态…")}</p>}
      {granting && (
        <p role="status">
          {t("正在允许 {name} 使用记忆…", { name: host.label })}
        </p>
      )}
      {notice && (
        <p className="recall-permission-meta" role="status">
          {t(notice, { name: host.label })}
        </p>
      )}
      {error && (
        <p className="recall-permission-error" role="alert">
          {t(error)}
        </p>
      )}
      {snapshot && (
        <PermissionDialog
          snapshot={snapshot}
          currentHost={host}
          currentStatus={status}
          sources={sources}
          workspaces={knownWorkspaces(workspaceScopes)}
          disabled={disabled}
          onClose={() => setSnapshot(null)}
          onStatus={receive}
          onChanged={props.onChanged}
        />
      )}
    </section>
  );
}

function PermissionDialog({
  snapshot,
  currentHost,
  currentStatus,
  sources,
  workspaces,
  disabled,
  onClose,
  onStatus,
  onChanged,
}: {
  snapshot: Snapshot;
  currentHost: RecallHostStatus;
  currentStatus: RecallPermissionStatus | undefined;
  sources: SourceResponse[];
  workspaces: WorkspaceScope[];
  disabled: boolean;
  onClose(): void;
  onStatus(value: RecallPermissionStatus): void;
  onChanged(): void;
}) {
  const id = useId();
  const t = useTranslate();
  const panel = useRef<HTMLElement>(null);
  const receipt = snapshot.permission.receipt;
  const editing = receipt.status === "approved";
  const defaults = fullGrantSelection({
    sources: snapshot.sources,
    workspaces: snapshot.workspaces,
    keep: receipt.allowedTools,
  });
  const [tools, setTools] = useState<readonly RecallPermissionTool[]>(
    editing ? receipt.allowedTools : defaults.allowedTools,
  );
  const [scopes, setScopes] = useState<readonly string[]>(
    editing ? receipt.allowedScopes : defaults.allowedScopes,
  );
  const [phase, setPhase] = useState<"select" | "revoke" | "receipt">(
    snapshot.action === "revoke" ? "revoke" : "select",
  );
  // The input of the write in flight, set in the same tick as the write. State
  // would only be readable by the next render, and the write's own guards need
  // it right away: reading a stale value there would report the very receipt
  // the write produced as a change that invalidates it.
  const submitted = useRef<SetRecallPermissionInput | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [stale, setStale] = useState(false);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [error, setError] = useState<UiMessage | null>(null);
  const [result, setResult] = useState<RecallPermissionStatus | null>(null);
  const committed = useRef(false);
  const mounted = useRef(false);
  const generation = useRef(0);
  const operation = useRef<Pending | null>(null);
  const invalidated = useRef(false);
  const current = useRef({
    disabled,
    currentHost,
    currentStatus,
    sources,
    workspaces,
  });
  current.current = {
    disabled,
    currentHost,
    currentStatus,
    sources,
    workspaces,
  };
  const unverified = receipt.allowedScopes.filter(
    (scope) => !knownScope(scope, snapshot),
  );
  const selectionValid =
    selectionIsValid({
      allowedTools: [...tools],
      allowedScopes: [...scopes],
    }) && scopes.every((scope) => knownScope(scope, snapshot));
  // While a write is in flight the parent may report back the very receipt
  // this sheet is producing; that is the expected outcome, not a change.
  const changed =
    !committed.current && !matchesCurrent(operation.current !== null);
  const blocked =
    stale || changed || needsRefresh || disabled || !permissionBridge();

  function matchesCurrent(allowCommitted = false) {
    const value = current.current;
    return (
      value.currentHost.hostId === snapshot.hostId &&
      value.currentHost.installed === snapshot.installed &&
      (sameStatus(snapshot.permission, value.currentStatus) ||
        (allowCommitted &&
          !!value.currentStatus &&
          validMutationResult(
            value.currentStatus,
            snapshot,
            submitted.current,
          ))) &&
      (snapshot.action === "revoke" ||
        scopes.every((scope) => {
          if (scope.startsWith("source:")) {
            const saved = snapshot.sources.find(
              (source) => `source:${source.source_id}` === scope,
            );
            if (!saved) return true;
            return (
              value.sources.filter(
                (source) => source.source_id === saved.source_id,
              ).length === 1 &&
              sameSource(
                saved,
                value.sources.find(
                  (source) => source.source_id === saved.source_id,
                ),
              )
            );
          }
          if (scope.startsWith("workspace:v1:")) {
            const saved = snapshot.workspaces.find(
              (option) => option.scope === scope,
            );
            return (
              !saved ||
              value.workspaces.some(
                (option) =>
                  option.scope === scope && option.label === saved.label,
              )
            );
          }
          return true;
        }))
    );
  }

  useEffect(() => {
    if (changed) {
      invalidated.current = true;
      setStale(true);
      setConfirmed(false);
    }
  }, [changed]);

  useEffect(() => {
    mounted.current = true;
    const before = snapshot.returnFocus;
    const prior = new Map<HTMLElement, boolean>();
    const root = panel.current?.parentElement;
    const makeInert = () => {
      for (const child of document.body.children) {
        if (
          !(child instanceof HTMLElement) ||
          child === root ||
          prior.has(child)
        )
          continue;
        prior.set(child, child.inert);
        child.inert = true;
      }
    };
    makeInert();
    const observer = new MutationObserver(makeInert);
    observer.observe(document.body, { childList: true });
    const focusInside = () =>
      (
        panel.current?.querySelector<HTMLElement>(focusSelector) ??
        panel.current
      )?.focus();
    const containFocus = (event: FocusEvent) => {
      if (
        event.target instanceof Node &&
        !panel.current?.contains(event.target)
      )
        focusInside();
    };
    document.addEventListener("focusin", containFocus);
    focusInside();
    return () => {
      mounted.current = false;
      generation.current += 1;
      observer.disconnect();
      document.removeEventListener("focusin", containFocus);
      for (const [element, inert] of prior) element.inert = inert;
      if (before.isConnected && !before.matches(":disabled")) before.focus();
      else snapshot.fallbackFocus?.focus();
    };
  }, [snapshot]);

  // A completed action or phase switch can remove or disable the focused control.
  // biome-ignore lint/correctness/useExhaustiveDependencies: Focus follows phase changes as well as pending state.
  useEffect(() => {
    if (pending) panel.current?.focus();
    else if (
      !panel.current?.contains(document.activeElement) ||
      document.activeElement?.matches(":disabled")
    )
      panel.current?.querySelector<HTMLElement>(focusSelector)?.focus();
  }, [pending, phase]);

  const active = (token: number) =>
    mounted.current && generation.current === token;
  function begin(kind: Pending) {
    if (operation.current || !mounted.current) return null;
    operation.current = kind;
    setPending(kind);
    setError(null);
    return ++generation.current;
  }
  function finish(token: number) {
    if (!active(token)) return;
    operation.current = null;
    setPending(null);
  }
  function invalidate(value?: RecallPermissionStatus) {
    invalidated.current = true;
    setStale(true);
    setConfirmed(false);
    setError(changedMessage);
    if (value?.hostId === snapshot.hostId) onStatus(value);
  }
  function close() {
    if (operation.current === "write") return;
    generation.current += 1;
    onClose();
  }
  function toggleTool(tool: RecallPermissionTool) {
    if (pending || blocked) return;
    setConfirmed(false);
    setTools((values) => toggle(values, tool));
  }
  function toggleScope(scope: string) {
    if (pending || blocked) return;
    setConfirmed(false);
    setScopes((values) => toggle(values, scope));
  }
  /** Commit the sheet as it stands.
   *
   * There used to be a second screen here: a summary plus a "I confirm the
   * tools and scope above" checkbox, reached by a "review scope" button. The
   * scope is already the box the user is looking at, so the review screen only
   * restated the same selection — reported from real use as an unnecessary
   * step under a sheet that should have been self-evident. The button is now
   * the confirmation, and the desktop still refuses the write unless the
   * connection identity and revision are what they were when the sheet opened.
   */
  function approve() {
    if (
      operation.current ||
      blocked ||
      !selectionValid ||
      invalidated.current ||
      !matchesCurrent()
    )
      return;
    const input: SetRecallPermissionInput = Object.freeze({
      installationId: receipt.installationId,
      expectedRevision: receipt.revision,
      allowedTools: Object.freeze([...tools]),
      allowedScopes: Object.freeze([...scopes].sort()),
      userConfirmed: true,
    });
    submitted.current = input;
    void commit(input);
  }

  async function refresh() {
    const api = permissionBridge();
    if (!api || disabled) return;
    const token = begin("refresh");
    if (token === null) return;
    setConfirmed(false);
    try {
      const value = await api.read(snapshot.hostId);
      if (!active(token)) return;
      const expected = result ?? snapshot.permission;
      if (
        !sameStatus(expected, value) ||
        (!committed.current && (invalidated.current || !matchesCurrent()))
      ) {
        invalidate(value);
        return;
      }
      setNeedsRefresh(false);
      if (committed.current) {
        onStatus(value);
        try {
          await onChanged();
        } catch {
          if (active(token))
            setError(refreshAfterCommitMessage(snapshot.action));
        }
      }
    } catch {
      if (active(token)) {
        setNeedsRefresh(true);
        setError(
          committed.current
            ? refreshAfterCommitMessage(snapshot.action)
            : "未能刷新授权状态，请稍后重试。未再次提交权限变更。",
        );
      }
    } finally {
      finish(token);
    }
  }

  /** Write the grant or the revocation the sheet is showing.
   *
   * `grant` is the exact selection the user just approved; `revoke` carries no
   * selection and still needs its own checkbox, because withdrawing a
   * permission is not something a single click should be able to do by
   * accident.
   */
  async function commit(grant: SetRecallPermissionInput | null) {
    const api = permissionBridge();
    if (
      !api ||
      blocked ||
      invalidated.current ||
      committed.current ||
      (snapshot.action === "revoke" ? !confirmed : !grant)
    )
      return;
    const token = begin("check");
    if (token === null) return;
    try {
      const latest = await api.read(snapshot.hostId);
      if (!active(token)) return;
      if (
        invalidated.current ||
        !matchesCurrent() ||
        !sameStatus(snapshot.permission, latest)
      ) {
        invalidate(latest);
        return;
      }
      if (current.current.disabled || permissionBridge() !== api) return;
      if (snapshot.action === "grant" && !grant) return;
      operation.current = "write";
      setPending("write");
      let saved: RecallPermissionStatus;
      if (snapshot.action === "revoke") {
        saved = await api.revoke(snapshot.hostId, {
          installationId: receipt.installationId,
          expectedRevision: receipt.revision,
          userConfirmed: true,
        });
      } else {
        if (!grant) return;
        saved = await api.grant(snapshot.hostId, grant);
      }
      if (!active(token)) return;
      if (!validMutationResult(saved, snapshot, grant))
        throw new Error("host_permission_invalid_receipt");
      const changedDuringCommit = invalidated.current || !matchesCurrent(true);
      committed.current = true;
      setResult(freezeStatus(saved));
      setPhase("receipt");
      setConfirmed(false);
      setNeedsRefresh(false);
      setStale(changedDuringCommit);
      onStatus(saved);
      operation.current = "refresh";
      setPending("refresh");
      try {
        await onChanged();
      } catch {
        if (active(token)) setError(refreshAfterCommitMessage(snapshot.action));
      }
    } catch (reason) {
      if (!active(token)) return;
      setConfirmed(false);
      setNeedsRefresh(true);
      if (isStaleError(reason)) invalidate();
      else
        setError(
          snapshot.action === "revoke"
            ? "撤回结果尚未确认。请刷新核对后重新确认，不要重复提交。"
            : "保存结果尚未确认。请刷新核对后重新确认，不要重复提交。",
        );
    } finally {
      finish(token);
    }
  }

  const removed = [
    ...receipt.allowedTools
      .filter((tool) => !tools.includes(tool))
      .map((tool) => t(toolNames[tool])),
    ...receipt.allowedScopes.filter((scope) => !scopes.includes(scope)),
  ];
  const visibleError = stale || changed ? changedMessage : error;
  const offeredTools = selectableTools(receipt.allowedTools);

  return createPortal(
    <div className="consent-backdrop recall-permission-backdrop">
      <section
        className="consent-sheet recall-permission-sheet"
        role="dialog"
        aria-modal="true"
        aria-busy={pending !== null}
        aria-labelledby={`${id}-title`}
        ref={panel}
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            close();
          }
          if (event.key !== "Tab") return;
          const controls =
            panel.current?.querySelectorAll<HTMLElement>(focusSelector);
          const first = controls?.[0],
            last = controls?.[controls.length - 1];
          if (!first) {
            event.preventDefault();
            panel.current?.focus();
          } else if (
            event.shiftKey &&
            (document.activeElement === first ||
              document.activeElement === panel.current)
          ) {
            event.preventDefault();
            last?.focus();
          } else if (
            !event.shiftKey &&
            (document.activeElement === last ||
              document.activeElement === panel.current)
          ) {
            event.preventDefault();
            first.focus();
          }
        }}
      >
        <header className="recall-permission-heading">
          <div>
            <p className="eyebrow">{t("独立调用授权")}</p>
            <h2 id={`${id}-title`}>
              {snapshot.label} ·{" "}
              {t(snapshot.action === "revoke" ? "撤回调用权限" : "使用记忆")}
            </h2>
          </div>
          <button
            type="button"
            className="quiet recall-permission-icon"
            aria-label={t("关闭")}
            title={t("关闭")}
            disabled={pending === "write"}
            onClick={close}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>
        <div className="recall-permission-body">
          <p className="recall-permission-meta">
            {t("安装：")}
            <code>{receipt.installationId}</code>
            {t(" · 授权版本 ")}
            {result?.receipt?.revision ?? receipt.revision}
          </p>
          {phase === "select" && (
            <>
              <p>
                {t(
                  "连接安装与使用记忆分别授权。所选权限仅用于此应用的这次安装。",
                )}
              </p>
              <fieldset
                className="recall-permission-options"
                disabled={!!pending || blocked}
              >
                <legend>{t("允许的工具")}</legend>
                {offeredTools.map((tool) => (
                  <label className="recall-permission-choice" key={tool}>
                    <input
                      type="checkbox"
                      aria-label={t(toolNames[tool])}
                      checked={tools.includes(tool)}
                      onChange={() => toggleTool(tool)}
                    />
                    <span>
                      {t(toolNames[tool])}
                      {tool === "om_ask" && (
                        <small>
                          {t(
                            "可能使用已配置的模型处理。本次只授予此应用的记忆读取范围。",
                          )}
                        </small>
                      )}
                    </span>
                  </label>
                ))}
              </fieldset>
              <fieldset
                className="recall-permission-options"
                disabled={!!pending || blocked}
              >
                <legend>{t("允许的记忆范围")}</legend>
                <label className="recall-permission-choice">
                  <input
                    type="checkbox"
                    checked={scopes.includes("all-apps")}
                    onChange={() => toggleScope("all-apps")}
                  />
                  <span>{t("跨应用共享记忆（all-apps）")}</span>
                </label>
                <p className="recall-permission-meta">
                  {t(
                    "仅限已允许共享的记忆，不等于全部原始对话，也不扩大来源读取授权。",
                  )}
                </p>
                {snapshot.workspaces.map((option) => (
                  <label
                    className="recall-permission-choice"
                    key={option.scope}
                  >
                    <input
                      type="checkbox"
                      aria-label={t("工作区：{name}", { name: option.label })}
                      checked={scopes.includes(option.scope)}
                      onChange={() => toggleScope(option.scope)}
                    />
                    <span>
                      {t("工作区：{name}", { name: option.label })}
                      <small>{option.scope}</small>
                    </span>
                  </label>
                ))}
              </fieldset>
              <fieldset
                className="recall-permission-options"
                disabled={!!pending || blocked}
              >
                <legend>{t("原始对话来源")}</legend>
                <p className="recall-permission-meta">
                  {t(
                    "读取原始对话需同时允许原始对话工具、all-apps 和明确选择的已授权来源。不会自动纳入其他来源。",
                  )}
                </p>
                {!snapshot.sources.length && (
                  <p>
                    {t("暂无可核对的已授权来源，不能新增原始对话读取权限。")}
                  </p>
                )}
                {snapshot.sources.map((source) => (
                  <label
                    className="recall-permission-choice"
                    key={source.source_id}
                  >
                    <input
                      type="checkbox"
                      aria-label={t("{name} 原始对话", {
                        name: source.display_name,
                      })}
                      checked={scopes.includes(`source:${source.source_id}`)}
                      onChange={() => toggleScope(`source:${source.source_id}`)}
                    />
                    <span>
                      {source.display_name}
                      <small>
                        source:{source.source_id}
                        {t(" · 来源授权版本 ")}
                        {source.grant_revision}
                      </small>
                    </span>
                  </label>
                ))}
              </fieldset>
              {unverified.length > 0 && (
                <fieldset
                  className="recall-permission-options"
                  disabled={!!pending || blocked}
                >
                  <legend>{t("未能核对的已有范围")}</legend>
                  <p>
                    {t(
                      "以下范围不会自动丢弃。继续更新需明确移除，或取消并补齐来源、工作区信息后再打开。",
                    )}
                  </p>
                  {unverified.map((scope) => (
                    <label className="recall-permission-choice" key={scope}>
                      <input
                        type="checkbox"
                        checked={!scopes.includes(scope)}
                        aria-label={t("移除未核对范围：{scope}", { scope })}
                        onChange={() => toggleScope(scope)}
                      />
                      <span>
                        {t("移除此范围")}
                        <small>{scope}</small>
                      </span>
                    </label>
                  ))}
                </fieldset>
              )}
            </>
          )}
          {editing && phase === "select" && (
            <section
              className="recall-permission-summary"
              aria-label={t("当前授权")}
            >
              <h3>{t("当前授权")}</h3>
              <PermissionSummary
                tools={receipt.allowedTools}
                scopes={receipt.allowedScopes}
                snapshot={snapshot}
              />
              <p>
                {t(
                  "本次确认将替换当前全部工具和范围，不会与旧授权合并。未选中的权限将被移除。",
                )}
              </p>
            </section>
          )}
          {/* The scope is on this screen rather than on a second one: the sheet
              already opens with everything ticked, so restating the same list
              behind another button was a step with no decision in it. */}
          {phase === "select" && (
            <section
              className="recall-permission-summary"
              aria-label={t("本次授权")}
            >
              <h3>{t(editing ? "本次将授权" : "本次将授权（默认全选）")}</h3>
              <PermissionSummary
                tools={tools}
                scopes={scopes}
                snapshot={snapshot}
              />
              {removed.length > 0 && (
                <p>
                  {t("本次将移除：{items}", {
                    items: removed.join(getUiLanguage() === "en" ? ", " : "、"),
                  })}
                </p>
              )}
            </section>
          )}
          {phase === "revoke" && (
            <>
              <p>
                {t(
                  "这将结束此安装的调用授权，阻止之后的记忆读取。不会删除来源或卸载连接，也不会关闭其他应用的授权。",
                )}
              </p>
              <p>{t("不能收回此应用此前已读取的内容。")}</p>
              <PermissionSummary
                tools={receipt.allowedTools}
                scopes={receipt.allowedScopes}
                snapshot={snapshot}
              />
            </>
          )}
          {phase === "revoke" && (
            <label className="recall-permission-choice recall-permission-final">
              <input
                type="checkbox"
                checked={confirmed && !blocked}
                disabled={!!pending || blocked}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span>{t("我确认撤回此安装的调用授权")}</span>
            </label>
          )}
          {phase === "receipt" && result?.receipt && (
            <>
              <p role="status">
                {t(
                  snapshot.action === "revoke"
                    ? "此安装的调用授权已撤回。"
                    : "调用授权已保存。",
                )}
              </p>
              <p className="recall-permission-meta">
                {t("授权版本 {revision}", {
                  revision: result.receipt.revision,
                })}
              </p>
              <PermissionSummary
                tools={result.receipt.allowedTools}
                scopes={result.receipt.allowedScopes}
                snapshot={snapshot}
              />
            </>
          )}
          {snapshot.action === "grant" && (
            <p className="recall-permission-meta">
              {t(
                "本次不会安装连接、导入历史或开启持续同步，也不改变内置 SDK 的本地记忆处理。授权不代表应用已实际调用或采用记忆。",
              )}
            </p>
          )}
          {pending && (
            <p role="status">
              {t(
                pending === "write"
                  ? "正在提交调用权限…"
                  : "正在核对安装身份与授权版本…",
              )}
            </p>
          )}
          {visibleError && (
            <p className="recall-permission-error" role="alert">
              {t(visibleError)}
            </p>
          )}
        </div>
        <footer className="consent-actions recall-permission-actions">
          <button
            type="button"
            className="secondary"
            disabled={pending === "write"}
            onClick={close}
          >
            {t(phase === "receipt" ? "完成" : "取消")}
          </button>
          {(needsRefresh || error) && !stale && !changed && (
            <button
              type="button"
              className="secondary"
              disabled={!!pending || disabled || !permissionBridge()}
              onClick={() => void refresh()}
            >
              <RefreshCw size={16} aria-hidden="true" />
              {t("刷新核对")}
            </button>
          )}
          {phase === "select" && (
            <button
              type="button"
              disabled={!!pending || blocked || !selectionValid}
              onClick={approve}
            >
              <ShieldCheck size={16} aria-hidden="true" />
              {t(editing ? "保存这次范围" : "允许并保存")}
            </button>
          )}
          {phase === "revoke" && (
            <button
              type="button"
              disabled={!!pending || blocked || !confirmed}
              onClick={() => void commit(null)}
            >
              <ShieldOff size={16} aria-hidden="true" />
              {t("确认撤回")}
            </button>
          )}
        </footer>
      </section>
    </div>,
    document.body,
  );
}

function PermissionSummary({
  tools,
  scopes,
  snapshot,
}: {
  tools: readonly RecallPermissionTool[];
  scopes: readonly string[];
  snapshot: Snapshot;
}) {
  const t = useTranslate();
  return (
    <>
      <p>
        {t("工具：{items}", {
          items:
            tools
              .map((tool) => t(toolNames[tool]))
              .join(getUiLanguage() === "en" ? ", " : "、") || t("无"),
        })}
      </p>
      <ul className="recall-permission-scope-list" aria-label={t("授权范围")}>
        {scopes.length ? (
          scopes.map((scope) => (
            <li key={scope}>
              <code>{scope}</code>
              {scope.startsWith("source:") && (
                <span>
                  {snapshot.sources.find(
                    (source) => `source:${source.source_id}` === scope,
                  )?.display_name ?? t("来源未能核对")}
                </span>
              )}
              {scope.startsWith("workspace:") && (
                <span>
                  {snapshot.workspaces.find((option) => option.scope === scope)
                    ?.label ?? t("工作区未能核对")}
                </span>
              )}
            </li>
          ))
        ) : (
          <li>{t("无授权范围")}</li>
        )}
      </ul>
    </>
  );
}

const permissionBridge = hostPermissionBridge;

function validReceipt(
  status: RecallPermissionStatus | undefined,
  hostId: string,
) {
  const receipt = status?.receipt;
  if (
    !receipt ||
    status?.hostId !== hostId ||
    receipt.hostId !== hostId ||
    typeof receipt.installationId !== "string" ||
    !receipt.installationId.trim() ||
    !Number.isSafeInteger(receipt.revision) ||
    receipt.revision < 1 ||
    !["install-only", "approved", "revoked"].includes(receipt.status) ||
    !Array.isArray(receipt.allowedTools) ||
    !receipt.allowedTools.every((tool) => Object.hasOwn(toolNames, tool)) ||
    !Array.isArray(receipt.allowedScopes) ||
    !receipt.allowedScopes.every(
      (scope) => typeof scope === "string" && validScope(scope),
    ) ||
    new Set(receipt.allowedTools).size !== receipt.allowedTools.length ||
    new Set(receipt.allowedScopes).size !== receipt.allowedScopes.length ||
    (["install-only", "approved", "revoked"].includes(status.state) &&
      receipt.status !== status.state)
  )
    return null;
  return receipt;
}

function freezeStatus(status: RecallPermissionStatus): RecallPermissionStatus;
function freezeStatus(
  status: RecallPermissionStatus | undefined,
): RecallPermissionStatus | undefined;
function freezeStatus(
  status: RecallPermissionStatus | undefined,
): RecallPermissionStatus | undefined {
  if (!status) return undefined;
  const receipt = validReceipt(status, status.hostId);
  // Explicitly copy display fields only; neither credentials nor unknown IPC fields belong in UI state.
  return Object.freeze({
    hostId: status.hostId,
    state: status.state,
    reason: status.reason,
    receipt: receipt
      ? Object.freeze({
          hostId: receipt.hostId,
          installationId: receipt.installationId,
          revision: receipt.revision,
          status: receipt.status,
          allowedTools: Object.freeze([...receipt.allowedTools]),
          allowedScopes: Object.freeze([...receipt.allowedScopes]),
        })
      : null,
  });
}

function canApprove(status: RecallPermissionStatus | undefined) {
  return (
    status?.reason === null &&
    ["install-only", "approved"].includes(status.state)
  );
}

function statusLabel(
  status: RecallPermissionStatus | undefined,
  hostId: string,
): UiMessage {
  if (!status || status.hostId !== hostId) return "调用授权状态尚未核对";
  if (status.state === "not-installed") return "尚未安装连接，不能授权使用记忆";
  if (status.state === "relink-required") {
    if (status.reason === "legacy_installation")
      return "检测到旧连接，请先重新连接，再允许使用记忆";
    if (status.reason === "managed_config_changed")
      return "连接配置已变化，请先核对并重新连接，再允许使用记忆";
    return "连接凭据需要更新，请先重新连接，再允许使用记忆";
  }
  if (status.state === "unavailable") return "调用授权服务暂不可用";
  if (status.state === "unsupported") return "此连接暂不支持调用权限管理";
  if (!validReceipt(status, hostId)) return "调用授权信息不完整，请刷新核对";
  if (status.state === "approved") return "已允许按所选范围使用记忆";
  if (status.state === "revoked")
    return "此安装的调用授权已撤回，需重新连接后再授权";
  return "已安装，尚未允许使用记忆";
}

function sameStatus(
  snapshot: RecallPermissionStatus,
  value: RecallPermissionStatus | undefined,
) {
  const receipt = validReceipt(value, snapshot.hostId);
  const saved = snapshot.receipt;
  return (
    !!receipt &&
    !!saved &&
    snapshot.state === value?.state &&
    snapshot.reason === value.reason &&
    saved.installationId === receipt.installationId &&
    saved.revision === receipt.revision &&
    saved.status === receipt.status &&
    sameSet(saved.allowedTools, receipt.allowedTools) &&
    sameSet(saved.allowedScopes, receipt.allowedScopes)
  );
}

function sameSet(a: readonly string[], b: readonly string[]) {
  return a.length === b.length && a.every((value) => b.includes(value));
}

function toggle<T>(values: readonly T[], value: T): T[] {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}

function validScope(scope: string) {
  return (
    scope === "all-apps" ||
    /^workspace:v1:[0-9a-f]{64}$/.test(scope) ||
    /^source:[a-z0-9][a-z0-9-]{0,127}$/.test(scope)
  );
}

function knownWorkspaces(
  options: RecallPermissionControlProps["workspaceScopes"],
): WorkspaceScope[] {
  const counts = new Map<string, number>();
  for (const option of options ?? [])
    counts.set(option.scope, (counts.get(option.scope) ?? 0) + 1);
  return (options ?? [])
    .filter(
      (option) =>
        /^workspace:v1:[0-9a-f]{64}$/.test(option.scope) &&
        typeof option.label === "string" &&
        !!option.label.trim() &&
        counts.get(option.scope) === 1,
    )
    .map((option) => ({ ...option }));
}

function sameSource(
  saved: SourceResponse,
  current: SourceResponse | undefined,
) {
  return (
    !!current &&
    eligibleSource(current) &&
    saved.root_id === current.root_id &&
    saved.grant_revision === current.grant_revision &&
    saved.candidate_path === current.candidate_path &&
    saved.root_label === current.root_label &&
    saved.candidate_kind === current.candidate_kind
  );
}

function knownScope(scope: string, snapshot: Snapshot) {
  return (
    scope === "all-apps" ||
    snapshot.sources.some((source) => `source:${source.source_id}` === scope) ||
    snapshot.workspaces.some((option) => option.scope === scope)
  );
}

function validMutationResult(
  status: RecallPermissionStatus,
  snapshot: Snapshot,
  review: SetRecallPermissionInput | null,
) {
  const receipt = validReceipt(status, snapshot.hostId);
  return (
    !!receipt &&
    status.reason === null &&
    receipt.installationId === snapshot.permission.receipt.installationId &&
    receipt.revision === snapshot.permission.receipt.revision + 1 &&
    (snapshot.action === "revoke"
      ? status.state === "revoked" &&
        receipt.allowedTools.length === 0 &&
        receipt.allowedScopes.length === 0
      : status.state === "approved" &&
        !!review &&
        sameSet(receipt.allowedTools, review.allowedTools) &&
        sameSet(receipt.allowedScopes, review.allowedScopes))
  );
}

function isStaleError(error: unknown) {
  const message = error instanceof Error ? error.message : "";
  return [
    "host_grant_revision_conflict",
    "host_grant_revoked",
    "host_permission_installation_mismatch",
    "host_permission_relink_required",
  ].some((code) => message.includes(code));
}

function refreshAfterCommitMessage(action: Snapshot["action"]): UiMessage {
  return action === "revoke"
    ? "授权已撤回，但列表刷新失败。刷新核对不会再次撤回。"
    : "授权已保存，但列表刷新失败。刷新核对不会再次授权。";
}
