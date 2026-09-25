import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Header from "./Header";

afterEach(() => {
  cleanup();
});

describe("Header", () => {
  it("renders brand and session title", () => {
    render(
      <Header
        title="GMV 分析"
        running={false}
        sessionsOpen={false}
        onToggleSessions={vi.fn()}
        figuresOpen={false}
        onToggleFigures={vi.fn()}
      />,
    );

    expect(screen.getByText("数据分析 · ANALYST")).toBeTruthy();
    expect(screen.getByText("GMV 分析")).toBeTruthy();
    expect(screen.queryByText("运行中")).toBeNull();
  });

  it("shows the running status while a run is in progress", () => {
    render(
      <Header
        title={null}
        running={true}
        sessionsOpen={false}
        onToggleSessions={vi.fn()}
        figuresOpen={false}
        onToggleFigures={vi.fn()}
      />,
    );
    expect(screen.getByText("运行中")).toBeTruthy();
  });

  it("fires the drawer toggles with aria-expanded state", () => {
    const onToggleSessions = vi.fn();
    const onToggleFigures = vi.fn();
    render(
      <Header
        title={null}
        running={false}
        sessionsOpen={true}
        onToggleSessions={onToggleSessions}
        figuresOpen={false}
        onToggleFigures={onToggleFigures}
      />,
    );

    const menu = screen.getByLabelText("打开会话列表");
    expect(menu.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(menu);
    expect(onToggleSessions).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByLabelText("打开图表看板"));
    expect(onToggleFigures).toHaveBeenCalledTimes(1);
  });
});
