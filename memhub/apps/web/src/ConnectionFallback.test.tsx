// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectionFallback } from "./ConnectionFallback";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";

it("switches connection prose live, preserves technical bytes and copies only on request", async () => {
  const user = userEvent.setup();
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  const mcp =
    '{"mcpServers":{"one-memory":{"command":"/synthetic/bin/recall"}}}';
  const shell = "'/synthetic/bin/recall' '查询内容'\n";
  const getConnectionMaterial = vi
    .fn()
    .mockResolvedValue({ mcp, shell, prompt: "synthetic legacy prompt" });
  const copyText = vi.fn().mockResolvedValue(undefined);
  const onLanguagePreferenceChanged = vi.fn().mockReturnValue(() => {});
  window.oneMemory = {
    getConnectionMaterial,
    copyText,
    getLanguagePreference: vi
      .fn()
      .mockResolvedValue({ preference: "es", locale: "es", revision: 1 }),
    onLanguagePreferenceChanged,
  } as unknown as Window["oneMemory"];
  render(
    <LanguageProvider>
      <ConnectionFallback />
    </LanguageProvider>,
  );
  await user.click(await screen.findByText("Conectar otras herramientas"));
  const input = await screen.findByLabelText("Material de conexión local");
  expect((input as HTMLTextAreaElement).value).toContain(
    "No crees un proxy público",
  );
  expect((input as HTMLTextAreaElement).value).toContain(mcp);
  expect((input as HTMLTextAreaElement).value).toContain(shell);
  expect(copyText).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "Copiar instrucciones" }),
  );
  expect(copyText).toHaveBeenCalledWith((input as HTMLTextAreaElement).value);
  await user.click(screen.getByRole("tab", { name: "Configuración MCP" }));
  expect(input).toHaveProperty("value", mcp);
  const listener = onLanguagePreferenceChanged.mock.calls[0][0];
  listener({ preference: "zh-CN", locale: "zh-CN", revision: 2 });
  expect(await screen.findByRole("tab", { name: "MCP 配置" })).toBeTruthy();
  expect(input).toHaveProperty("value", mcp);
  expect(getConnectionMaterial).toHaveBeenCalledTimes(1);
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  delete window.oneMemory;
  localStorage.clear();
});

const CHINESE_PROMPT = "synthetic 中文提示词 {mcp} {shell}\n";

/** Spanish behavioural invariants every variant of the prompt must keep. */
const SPANISH_INVARIANTS = [
  // authorization
  "Ver una herramienta no equivale a tener autorización.",
  "no reintentes ni amplíes permisos",
  "No cambies de herramienta, transporte, configuración o alcance para eludir una denegación.",
  // failures
  "Si MemHub está desconectado o falla, indica que no está disponible",
  "sin afirmar éxito ni inventar citas",
  // partial
  "si la cobertura es parcial, dilo y conserva la limitación",
  "No presentes una cobertura parcial sin resultados como recuperación exitosa.",
  // empty
  "Si ambas rutas están vacías, indica únicamente «He completado la consulta de memorias de MemHub»",
  "Un resultado vacío no demuestra que se haya buscado en todo el historial.",
  // attribution
  "Cita solo fuentes devueltas y realmente usadas, atribuyendo fuente, autor, fecha e identificadores",
  "Recuperar resultados no demuestra que la respuesta los haya utilizado.",
  // Chinese action notice stays verbatim inside the Spanish prompt
  "En chino, usa «正在使用 MemHub 查询记忆»",
];

function mountSpanish(material: {
  mcp: string;
  shell: string | null;
  prompt: string;
}) {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  const getConnectionMaterial = vi.fn().mockResolvedValue(material);
  const copyText = vi.fn().mockResolvedValue(undefined);
  const onLanguagePreferenceChanged = vi.fn().mockReturnValue(() => {});
  window.oneMemory = {
    getConnectionMaterial,
    copyText,
    getLanguagePreference: vi
      .fn()
      .mockResolvedValue({ preference: "es", locale: "es", revision: 1 }),
    onLanguagePreferenceChanged,
  } as unknown as Window["oneMemory"];
  render(
    <LanguageProvider>
      <ConnectionFallback />
    </LanguageProvider>,
  );
  return { getConnectionMaterial, copyText, onLanguagePreferenceChanged };
}

