// @vitest-environment jsdom
import type { ImportJob, SourceResponse } from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { completeOnboarding } from "./api";
import { FirstImportReview } from "./FirstImportReview";
import {
  LANGUAGE_STORAGE_KEY,
  LanguageProvider,
  useGuiLanguage,
} from "./gui-language";
import { OnboardingFlow } from "./OnboardingFlow";
import { OnboardingModelController } from "./OnboardingModelController";
import { R6ImportProgress } from "./R6ImportProgress";
import type { ImportSourcesController } from "./use-import-sources";

vi.mock("./api", () => ({
  completeOnboarding: vi.fn(),
  getModelProfiles: vi.fn(),
}));
const models = {
  pendingProbe: false,
  probeStorageFailed: false,
  pendingCreation: false,
  creationStorageFailed: false,
  error: null,
  create: vi.fn(),
  update: vi.fn(),
  probeProfile: vi.fn(),
  activate: vi.fn(),
};
vi.mock("./use-model-profiles", () => ({ useModelProfiles: () => models }));
const source: SourceResponse = {
  source_id: "cursor",
  display_name: "Cursor",
  available: true,
  authorized: false,
  detected: false,
  storage_kinds: ["jsonl"],
  root_label: null,
  candidate_path: "/fixture/cursor",
  candidate_kind: "default",
  candidate_root_id: "fixture-root",
  location_state: "found",
  imported_messages: 0,
  imported_conversations: 0,
  sync_enabled: false,
  sync_completeness: "additions_only",
  capabilities: {
    historical_import: "implemented",
    incremental_import: "implemented",
    live_sync: "not_implemented",
    recall: "not_implemented",
    installer: "not_implemented",
    verification: "fixture_only",
  },
};
function controller(overrides: Partial<SourceResponse> = {}) {
  const sources = [{ ...source, ...overrides }];
  return {
    sources,
    jobs: [],
    loading: false,
    refresh: vi.fn().mockResolvedValue({ sources, jobs: [], health: null }),
    connectForOnboarding: vi.fn(),
    detect: vi.fn(),
  } as unknown as ImportSourcesController;
}
function SwitchLanguage() {
  const { setPreference } = useGuiLanguage();
  return (
    <button type="button" onClick={() => void setPreference("zh-CN")}>
      中文
    </button>
  );
}
const state = {
  status: "not_started" as const,
  selected_source_ids: [],
  started_at: null,
  completed_at: null,
};
beforeEach(() => {
  delete window.oneMemory;
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  localStorage.clear();
  delete window.oneMemory;
});

it("changes the first-screen language without losing source choices or starting an import", async () => {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh-CN");
  const imports = controller();
  const finish = vi.fn();
  const user = userEvent.setup();
  render(
    <LanguageProvider>
      <OnboardingFlow state={state} imports={imports} onFinish={finish} />
    </LanguageProvider>,
  );
  const selector = screen.getByRole("combobox", { name: "界面语言" });
  await waitFor(() =>
    expect(screen.getByLabelText("导入 Cursor 历史")).toHaveProperty(
      "checked",
      true,
    ),
  );
  await user.click(screen.getByLabelText("导入 Cursor 历史"));
  await user.click(selector);
  await user.click(screen.getByRole("option", { name: "Español" }));
  expect(screen.getByRole("combobox", { name: "Idioma de la interfaz" })).toBe(
    selector,
  );
  expect(
    screen.getByLabelText("Importar el historial de Cursor"),
  ).toHaveProperty("checked", false);
  await user.click(screen.getByLabelText("Importar el historial de Cursor"));
  await user.click(selector);
  await user.click(screen.getByRole("option", { name: "简体中文" }));
  expect(screen.getByLabelText("导入 Cursor 历史")).toHaveProperty(
    "checked",
    true,
  );
  expect(imports.refresh).toHaveBeenCalledOnce();
  expect(imports.connectForOnboarding).not.toHaveBeenCalled();
  expect(imports.detect).not.toHaveBeenCalled();
  expect(completeOnboarding).not.toHaveBeenCalled();
  expect(finish).not.toHaveBeenCalled();
});

