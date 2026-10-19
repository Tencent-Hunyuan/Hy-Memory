import { Copy, RefreshCw } from "lucide-react";
import { useState } from "react";
import { copyText } from "./copy-text";

type Material = { displayName: string; expiresAt: string; skill: string };

export function ConnectionFallback() {
  const [displayName, setDisplayName] = useState("");
  const [material, setMaterial] = useState<Material | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [notice, setNotice] = useState("");
  const available = !!window.oneMemory?.createCustomHarnessSkill;

  async function generate() {
    const bridge = window.oneMemory;
    const name = displayName.trim();
    if (!bridge?.createCustomHarnessSkill || loading || !name) return;
    setLoading(true);
    setError(false);
    setNotice("");
    try {
      setMaterial(await bridge.createCustomHarnessSkill(name));
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <details className="connection-fallback">
      <summary>连接其他工具</summary>
      {!available ? (
        <p>本机配对仅在桌面 App 中可用。</p>
      ) : (
        <>
          <p className="connection-boundary">
            为未列出的本机 Harness 生成一次性配对 Skill。纯网页聊天、远端容器或另一台机器不能直接连接本机。
          </p>
          <label className="custom-harness-name">
            Harness 名称
            <input
              value={displayName}
              maxLength={80}
              placeholder="例如 Kimi Code"
              onChange={(event) => {
                setDisplayName(event.target.value);
                setMaterial(null);
                setError(false);
                setNotice("");
              }}
            />
          </label>
          <button
            type="button"
            disabled={loading || !displayName.trim()}
            onClick={() => void generate()}
          >
            {loading ? "正在生成…" : "生成配对 Skill"}
          </button>
          {error && (
            <p role="alert">
              未能生成配对 Skill。
              <button
                type="button"
                className="quiet"
                onClick={() => void generate()}
              >
                <RefreshCw size={15} aria-hidden="true" />
                重试
              </button>
            </p>
          )}
          {material && (
            <>
              <p>
                已为 {material.displayName} 生成一次性配对 Skill，有效期至{" "}
                {new Date(material.expiresAt).toLocaleTimeString()}。
              </p>
              <textarea
                aria-label="配对 Skill"
                readOnly
                value={material.skill}
                spellCheck={false}
              />
              <button
                type="button"
                className="secondary"
                onClick={async () => {
                  try {
                    await copyText(material.skill);
                    setNotice("已复制配对 Skill。请粘贴到对应 Harness 中执行。");
                  } catch {
                    setNotice("复制失败，可在上方选中文本复制。");
                  }
                }}
              >
                <Copy size={15} aria-hidden="true" />
                复制 Skill
              </button>
            </>
          )}
          {notice && <p role="status">{notice}</p>}
        </>
      )}
    </details>
  );
}
