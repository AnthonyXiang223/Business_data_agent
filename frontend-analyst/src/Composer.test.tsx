import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Composer from "./Composer";

afterEach(() => {
  cleanup();
});

describe("Composer", () => {
  it("submits trimmed text and clears the input", () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} onStop={vi.fn()} running={false} disabled={false} />);

    fireEvent.change(screen.getByLabelText("分析问题"), { target: { value: "  分析 GMV  " } });
    fireEvent.click(screen.getByText("发送"));

    expect(onSubmit).toHaveBeenCalledWith("分析 GMV");
    expect(screen.getByLabelText<HTMLInputElement>("分析问题").value).toBe("");
  });

  it("does not submit empty text", () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} onStop={vi.fn()} running={false} disabled={false} />);

    fireEvent.click(screen.getByText("发送"));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("blocks submit while disabled", () => {
    const onSubmit = vi.fn();
    render(<Composer onSubmit={onSubmit} onStop={vi.fn()} running={false} disabled={true} />);

    fireEvent.change(screen.getByLabelText("分析问题"), { target: { value: "画图" } });
    fireEvent.click(screen.getByText("发送"));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("turns the send button into a stop button while running", () => {
    const onSubmit = vi.fn();
    const onStop = vi.fn();
    render(<Composer onSubmit={onSubmit} onStop={onStop} running={true} disabled={false} />);

    expect(screen.queryByText("发送")).toBeNull();
    fireEvent.click(screen.getByText("停止"));
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("disables the input while running", () => {
    render(<Composer onSubmit={vi.fn()} onStop={vi.fn()} running={true} disabled={false} />);
    expect(screen.getByLabelText<HTMLInputElement>("分析问题").disabled).toBe(true);
  });
});
