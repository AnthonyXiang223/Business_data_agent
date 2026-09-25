import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import MessageFlow from "./MessageFlow";
import type { Row } from "./lib/rows";

afterEach(() => {
  cleanup();
});

const CHART_SPEC = {
  title: "品类GMV",
  chart_type: "bar" as const,
  data: {
    categories: ["数码", "服饰"],
    series: [{ name: "GMV", values: [120.5, 89] }],
  },
};

const ROWS: Row[] = [
  { kind: "prose", key: "h1", role: "human", body: "分析各品类 GMV" },
  { kind: "figure", key: "fig-1", number: 1, spec: CHART_SPEC },
  { kind: "prose", key: "a1", role: "ai", body: "**结论**：电子产品 GMV 最高。" },
];

describe("MessageFlow", () => {
  it("renders prose and figure rows in order", () => {
    render(<MessageFlow rows={ROWS} loading={false} error={null} />);

    expect(screen.getByText("分析各品类 GMV")).toBeTruthy();
    expect(screen.getByText("Figure 1")).toBeTruthy();
    // markdown 加粗会拆开文本节点，用 strong 定位再校验整段内容
    const strong = screen.getByText("结论");
    expect(strong.closest("p")?.textContent).toBe("结论：电子产品 GMV 最高。");
    expect(strong.closest("article")?.querySelector("strong")).toBeTruthy();
  });

  it("does not render markdown images", () => {
    render(
      <MessageFlow
        rows={[{ kind: "prose", key: "a1", role: "ai", body: "![x](http://fake.example/a.png) 图表如下" }]}
        loading={false}
        error={null}
      />,
    );

    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText(/图表如下/)).toBeTruthy();
  });

  it("renders run meta and the stopped badge on an ai row", () => {
    render(
      <MessageFlow
        rows={[
          {
            kind: "prose",
            key: "a1",
            role: "ai",
            body: "部分结论",
            meta: { toolCalls: 3, tokens: 1234, durationMs: 42000 },
            stopped: true,
          },
        ]}
        loading={false}
        error={null}
      />,
    );

    expect(screen.getByText("已停止")).toBeTruthy();
    expect(screen.getByText(/工具调用 3 次/)).toBeTruthy();
    expect(screen.getByText(/1,234 tokens/)).toBeTruthy();
    expect(screen.getByText(/42 秒/)).toBeTruthy();
  });

  it("renders a minimal stopped row even without body text", () => {
    render(
      <MessageFlow
        rows={[{ kind: "prose", key: "a1", role: "ai", body: "", stopped: true }]}
        loading={false}
        error={null}
      />,
    );

    expect(screen.getByText("已停止")).toBeTruthy();
  });

  it("shows loading and error states", () => {
    const { rerender } = render(<MessageFlow rows={[]} loading={true} error={null} />);
    expect(screen.getByText("分析中")).toBeTruthy();

    rerender(<MessageFlow rows={[]} loading={false} error="连接失败" />);
    expect(screen.getByText("连接失败")).toBeTruthy();
  });
});
