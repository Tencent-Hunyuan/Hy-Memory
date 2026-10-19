import { Copy } from "lucide-react";
import { useRef, useState } from "react";
import { copyText } from "./copy-text";

type Material = { mcp: string; shell: string | null; prompt: string };
type Mode = keyof Material;

export function ConnectionFallback() {
  const [material, setMaterial] = useState<Material | null>(null);
  const [mode, setMode] = useState<Mode>("prompt");
  const [loading, setLoading] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState(false);
  const [notice, setNotice] = useState("");
  const available = !!window.oneMemory?.prepareConnectionMaterial;

  async function generateAndCopy() {
    const bridge = window.oneMemory;
    if (!bridge?.prepareConnectionMaterial || inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    setError(false);
    setNotice("");
    try {
      const next = await bridge.prepareConnectionMaterial();
      setMaterial(next);
      try {
        await copyText(next[mode] ?? next.prompt);
        setNotice(
          "已复制连接材料，尚未建立连接。请在目标工具中配置并验证查询。",
        );
      } catch {
        setNotice("材料已生成，但复制失败；可在下方选中文本复制。");
      }
    } catch {
      setMaterial(null);
      setError(true);
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }

  return (
    <details className="connection-fallback">
      <summary>连接其他工具</summary>
      {!available ? (
        <p>本机连接材料仅在桌面 App 中可用。</p>
      ) : (
        <>
          <p className="connection-boundary">
            仅本机 MCP / 命令工具。纯网页聊天不支持直接连接本机。
          </p>
          <p>列表内工具请用上方一键安装，此处仅供未列出的工具。</p>
          <p>
            点击“生成并复制”将授权此数据目录共用的一份本机身份，允许查询跨应用记忆和已导入会话的原文片段；多个工具使用同一身份与权限，不会自动导入新内容。
          </p>
          {!material && (
            <button
              type="button"
              className="secondary"
              disabled={loading}
              onClick={() => void generateAndCopy()}
            >
              <Copy size={15} aria-hidden="true" />
              生成并复制
            </button>
          )}
          {loading && <p role="status">正在准备本机连接材料…</p>}
          {error && (
            <p role="alert">
              无法生成连接材料；授权未完成或凭据不可用。不会自动重签，请检查本机权限状态后重试。
            </p>
          )}
          {material && (
            <>
              <div
                role="tablist"
                aria-label="连接材料格式"
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
                    {label}
                  </button>
                ))}
              </div>
              <textarea
                aria-label="本机连接材料"
                readOnly
                value={material[mode] ?? ""}
                spellCheck={false}
              />
              <p>
                包含本机路径，不含记忆和密钥。片段会运行本机 MemHub
                连接程序；粘贴前请确认，MemHub 需保持运行。
              </p>
              <button
                type="button"
                className="secondary"
                onClick={async () => {
                  try {
                    await copyText(material[mode] ?? "");
                    setNotice("已复制连接材料，尚未建立连接。");
                  } catch {
                    setNotice("复制失败，可在上方选中文本复制。");
                  }
                }}
              >
                <Copy size={15} aria-hidden="true" />
                复制
                {mode === "prompt"
                  ? "提示词"
                  : mode === "mcp"
                    ? "配置"
                    : "命令"}
              </button>
            </>
          )}
          {notice && <p role="status">{notice}</p>}
        </>
      )}
    </details>
  );
}
