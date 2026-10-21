import {
  type ConnectionMaterial,
  connectionPrompt,
} from "@one-memory/contracts/gui-i18n";
import { Copy, RefreshCw } from "lucide-react";
import { useState } from "react";
import { copyText } from "./copy-text";
import { useOnboardingTranslation } from "./onboarding-language";

type Material = ConnectionMaterial;
type Mode = keyof Material;

export function ConnectionFallback() {
  const { locale, text } = useOnboardingTranslation();
  const [material, setMaterial] = useState<Material | null>(null);
  const [mode, setMode] = useState<Mode>("prompt");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [notice, setNotice] = useState("");
  const available = !!window.oneMemory?.getConnectionMaterial;
  // Localize only the template prose; never parse or rewrite technical material.
  const value = material
    ? mode === "prompt"
      ? connectionPrompt(locale, material)
      : (material[mode] ?? "")
    : "";

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
      <summary>{text("连接其他工具")}</summary>
      {!available ? (
        <p>{text("本机连接材料仅在桌面 App 中可用。")}</p>
      ) : (
        <>
          <p className="connection-boundary">
            {text("仅本机 MCP / 命令工具。纯网页聊天不支持直接连接本机。")}
          </p>
          <p>{text("列表内工具请用上方一键安装，此处仅供未列出的工具。")}</p>
          {loading && <p role="status">{text("正在读取本机连接配置…")}</p>}
          {error && (
            <p role="alert">
              {text("未能读取连接配置。")}
              <button
                type="button"
                className="quiet"
                onClick={() => void load()}
              >
                <RefreshCw size={15} aria-hidden="true" />
                {text("重试")}
              </button>
            </p>
          )}
          {material && (
            <>
              <div
                role="tablist"
                aria-label={text("连接材料格式")}
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
                    {text(label)}
                  </button>
                ))}
              </div>
              <textarea
                aria-label={text("本机连接材料")}
                readOnly
                value={value}
                spellCheck={false}
              />
              <p>
                {text(
                  "包含本机路径，不含记忆和密钥。片段会运行本机 MemHub 连接程序；粘贴前请确认，MemHub 需保持运行。",
                )}
              </p>
              <button
                type="button"
                className="secondary"
                onClick={async () => {
                  try {
                    await copyText(value);
                    setNotice("已复制，尚未建立连接。");
                  } catch {
                    setNotice("复制失败，可在上方选中文本复制。");
                  }
                }}
              >
                <Copy size={15} aria-hidden="true" />
                {text(
                  mode === "prompt"
                    ? "复制提示词"
                    : mode === "mcp"
                      ? "复制配置"
                      : "复制命令",
                )}
              </button>
              {notice && <p role="status">{text(notice)}</p>}
            </>
          )}
        </>
      )}
    </details>
  );
}
