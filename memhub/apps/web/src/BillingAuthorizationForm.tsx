import type { BillingAuthorizationState } from "@one-memory/contracts";
import type { MessageKey } from "@one-memory/i18n";
import { useEffect, useState } from "react";
import {
  deleteBillingAuthorization,
  getBillingAuthorization,
  setBillingAuthorization,
} from "./api";
import { useGuiLanguage } from "./gui-language";

const LABELS: Record<string, MessageKey> = {
  api_key: "finance.apiKey",
  project_id: "finance.projectId",
  api_key_id: "finance.apiKeyId",
  access_key_id: "finance.accessKeyId",
  access_key_secret: "finance.accessKeySecret",
  security_token: "finance.securityToken",
  secret_id: "finance.secretId",
  secret_key: "finance.secretKey",
  session_token: "finance.securityToken",
  access_token: "finance.azureToken",
  resource_id: "finance.azureResource",
};
export function BillingAuthorizationForm({
  profileId,
  onSaved,
}: {
  profileId: string;
  onSaved(): void;
}) {
  const { t } = useGuiLanguage();
  const [config, setConfig] = useState<BillingAuthorizationState | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [state, setState] = useState<
    "loading" | "ready" | "saving" | "saved" | "error"
  >("loading");
  useEffect(() => {
    let cancelled = false;
    void getBillingAuthorization(profileId)
      .then((value) => {
        if (!cancelled) {
          setConfig(value);
          setState("ready");
        }
      })
      .catch(() => {
        if (!cancelled) setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [profileId]);
  return (
    <form
      className="memory-usage-pricing-form"
      onSubmit={async (event) => {
        event.preventDefault();
        setState("saving");
        try {
          setConfig(await setBillingAuthorization(profileId, fields));
          setFields({});
          setState("saved");
          onSaved();
        } catch {
          setState("error");
        }
      }}
    >
      <p>{t("finance.authorizationNote")}</p>
      {config?.provider === "openai" && <p>{t("finance.openaiHint")}</p>}
      {config?.provider === "openrouter" && (
        <p>{t("finance.openrouterHint")}</p>
      )}
      {config?.provider === "azure" && <p>{t("finance.azureHint")}</p>}
      {config?.configured && <p>{t("finance.configured")}</p>}
      {config?.fields.map((field) => (
        <label key={field.name}>
          {LABELS[field.name] ? t(LABELS[field.name]) : field.name}
          <input
            type={field.secret ? "password" : "text"}
            autoComplete="off"
            required={field.required}
            maxLength={16384}
            value={fields[field.name] ?? ""}
            onChange={(event) => {
              setFields((previous) => ({
                ...previous,
                [field.name]: event.target.value,
              }));
              setState("ready");
            }}
          />
        </label>
      ))}
      {config && (
        <button type="submit" disabled={state === "saving"}>
          {t("finance.saveAuthorization")}
        </button>
      )}
      {config?.configured && (
        <button
          type="button"
          disabled={state === "saving"}
          onClick={async () => {
            setState("saving");
            try {
              setConfig(await deleteBillingAuthorization(profileId));
              setFields({});
              setState("ready");
              onSaved();
            } catch {
              setState("error");
            }
          }}
        >
          {t("finance.removeAuthorization")}
        </button>
      )}
      {config?.docs_url && (
        <a href={config.docs_url} target="_blank" rel="noopener noreferrer">
          {t("finance.authorizationDocs")}
        </a>
      )}
      {state === "error" && (
        <p role="alert">{t("finance.authorizationError")}</p>
      )}
      {state === "saved" && (
        <p role="status">{t("finance.authorizationSaved")}</p>
      )}
    </form>
  );
}
