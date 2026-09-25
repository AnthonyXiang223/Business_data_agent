import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SessionSidebar from "./SessionSidebar";

afterEach(() => {
  cleanup();
});

const NOW_ISO = new Date().toISOString();
const SESSIONS = [
  { thread_id: "t1", title: "GMV 分析", message_count: 3, last_updated: NOW_ISO },
  { thread_id: "t2", title: "客单价分布", message_count: 1, last_updated: NOW_ISO },
];

function renderSidebar(overrides: Partial<Parameters<typeof SessionSidebar>[0]> = {}) {
  const props = {
    sessions: SESSIONS,
    loading: false,
    error: null,
    currentThreadId: "t1",
    onSelect: vi.fn(),
    onNew: vi.fn(),
    onDelete: vi.fn(),
    ...overrides,
  };
  render(<SessionSidebar {...props} />);
  return props;
}

describe("SessionSidebar", () => {
  it("shows a skeleton while the first load is in flight", () => {
    const { container } = render(
      <SessionSidebar
        sessions={[]}
        loading={true}
        error={null}
        currentThreadId={undefined}
        onSelect={vi.fn()}
        onNew={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(container.querySelector(".sessions__skeleton")).toBeTruthy();
  });

  it("shows empty and error states", () => {
    const { unmount } = render(
      <SessionSidebar
        sessions={[]}
        loading={false}
        error={null}
        currentThreadId={undefined}
        onSelect={vi.fn()}
        onNew={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.getByText("暂无会话")).toBeTruthy();
    unmount();

    render(
      <SessionSidebar
        sessions={[]}
        loading={false}
        error="连接失败"
        currentThreadId={undefined}
        onSelect={vi.fn()}
        onNew={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.getByText("连接失败")).toBeTruthy();
  });

  it("renders titles with message counts and marks the active session", () => {
    renderSidebar();

    expect(screen.getByText("GMV 分析")).toBeTruthy();
    expect(screen.getByText("客单价分布")).toBeTruthy();
    expect(screen.getByText(/3 条/)).toBeTruthy();
    const active = screen.getByText("GMV 分析").closest("button");
    expect(active?.className).toContain("sessions__item--active");
  });

  it("selects a session on click", () => {
    const props = renderSidebar();
    fireEvent.click(screen.getByText("客单价分布"));
    expect(props.onSelect).toHaveBeenCalledWith("t2");
  });

  it("creates a session from the new button", () => {
    const props = renderSidebar();
    fireEvent.click(screen.getByLabelText("新建会话"));
    expect(props.onNew).toHaveBeenCalledTimes(1);
  });

  it("deletes with a two-step confirm", () => {
    const props = renderSidebar();

    fireEvent.click(screen.getByLabelText("删除会话 GMV 分析"));
    expect(screen.getByText("确认删除？")).toBeTruthy();

    fireEvent.click(screen.getByText("取消"));
    expect(screen.queryByText("确认删除？")).toBeNull();

    fireEvent.click(screen.getByLabelText("删除会话 GMV 分析"));
    fireEvent.click(screen.getByText("删除"));
    expect(props.onDelete).toHaveBeenCalledWith("t1");
  });
});
