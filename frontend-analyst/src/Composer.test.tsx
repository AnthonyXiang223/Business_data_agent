import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Composer from "./Composer";

afterEach(() => {
  cleanup();
});

describe("Composer", () => {
  it("submits trimmed text and clears the input", () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} disabled={false} />);

    fireEvent.change(screen.getByLabelText("分析问题"), { target: { value: "  分析 GMV  " } });
    fireEvent.click(screen.getByText("发送"));

    expect(onSubmit).toHaveBeenCalledWith("分析 GMV");
    expect(screen.getByLabelText<HTMLInputElement>("分析问题").value).toBe("");
  });

  it("does not submit empty text", () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} disabled={false} />);

    fireEvent.click(screen.getByText("发送"));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("blocks submit while disabled", () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} disabled={true} />);

    fireEvent.change(screen.getByLabelText("分析问题"), { target: { value: "画图" } });
    fireEvent.click(screen.getByText("发送"));
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
