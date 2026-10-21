// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LanguageSelector } from "./LanguageSelector";
import { getUiLanguage, setUiLanguage, startUiLanguage } from "./ui-language";

const originalBridge = window.oneMemory;
afterEach(async () => {
  window.oneMemory = undefined;
  await setUiLanguage("zh-CN");
  window.localStorage.clear();
  window.oneMemory = originalBridge;
});

describe("renderer language preference", () => {
  it("switches in standalone Web and restores the selected language on startup", async () => {
    window.oneMemory = undefined;
    window.localStorage.setItem("memhub.ui-language", "en");
    const stop = startUiLanguage();
    expect(getUiLanguage()).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    const { unmount } = render(<LanguageSelector />);
    expect(
      (screen.getByRole("combobox", { name: "Language" }) as HTMLSelectElement)
        .value,
    ).toBe("en");
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "zh-CN" },
    });
    await waitFor(() =>
      expect(
        (screen.getByRole("combobox", { name: "语言" }) as HTMLSelectElement)
          .value,
      ).toBe("zh-CN"),
    );
    expect(window.localStorage.getItem("memhub.ui-language")).toBe("zh-CN");
    unmount();
    stop();
  });

  it("updates other Web tabs and ignores invalid stored values", async () => {
    window.oneMemory = undefined;
    const stop = startUiLanguage();
    window.localStorage.setItem("memhub.ui-language", "en");
    window.dispatchEvent(
      new StorageEvent("storage", { key: "memhub.ui-language" }),
    );
    expect(getUiLanguage()).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    window.localStorage.setItem("memhub.ui-language", "invalid");
    window.dispatchEvent(
      new StorageEvent("storage", { key: "memhub.ui-language" }),
    );
    expect(getUiLanguage()).toBe("zh-CN");
    stop();
  });

  it("keeps the previous language when desktop persistence fails", async () => {
    window.oneMemory = {
      setUiLanguage: vi.fn().mockRejectedValue(new Error("disk unavailable")),
    } as unknown as NonNullable<typeof window.oneMemory>;
    const { unmount } = render(<LanguageSelector />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "en" } });
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "语言未能保存，请重试。",
    );
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe(
      "zh-CN",
    );
    expect(window.localStorage.getItem("memhub.ui-language")).toBeNull();
    unmount();
  });

  it("accepts only validated desktop IPC updates and does not use localStorage", async () => {
    const setNative = vi.fn(async () => "en" as const);
    let notify: ((value: "en" | "zh-CN") => void) | undefined;
    const unsubscribe = vi.fn();
    window.oneMemory = {
      getUiLanguage: vi.fn(async () => "zh-CN"),
      setUiLanguage: setNative,
      onUiLanguageChanged: (listener: (value: "en" | "zh-CN") => void) => {
        notify = listener;
        return unsubscribe;
      },
    } as unknown as NonNullable<typeof window.oneMemory>;
    const stop = startUiLanguage();
    await setUiLanguage("en");
    expect(getUiLanguage()).toBe("en");
    expect(setNative).toHaveBeenCalledWith("en");
    expect(window.localStorage.getItem("memhub.ui-language")).toBeNull();
    notify?.("zh-CN");
    expect(getUiLanguage()).toBe("zh-CN");
    await expect(setUiLanguage("../../path" as never)).rejects.toThrow(
      "invalid_ui_language",
    );
    expect(setNative).toHaveBeenCalledOnce();
    stop();
    expect(unsubscribe).toHaveBeenCalledOnce();
  });
});