describe("Spanish connection prompt", () => {
  // Synthetic snippets with braces, placeholders, replacement patterns,
  // quotes, backticks and line breaks: all must pass through untouched.
  const mcp =
    '{"mcpServers":{"one-memory":{"command":"/synthetic/{shell}/bin","args":["$&","$1","{mcp}"]}}}\n';
  const shell =
    "'/synthetic/bin/recall' --cwd \"/tmp/a b\" '{mcp} 查询内容' `x`\nsecond line {shellBlock}\n";

  it("embeds each snippet exactly once and keeps every invariant", async () => {
    const user = userEvent.setup();
    const { copyText } = mountSpanish({ mcp, shell, prompt: CHINESE_PROMPT });
    await user.click(await screen.findByText("Conectar otras herramientas"));
    const input = (await screen.findByLabelText(
      "Material de conexión local",
    )) as HTMLTextAreaElement;
    const value = input.value;
    expect(value.split(mcp)).toHaveLength(2);
    expect(value.split(shell)).toHaveLength(2);
    expect(value).toContain(`\`\`\`json\n${mcp}\`\`\``);
    expect(value).toContain(`\`\`\`bash\n${shell}\`\`\``);
    expect(value).toContain("Si solo admites shell local");
    expect(value).not.toContain(CHINESE_PROMPT);
    for (const line of SPANISH_INVARIANTS) expect(value).toContain(line);
    await user.click(
      screen.getByRole("button", { name: "Copiar instrucciones" }),
    );
    expect(copyText).toHaveBeenCalledExactlyOnceWith(value);
  });

  it("omits the shell section without shell and keeps every invariant", async () => {
    const user = userEvent.setup();
    const { copyText } = mountSpanish({
      mcp,
      shell: null,
      prompt: CHINESE_PROMPT,
    });
    await user.click(await screen.findByText("Conectar otras herramientas"));
    const input = (await screen.findByLabelText(
      "Material de conexión local",
    )) as HTMLTextAreaElement;
    const value = input.value;
    expect(value.split(mcp)).toHaveLength(2);
    expect(value).not.toContain("```bash");
    expect(value).not.toContain("Si solo admites shell local");
    expect(value).not.toContain("{shellBlock}");
    expect(value).toContain(
      `\`\`\`\n\nTras conectar, realiza una consulta real`,
    );
    for (const line of SPANISH_INVARIANTS) expect(value).toContain(line);
    expect(screen.getByRole("tab", { name: "Comando" })).toHaveProperty(
      "disabled",
      true,
    );
    await user.click(
      screen.getByRole("button", { name: "Copiar instrucciones" }),
    );
    expect(copyText).toHaveBeenCalledExactlyOnceWith(value);
  });

  it("returns to the bridge's Chinese prompt byte for byte without reloading", async () => {
    const user = userEvent.setup();
    const { copyText, getConnectionMaterial, onLanguagePreferenceChanged } =
      mountSpanish({ mcp, shell, prompt: CHINESE_PROMPT });
    await user.click(await screen.findByText("Conectar otras herramientas"));
    const input = (await screen.findByLabelText(
      "Material de conexión local",
    )) as HTMLTextAreaElement;
    expect(input.value).toContain("Tras conectar");
    const listener = onLanguagePreferenceChanged.mock.calls[0][0];
    act(() => listener({ preference: "zh-CN", locale: "zh-CN", revision: 2 }));
    await screen.findByRole("button", { name: "复制提示词" });
    expect(input.value).toBe(CHINESE_PROMPT);
    await user.click(screen.getByRole("button", { name: "复制提示词" }));
    expect(copyText).toHaveBeenCalledExactlyOnceWith(CHINESE_PROMPT);
    await user.click(screen.getByRole("tab", { name: "命令" }));
    expect(input.value).toBe(shell);
    expect(getConnectionMaterial).toHaveBeenCalledTimes(1);
  });
});
describe("ConnectionFallback", () => {
  it("names an unknown Harness before generating and copying its pairing Skill", async () => {
    const user = userEvent.setup();
    const createCustomHarnessSkill = vi.fn().mockResolvedValue({
      displayName: "Kimi Code",
      expiresAt: "2026-10-19T12:10:00.000Z",
      skill: "pair with 123456",
    });
    const copyText = vi.fn().mockResolvedValue(undefined);
    window.oneMemory = {
      createCustomHarnessSkill,
      copyText,
    } as unknown as Window["oneMemory"];
    render(<ConnectionFallback />);
    await user.click(screen.getByText("连接其他工具"));
    const generate = screen.getByRole("button", { name: "生成配对 Skill" });
    expect(generate).toHaveProperty("disabled", true);
    await user.type(screen.getByLabelText("Harness 名称"), "Kimi Code");
    await user.click(generate);
    expect(createCustomHarnessSkill).toHaveBeenCalledWith("Kimi Code");
    expect(await screen.findByLabelText("配对 Skill")).toHaveProperty(
      "value",
      "pair with 123456",
    );
    await user.click(screen.getByRole("button", { name: "复制 Skill" }));
    expect(copyText).toHaveBeenCalledWith("pair with 123456");
    expect(screen.getByRole("status").textContent).toContain(
      "粘贴到对应 Harness",
    );
  });
  it("offers retry and clipboard failure recovery without fake pairing success", async () => {
    const user = userEvent.setup();
    const createCustomHarnessSkill = vi
      .fn()
      .mockRejectedValueOnce(new Error())
      .mockResolvedValue({
        displayName: "Kimi Code",
        expiresAt: "2026-10-19T12:10:00.000Z",
        skill: "pair once",
      });
    window.oneMemory = {
      createCustomHarnessSkill,
      copyText: vi.fn().mockRejectedValue(new Error()),
    } as unknown as Window["oneMemory"];
    render(<ConnectionFallback />);
    await user.click(screen.getByText("连接其他工具"));
    await user.type(screen.getByLabelText("Harness 名称"), "Kimi Code");
    await user.click(screen.getByRole("button", { name: "生成配对 Skill" }));
    await user.click(await screen.findByRole("button", { name: "重试" }));
    await screen.findByLabelText("配对 Skill");
    await user.click(screen.getByRole("button", { name: "复制 Skill" }));
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain("复制失败"),
    );
  });
  it("does not invent runtime paths in the browser", () => {
    render(<ConnectionFallback />);
    expect(screen.queryByLabelText("配对 Skill")).toBeNull();
    expect(screen.getByText("本机配对仅在桌面 App 中可用。")).toBeTruthy();
  });
});