it("translates discovery, progress and privacy without importing or authorizing on language changes", async () => {
  const imports = controller();
  render(
    <LanguageProvider>
      <SwitchLanguage />
      <OnboardingFlow state={state} imports={imports} onFinish={vi.fn()} />
    </LanguageProvider>,
  );
  expect(
    await screen.findByRole("heading", {
      name: "Trae aquí el contexto que ya tienes",
    }),
  ).toBeTruthy();
  await waitFor(() =>
    expect(
      screen.getByLabelText<HTMLInputElement>("Importar el historial de Cursor")
        .checked,
    ).toBe(true),
  );
  expect(
    screen.getByRole("navigation", {
      name: "Progreso de la configuración inicial",
    }),
  ).toBeTruthy();
  expect(
    screen.getByText(
      "Antes de confirmar, solo se comprueba la información de las aplicaciones. No se leen conversaciones ni se instalan conexiones.",
    ),
  ).toBeTruthy();
  expect(
    screen.getByText("/fixture/cursor · Ubicación predeterminada"),
  ).toBeTruthy();
  fireEvent.click(screen.getByText("中文"));
  await screen.findByRole("heading", { name: "把熟悉的背景，带到这里" });
  expect(
    screen.getByLabelText<HTMLInputElement>("导入 Cursor 历史").checked,
  ).toBe(true);
  expect(imports.refresh).toHaveBeenCalledTimes(1);
  expect(imports.connectForOnboarding).not.toHaveBeenCalled();
  expect(imports.detect).not.toHaveBeenCalled();
});
it("translates stored discovery errors live without repeating discovery", async () => {
  const imports = controller();
  vi.mocked(imports.refresh).mockRejectedValue(new Error("fixture-offline"));
  render(
    <LanguageProvider>
      <SwitchLanguage />
      <OnboardingFlow state={state} imports={imports} onFinish={vi.fn()} />
    </LanguageProvider>,
  );
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain(
      "No se completó la detección",
    ),
  );
  fireEvent.click(screen.getByText("中文"));
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain("位置检测未完成"),
  );
  expect(imports.refresh).toHaveBeenCalledTimes(1);
});
it("translates custom-location consent without granting access", async () => {
  const imports = controller({ candidate_kind: "environment_override" });
  render(
    <LanguageProvider>
      <OnboardingFlow state={state} imports={imports} onFinish={vi.fn()} />
    </LanguageProvider>,
  );
  await waitFor(() =>
    expect(
      screen.getByLabelText("Importar el historial de Cursor"),
    ).toHaveProperty("disabled", false),
  );
  fireEvent.click(screen.getByLabelText("Importar el historial de Cursor"));
  expect(
    screen.getByRole("dialog", { name: "Confirmar ubicación personalizada" }),
  ).toBeTruthy();
  expect(
    screen.getByLabelText(
      "Confirmo que esta es la ubicación del historial de Cursor",
    ),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
  expect(imports.connectForOnboarding).not.toHaveBeenCalled();
});
it("translates import review and preserves independent permission disclosure", async () => {
  const change = vi.fn();
  render(
    <LanguageProvider>
      <FirstImportReview
        sources={[{ ...source, authorized: true }]}
        sourceIds={[source.source_id]}
        jobs={[]}
        onContinue={change}
      />
    </LanguageProvider>,
  );
  expect(
    await screen.findByRole("heading", {
      name: "Tus conversaciones llegan; tus memorias empiezan aquí",
    }),
  ).toBeTruthy();
  expect(
    screen.getByText(
      "Importar no concede automáticamente acceso de lectura a las aplicaciones. Las memorias comenzarán a generarse cuando configures un modelo.",
    ),
  ).toBeTruthy();
  expect(
    screen.getByRole("region", {
      name: "Progreso de importación de las aplicaciones",
    }),
  ).toBeTruthy();
  expect(change).not.toHaveBeenCalled();
});
it("uses compact Spanish actions and preserves Chinese copy and permissions", async () => {
  const next = vi.fn();
  const draft = vi.fn();
  render(
    <LanguageProvider>
      <SwitchLanguage />
      <FirstImportReview
        sources={[{ ...source, authorized: true }]}
        sourceIds={[source.source_id]}
        jobs={[]}
        connectionDraft={{ hostSourceIds: [] }}
        onConnectionDraftChange={draft}
        onContinue={next}
      />
    </LanguageProvider>,
  );
  const navigation = screen.getByRole("navigation");
  expect(navigation.textContent).toContain("Conectar apps");
  expect(
    screen.getByRole("button", {
      name: "Configurar un modelo para crear memorias",
    }),
  ).toBeTruthy();
  expect(screen.getByText(/Importar no concede automáticamente/)).toBeTruthy();
  expect(
    screen.getByText(/Solo para las apps seleccionadas y compatibles/),
  ).toBeTruthy();
  expect(screen.getByText(/Si una falla, las demás siguen/)).toBeTruthy();
  fireEvent.click(screen.getByText("中文"));
  expect(
    screen.getByRole("button", { name: "配置模型，沉淀记忆" }),
  ).toBeTruthy();
  expect(navigation.textContent).toContain("连接应用");
  expect(screen.getByText(/不会自动开放应用读取权限/)).toBeTruthy();
  expect(next).not.toHaveBeenCalled();
  expect(draft).not.toHaveBeenCalled();
});
it.each([
  ["pending", "Importando"],
  ["running", "Importando"],
  ["paused", "En pausa"],
  ["interrupted", "Interrumpida"],
  ["failed", "Problema de importación"],
  ["cancelled", "Cancelada"],
  ["completed", "Importación completada"],
] as const)(
  "translates %s progress receipts without changing counts",
  async (status, label) => {
    const job = {
      job_id: "fixture-job",
      source_id: "cursor",
      status,
      stored: 17,
      errors: 2,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:01:00Z",
    } as ImportJob;
    render(
      <LanguageProvider>
        <R6ImportProgress
          sourceIds={["cursor"]}
          sources={[{ ...source, authorized: true }]}
          jobs={[job]}
        />
      </LanguageProvider>,
    );
    expect(
      await screen.findByRole("article", {
        name: "Progreso de importación de Cursor",
      }),
    ).toBeTruthy();
    expect(screen.getByText("Mensajes guardados: 17")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe(
      `${label} · Errores de lectura: 2`,
    );
  },
);
it.each(["es", "zh-CN"] as const)(
  "preserves three multiline progress cards and receipt statuses in %s",
  (locale) => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, locale);
    const sources = ["cursor", "claude-code", "codex"].map((id, i) => ({
      ...source,
      source_id: id,
      display_name: `${locale === "es" ? "Aplicación con nombre largo" : "名称较长的应用"} ${i + 1}`,
      authorized: true,
    }));
    const jobs = sources.map((item, i) => ({
      job_id: `fixture-${i}`,
      source_id: item.source_id,
      status: ["completed", "failed", "paused"][i],
      stored: 12345,
      errors: i,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:01:00Z",
    })) as ImportJob[];
    render(
      <LanguageProvider>
        <R6ImportProgress
          sourceIds={sources.map((item) => item.source_id)}
          sources={sources}
          jobs={jobs}
        />
      </LanguageProvider>,
    );
    expect(screen.getAllByRole("article")).toHaveLength(3);
    const statuses = screen
      .getAllByRole("status")
      .map((item) => item.textContent);
    expect(statuses).toEqual(
      locale === "es"
        ? [
            "Importación completada",
            "Problema de importación · Errores de lectura: 1",
            "En pausa · Errores de lectura: 2",
          ]
        : ["导入完成", "导入遇到问题 · 1 条读取异常", "已暂停 · 2 条读取异常"],
    );
  },
);
it("translates completion recovery live without repeating completion writes", async () => {
  vi.mocked(completeOnboarding).mockRejectedValueOnce(
    new Error("fixture-offline"),
  );
  const imports = controller();
  const finish = vi.fn();
  render(
    <LanguageProvider>
      <SwitchLanguage />
      <OnboardingFlow state={state} imports={imports} onFinish={finish} />
    </LanguageProvider>,
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Entrar y añadir más tarde" }),
    ).toHaveProperty("disabled", false),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Entrar y añadir más tarde" }),
  );
  expect(
    await screen.findByRole("button", { name: "Reintentar guardar y entrar" }),
  ).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain(
    "aún no está confirmada",
  );
  fireEvent.click(screen.getByText("中文"));
  expect(
    await screen.findByRole("button", { name: "重试保存并进入" }),
  ).toBeTruthy();
  expect(completeOnboarding).toHaveBeenCalledTimes(1);
  expect(finish).not.toHaveBeenCalled();
});
it("translates model configuration, validation and advanced fields without activating permissions", async () => {
  render(
    <LanguageProvider>
      <SwitchLanguage />
      <OnboardingModelController sourceNames={["Cursor"]} onBack={vi.fn()} />
    </LanguageProvider>,
  );
  expect(
    await screen.findByRole("heading", {
      name: "Elige un modelo para crear memorias",
    }),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Probar conexión" }));
  expect(await screen.findByText("Introduce una API Key.")).toBeTruthy();
  expect(screen.getByLabelText("Tiempo de espera (segundos)")).toBeTruthy();
  expect(models.create).not.toHaveBeenCalled();
  expect(models.activate).not.toHaveBeenCalled();
  await act(async () => fireEvent.click(screen.getByText("中文")));
  expect(screen.getByText("请填写 API Key。")).toBeTruthy();
  expect(screen.getByLabelText("超时（秒）")).toBeTruthy();
});

it.each([
  [
    "completed",
    null,
    "Mensajes examinados: 5; no hay conversaciones para guardar",
    "已扫描 5 条，本次没有可保存的对话",
  ],
  [
    "completed",
    "2026-01-01T00:00:00Z",
    "Mensajes examinados: 5; no hay conversaciones dentro del periodo elegido",
    "已扫描 5 条，本次没有符合时间范围的对话",
  ],
  [
    "running",
    "2026-01-01T00:00:00Z",
    "Mensajes examinados: 5; filtrando los últimos 3 días",
    "已扫描 5 条，正在筛选最近 3 天",
  ],
  [
    "running",
    null,
    "Mensajes examinados: 5; filtrando",
    "已扫描 5 条，正在筛选",
  ],
  [
    "paused",
    null,
    "Mensajes examinados: 5; aún sin guardar",
    "已扫描 5 条，尚未保存",
  ],
] as const)(
  "words a %s scan with history_after=%s in both languages",
  async (status, historyAfter, spanish, chinese) => {
    const job = {
      job_id: "fixture-job",
      source_id: "cursor",
      status,
      stored: 0,
      discovered: 5,
      errors: 0,
      history_after: historyAfter,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:01:00Z",
    } as unknown as ImportJob;
    for (const [locale, expected] of [
      ["es", spanish],
      ["zh-CN", chinese],
    ] as const) {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, locale);
      const view = render(
        <LanguageProvider>
          <R6ImportProgress
            sourceIds={["cursor"]}
            sources={[{ ...source, authorized: true }]}
            jobs={[job]}
          />
        </LanguageProvider>,
      );
      expect(await screen.findByText(expected)).toBeTruthy();
      view.unmount();
    }
  },
);
