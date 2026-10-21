import { Copy, RefreshCw } from "lucide-react";
import { useState } from "react";
import { copyText } from "./copy-text";
import { useTranslate, useUiLanguage } from "./ui-language";

type Material = { mcp: string; shell: string | null; prompt: string };
type Mode = keyof Material;

export function ConnectionFallback() {
  const t = useTranslate();
  const language = useUiLanguage();
  const [material, setMaterial] = useState<Material | null>(null);
  const [mode, setMode] = useState<Mode>("prompt");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [notice, setNotice] = useState<
    "" | "已复制，尚未建立连接。" | "复制失败，可在上方选中文本复制。"
  >("");
  const available = !!window.oneMemory?.getConnectionMaterial;

  async function load() {
    const bridge = window.oneMemory;
    if (!bridge?.getConnectionMaterial || loading) return;
    setLoading(true);
    setError(false);
    try {
      setMaterial(await bridge.getConnectionMaterial());
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <details
      className="connection-fallback"
      onToggle={(event) => {
        if (event.currentTarget.open && !material && !loading && !error)
          void load();
      }}
    >
      <summary>{t("连接其他工具")}</summary>
      {!available ? (
        <p>{t("本机连接材料仅在桌面 App 中可用。")}</p>
      ) : (
        <>
          <p className="connection-boundary">
            {t("仅本机 MCP / 命令工具。纯网页聊天不支持直接连接本机。")}
          </p>
          <p>{t("列表内工具请用上方一键安装，此处仅供未列出的工具。")}</p>
          {loading && <p role="status">{t("正在读取本机连接配置…")}</p>}
          {error && (
            <p role="alert">
              {t("未能读取连接配置。")}
              <button
                type="button"
                className="quiet"
                onClick={() => void load()}
              >
                <RefreshCw size={15} aria-hidden="true" />
                {t("重试")}
              </button>
            </p>
          )}
          {material && (
            <>
              <div
                role="tablist"
                aria-label={t("连接材料格式")}
                className="connection-modes"
              >
                {(
                  [
                    ["prompt", "连接提示词"],
                    ["mcp", "MCP 配置"],
                    ["shell", "命令"],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    aria-selected={mode === id}
                    disabled={material[id] === null}
                    onClick={() => {
                      setMode(id);
                      setNotice("");
                    }}
                  >
                    {t(label)}
                  </button>
                ))}
              </div>
              <textarea
                aria-label={t("本机连接材料")}
                readOnly
                value={material[mode] ?? ""}
                spellCheck={false}
              />
              <p>
                {t(
                  "包含本机路径，不含记忆和密钥。片段会运行本机 MemHub 连接程序；粘贴前请确认，MemHub 需保持运行。",
                )}
              </p>
              <button
                type="button"
                className="secondary"
                onClick={async () => {
                  try {
                    await copyText(material[mode] ?? "");
                    setNotice("已复制，尚未建立连接。");
                  } catch {
                    setNotice("复制失败，可在上方选中文本复制。");
                  }
                }}
              >
                <Copy size={15} aria-hidden="true" />
                {t("复制")}
                {language === "en" ? " " : ""}
                {mode === "prompt"
                  ? t("提示词")
                  : mode === "mcp"
                    ? t("配置")
                    : t("命令")}
              </button>
              {notice && <p role="status">{t(notice)}</p>}
            </>
          )}
        </>
      )}
    </details>
  );
}
