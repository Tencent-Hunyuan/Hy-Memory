import { Copy, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { copyText } from "./copy-text";
import { useGuiLanguage } from "./gui-language";

type Material = { displayName: string; expiresAt: string; skill: string };
type ConnectionMaterial = {
  mcp: string;
  shell: string | null;
  prompt: string;
};
type MaterialBridge = {
  getConnectionMaterial(): Promise<ConnectionMaterial>;
};

export function ConnectionFallback({
  defaultExpanded = false,
}: {
  defaultExpanded?: boolean;
} = {}) {
  const { locale, t } = useGuiLanguage();
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [displayName, setDisplayName] = useState("");
  const [material, setMaterial] = useState<Material | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [notice, setNotice] = useState("");
  const available = !!window.oneMemory?.createCustomHarnessSkill;
  const materialBridge = window.oneMemory as
    | (Window["oneMemory"] & Partial<MaterialBridge>)
    | undefined;

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
    <details
      className="connection-fallback"
      open={expanded}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>{t("connectionFallback.title")}</summary>
      {materialBridge?.getConnectionMaterial ? (
        <ConnectionMaterialFallback
          bridge={materialBridge as Window["oneMemory"] & MaterialBridge}
        />
      ) : !available ? (
        <p>{t("connectionFallback.desktopOnly")}</p>
      ) : (
        <>
          <p className="connection-boundary">
            {t("connectionFallback.description")}
          </p>
          <label className="custom-harness-name">
            {t("connectionFallback.name")}
            <input
              value={displayName}
              maxLength={80}
              placeholder={t("connectionFallback.namePlaceholder")}
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
            {loading
              ? t("connectionFallback.generating")
              : t("connectionFallback.generate")}
          </button>
          {error && (
            <p role="alert">
              {t("connectionFallback.generateFailed")}
              <button
                type="button"
                className="quiet"
                onClick={() => void generate()}
              >
                <RefreshCw size={15} aria-hidden="true" />
                {t("connectionFallback.retry")}
              </button>
            </p>
          )}
          {material && (
            <>
              <p>
                {t("connectionFallback.generated", {
                  name: material.displayName,
                  time: new Intl.DateTimeFormat(locale, {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  }).format(new Date(material.expiresAt)),
                })}
              </p>
              <textarea
                aria-label={t("connectionFallback.skillLabel")}
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
                    setNotice(t("connectionFallback.copied"));
                  } catch {
                    setNotice(t("connectionFallback.copyFailed"));
                  }
                }}
              >
                <Copy size={15} aria-hidden="true" />
                {t("connectionFallback.copy")}
              </button>
            </>
          )}
          {notice && <p role="status">{notice}</p>}
        </>
      )}
    </details>
  );
}

function ConnectionMaterialFallback({
  bridge,
}: {
  bridge: Window["oneMemory"] & MaterialBridge;
}) {
  const { locale, t } = useGuiLanguage();
  const [material, setMaterial] = useState<ConnectionMaterial | null>(null);
  const [tab, setTab] = useState<"prompt" | "mcp" | "shell">("prompt");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [notice, setNotice] = useState("");

  async function load() {
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

  useEffect(() => {
    let current = true;
    void bridge.getConnectionMaterial().then(
      (value) => {
        if (!current) return;
        setMaterial(value);
        setLoading(false);
      },
      () => {
        if (!current) return;
        setError(true);
        setLoading(false);
      },
    );
    // The material is a snapshot. Language changes re-render its presentation
    // without reading paths or snippets again.
    return () => {
      current = false;
    };
  }, [bridge]);

  if (loading && !material)
    return <p role="status">{t("connectionFallback.loading")}</p>;
  if (error && !material)
    return (
      <p role="alert">
        {t("connectionFallback.loadFailed")}
        <button type="button" className="quiet" onClick={() => void load()}>
          <RefreshCw size={15} aria-hidden="true" />
          {t("connectionFallback.retry")}
        </button>
      </p>
    );
  if (!material) return null;

  const shellBlock = material.shell
    ? t("connectionFallback.promptShell", { shell: material.shell })
    : "";
  const prompt =
    locale === "zh-CN"
      ? material.prompt
      : t("connectionFallback.promptTemplate", {
          mcp: material.mcp,
          shellBlock,
        });
  const value =
    tab === "prompt"
      ? prompt
      : tab === "mcp"
        ? material.mcp
        : (material.shell ?? "");
  const copyLabel =
    tab === "prompt"
      ? t("connectionFallback.copyPrompt")
      : tab === "mcp"
        ? t("connectionFallback.copyConfig")
        : t("connectionFallback.copyCommand");

  return (
    <>
      <p className="connection-boundary">
        {t("connectionFallback.localDescription")}
      </p>
      <fieldset>
        <legend>{t("connectionFallback.format")}</legend>
        <div role="tablist">
          {(
            [
              ["prompt", t("connectionFallback.promptTab")],
              ["mcp", t("connectionFallback.mcpTab")],
              ["shell", t("connectionFallback.commandTab")],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              disabled={value === "shell" && !material.shell}
              onClick={() => {
                setTab(value);
                setNotice("");
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </fieldset>
      <textarea
        aria-label={t("connectionFallback.materialLabel")}
        readOnly
        value={value}
        spellCheck={false}
      />
      <p>{t("connectionFallback.materialBoundary")}</p>
      <button
        type="button"
        className="secondary"
        onClick={async () => {
          try {
            await copyText(value);
            setNotice(t("connectionFallback.materialCopied"));
          } catch {
            setNotice(t("connectionFallback.copyFailed"));
          }
        }}
      >
        <Copy size={15} aria-hidden="true" />
        {copyLabel}
      </button>
      {notice && <p role="status">{notice}</p>}
    </>
  );
}
