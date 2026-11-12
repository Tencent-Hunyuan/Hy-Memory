// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  R6Button,
  R6Field,
  R6LocalStorage,
  R6Stepper,
  R6Switch,
} from "./R6Controls";

afterEach(cleanup);

describe("R6 shared controls", () => {
  it("does not submit forms unless explicitly requested", () => {
    render(<R6Button>返回</R6Button>);
    expect(screen.getByRole("button").getAttribute("type")).toBe("button");
  });

  it("keeps its label and prevents duplicate activation while pending", () => {
    const onClick = vi.fn();
    const view = render(
      <R6Button pending={false} onClick={onClick}>
        保存修改
      </R6Button>,
    );
    const button = screen.getByRole("button", { name: "保存修改" });
    expect(button.getAttribute("data-pending-capable")).toBe("true");
    const reservedSpinner = button.querySelector(".r6-button-spinner");
    expect(reservedSpinner).toBeTruthy();
    expect(reservedSpinner?.getAttribute("data-visible")).toBeNull();
    view.rerender(
      <R6Button pending onClick={onClick}>
        保存修改
      </R6Button>,
    );
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect(button.querySelector(".r6-button-spinner")).toBe(reservedSpinner);
    expect(reservedSpinner?.getAttribute("data-visible")).toBe("true");
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
    expect(button.textContent).toContain("保存修改");
  });

  it("honors an explicit submit action", () => {
    render(<R6Button type="submit">提交</R6Button>);
    expect(screen.getByRole("button").getAttribute("type")).toBe("submit");
  });

  it("keeps switch state controlled by the authoritative parent", () => {
    const change = vi.fn();
    const view = render(
      <R6Switch label="持续同步" checked={false} onCheckedChange={change} />,
    );
    const control = screen.getByRole("switch", { name: "持续同步" });
    fireEvent.click(control);
    expect(change).toHaveBeenCalledWith(true);
    expect(control.getAttribute("aria-checked")).toBe("false");
    view.rerender(
      <R6Switch label="持续同步" checked pending onCheckedChange={change} />,
    );
    fireEvent.click(control);
    expect(change).toHaveBeenCalledTimes(2);
    expect(control.getAttribute("aria-checked")).toBe("true");
    expect(control.getAttribute("aria-busy")).toBe("true");
  });

  it("associates field errors without removing caller descriptions", () => {
    render(
      <R6Field
        label="API Key"
        name="api-key"
        type="password"
        error="请输入密钥"
        aria-describedby="provider-note"
      />,
    );
    const field = screen.getByLabelText("API Key");
    expect(field.getAttribute("aria-invalid")).toBe("true");
    const ids = field.getAttribute("aria-describedby")?.split(" ") ?? [];
    expect(ids).toContain("provider-note");
    expect(
      ids.some(
        (id) => document.getElementById(id)?.textContent === "请输入密钥",
      ),
    ).toBe(true);
    expect(field.getAttribute("type")).toBe("password");
  });

  it("marks exactly one active step", () => {
    render(<R6Stepper active={2} />);
    expect(document.querySelectorAll('[aria-current="step"]')).toHaveLength(1);
    expect(
      document.querySelector('[aria-current="step"]')?.textContent,
    ).toContain("连接应用");
    expect(screen.getByRole("list").children).toHaveLength(3);
  });

  it("describes storage without claiming network or service health", () => {
    render(<R6LocalStorage />);
    expect(screen.getByText("本地存储")).toBeTruthy();
    expect(screen.queryByText("后台运行中")).toBeNull();
  });
});
