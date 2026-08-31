import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const CHART_JSON =
  '{"title":"品类GMV","chart_type":"bar","data":{"categories":["数码","服饰"],"series":[{"name":"GMV","values":[120.5,89]}]}}';

const streamState: {
  messages: Array<Record<string, unknown>>;
  isLoading: boolean;
  error: null;
  submit: ReturnType<typeof vi.fn>;
} = {
  messages: [
    { id: "human-1", type: "human", content: "分析 GMV 并画柱状图" },
    {
      id: "ai-1",
      type: "ai",
      content: "",
      tool_calls: [{ id: "call-1", name: "get_data_profile", args: {} }],
    },
    { id: "tool-1", type: "tool", tool_call_id: "call-1", name: "get_data_profile", content: "画像..." },
    {
      id: "ai-2",
      type: "ai",
      content: "",
      tool_calls: [{ id: "call-2", name: "create_chart", args: {} }],
    },
    { id: "tool-2", type: "tool", tool_call_id: "call-2", name: "create_chart", content: CHART_JSON },
    { id: "ai-3", type: "ai", content: "图表已生成，电子产品 GMV 最高。" },
  ],
  isLoading: false,
  error: null,
  submit: vi.fn(),
};

let capturedStreamOptions: Record<string, unknown> | null = null;

vi.mock("@langchain/react", () => ({
  useStream: (options: Record<string, unknown>) => {
    capturedStreamOptions = options;
    return streamState;
  },
}));

afterEach(() => {
  cleanup();
  capturedStreamOptions = null;
  window.history.replaceState({}, "", "http://localhost:3000/");
  streamState.isLoading = false;
  streamState.error = null;
  streamState.messages = [
    { id: "human-1", type: "human", content: "分析 GMV 并画柱状图" },
    {
      id: "ai-1",
      type: "ai",
      content: "",
      tool_calls: [{ id: "call-1", name: "get_data_profile", args: {} }],
    },
    { id: "tool-1", type: "tool", tool_call_id: "call-1", name: "get_data_profile", content: "画像..." },
    {
      id: "ai-2",
      type: "ai",
      content: "",
      tool_calls: [{ id: "call-2", name: "create_chart", args: {} }],
    },
    { id: "tool-2", type: "tool", tool_call_id: "call-2", name: "create_chart", content: CHART_JSON },
    { id: "ai-3", type: "ai", content: "图表已生成，电子产品 GMV 最高。" },
  ];
});

describe("App", () => {
  it("targets the analyst assistant", () => {
    render(<App />);
    expect(capturedStreamOptions?.assistantId).toBe("analyst");
  });

  it("renders the question, final answer and the figure — tool traffic invisible", () => {
    render(<App />);

    expect(screen.getByText("分析 GMV 并画柱状图")).toBeTruthy();
    expect(screen.getByText("图表已生成，电子产品 GMV 最高。")).toBeTruthy();
    // Figure 1 同时出现在看板索引与图卡标签
    expect(screen.getAllByText("Figure 1").length).toBeGreaterThan(0);
    // 工具过程痕迹不得出现
    expect(screen.queryByText(/get_data_profile/)).toBeNull();
    expect(screen.queryByText(/Tool:/)).toBeNull();
    expect(screen.queryByText("画像...")).toBeNull();
  });

  it("lists the figure in the dashboard index", () => {
    render(<App />);

    const nav = screen.getByLabelText("图表看板");
    expect(nav.textContent).toContain("Figure 1");
    expect(nav.textContent).toContain("品类GMV");
  });

  it("stores the thread id without touching the URL", () => {
    render(<App />);

    act(() => {
      (capturedStreamOptions?.onThreadId as ((id: string) => void) | undefined)?.("thread-1");
    });

    expect(window.location.search).toBe("");
  });

  it("submits the typed question", () => {
    render(<App />);

    fireEvent.change(screen.getByLabelText("分析问题"), { target: { value: "画一张饼图" } });
    fireEvent.click(screen.getByText("发送"));

    expect(streamState.submit).toHaveBeenCalledWith({
      messages: [{ type: "human", content: "画一张饼图" }],
    });
  });

  it("shows the loading indicator while in progress", () => {
    streamState.isLoading = true;
    render(<App />);
    expect(screen.getByText("分析中")).toBeTruthy();
  });
});
