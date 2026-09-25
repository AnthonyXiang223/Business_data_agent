import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const CHART_JSON =
  '{"title":"品类GMV","chart_type":"bar","data":{"categories":["数码","服饰"],"series":[{"name":"GMV","values":[120.5,89]}]}}';

function defaultMessages(): Array<Record<string, unknown>> {
  return [
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
}

let capturedApiUrl: string | null = null;
// 初始值必须有：第一个测试跑在 afterEach 之前，声明无初值 = undefined → 解构崩溃
let mockState: {
  messages: Array<Record<string, unknown>>;
  isLoading: boolean;
  error: string | null;
  threadId: string | undefined;
  submit: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
  restore: ReturnType<typeof vi.fn>;
} = {
  messages: defaultMessages(),
  isLoading: false,
  error: null,
  threadId: undefined,
  submit: vi.fn(),
  stop: vi.fn(),
  restore: vi.fn(),
};

vi.mock("./lib/chat", () => ({
  useChat: (apiUrl: string) => {
    capturedApiUrl = apiUrl;
    return mockState;
  },
}));

// App 编排层依赖 sessions 库（列表/历史/记忆），整库 mock 后用 hoisted 对象按测试注入
const sessionsMock = vi.hoisted(() => ({
  useSessions: vi.fn(),
  fetchHistory: vi.fn(),
  toMessages: vi.fn((h: unknown[]) => h),
  readLastThreadId: vi.fn<() => string | null>(() => null),
  writeLastThreadId: vi.fn(),
}));

vi.mock("./lib/sessions", () => sessionsMock);

beforeEach(() => {
  sessionsMock.useSessions.mockReturnValue({
    sessions: [],
    loading: false,
    error: null,
    refresh: vi.fn(),
    remove: vi.fn(),
  });
  sessionsMock.fetchHistory.mockResolvedValue([]);
  sessionsMock.toMessages.mockImplementation((h: unknown[]) => h);
  sessionsMock.readLastThreadId.mockReturnValue(null);
});

afterEach(() => {
  cleanup();
  capturedApiUrl = null;
  window.history.replaceState({}, "", "http://localhost:3000/");
  localStorage.clear();
  vi.useRealTimers(); // 防自动恢复重试测试的假时钟泄漏到后续用例
  vi.clearAllMocks();
  mockState = {
    messages: defaultMessages(),
    isLoading: false,
    error: null,
    threadId: undefined,
    submit: vi.fn(),
    stop: vi.fn(),
    restore: vi.fn(),
  };
});

const NOW_ISO = new Date().toISOString();

// 断言基准必须与 App 同款解析逻辑：Docker 构建时注入 VITE_ANALYST_API_URL=/api
// （nginx 同源反代），本地开发未注入时用默认 8000——测试不能硬编码任何一种
const EXPECTED_API_URL =
  import.meta.env.VITE_ANALYST_API_URL ?? "http://127.0.0.1:8000";

describe("App", () => {
  it("passes the resolved API URL through to the chat hook", () => {
    render(<App />);
    expect(capturedApiUrl).toBe(EXPECTED_API_URL);
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

  it("submits the typed question to the chat hook", () => {
    render(<App />);

    fireEvent.change(screen.getByLabelText("分析问题"), { target: { value: "画一张饼图" } });
    fireEvent.click(screen.getByText("发送"));

    expect(mockState.submit).toHaveBeenCalledWith("画一张饼图");
  });

  it("shows the loading indicator while in progress", () => {
    mockState.isLoading = true;
    render(<App />);
    expect(screen.getByText("分析中")).toBeTruthy();
    expect(screen.getByText("运行中")).toBeTruthy(); // 顶栏运行状态
  });

  it("shows the error banner when the stream fails", () => {
    mockState.error = "请求失败 HTTP 500";
    render(<App />);
    expect(screen.getByText("请求失败 HTTP 500")).toBeTruthy();
  });

  it("shows the welcome screen with capabilities and sample questions when empty", () => {
    mockState.messages = [];
    render(<App />);

    expect(screen.getByText("你好，我是业务数据分析师。")).toBeTruthy();
    expect(screen.getByText("数据探查")).toBeTruthy();
    expect(screen.getByText("会话记忆")).toBeTruthy();
    expect(screen.getByText("近 30 天 GMV 趋势如何？画一张折线图")).toBeTruthy();
  });

  it("submits a sample question when a welcome chip is clicked", () => {
    mockState.messages = [];
    render(<App />);

    fireEvent.click(screen.getByText("各品类 GMV 占比是多少？画一张饼图"));
    expect(mockState.submit).toHaveBeenCalledWith("各品类 GMV 占比是多少？画一张饼图");
  });

  it("renders sessions in the sidebar", () => {
    sessionsMock.useSessions.mockReturnValue({
      sessions: [
        { thread_id: "t1", title: "GMV 分析", message_count: 3, last_updated: NOW_ISO },
      ],
      loading: false,
      error: null,
      refresh: vi.fn(),
      remove: vi.fn(),
    });
    render(<App />);

    expect(screen.getByText("GMV 分析")).toBeTruthy();
    expect(screen.getByText(/3 条/)).toBeTruthy();
  });

  it("selecting a session restores its history", async () => {
    const history = [{ role: "user", type: "text", id: "m1", content: "历史问题" }];
    sessionsMock.fetchHistory.mockResolvedValue(history);
    sessionsMock.useSessions.mockReturnValue({
      sessions: [
        { thread_id: "t1", title: "GMV 分析", message_count: 3, last_updated: NOW_ISO },
      ],
      loading: false,
      error: null,
      refresh: vi.fn(),
      remove: vi.fn(),
    });
    render(<App />);

    fireEvent.click(screen.getByText("GMV 分析"));
    await waitFor(() => {
      expect(sessionsMock.fetchHistory).toHaveBeenCalledWith(EXPECTED_API_URL, "t1");
      expect(mockState.restore).toHaveBeenCalledWith(history, "t1");
    });
  });

  it("new session button resets the conversation", () => {
    render(<App />);

    fireEvent.click(screen.getByLabelText("新建会话"));
    expect(mockState.restore).toHaveBeenCalledWith([], undefined);
  });

  it("deleting a session asks for confirmation and removes it", async () => {
    const remove = vi.fn().mockResolvedValue(undefined);
    sessionsMock.useSessions.mockReturnValue({
      sessions: [
        { thread_id: "t1", title: "GMV 分析", message_count: 3, last_updated: NOW_ISO },
      ],
      loading: false,
      error: null,
      refresh: vi.fn(),
      remove,
    });
    render(<App />);

    fireEvent.click(screen.getByLabelText("删除会话 GMV 分析"));
    expect(screen.getByText("确认删除？")).toBeTruthy();

    fireEvent.click(screen.getByText("删除"));
    await waitFor(() => {
      expect(remove).toHaveBeenCalledWith("t1");
    });
  });

  it("keeps the remembered thread and silently retries when restore fails transiently", async () => {
    vi.useFakeTimers();
    sessionsMock.readLastThreadId.mockReturnValue("t1");
    // 第一次失败 = 后端临时不可达（如重启窗口），第二次成功
    sessionsMock.fetchHistory
      .mockRejectedValueOnce(new Error("Failed to fetch"))
      .mockResolvedValueOnce([{ role: "user", type: "text", id: "m1", content: "历史问题" }]);
    render(<App />);

    await act(async () => {}); // 冲刷首次失败的微任务
    expect(sessionsMock.writeLastThreadId).not.toHaveBeenCalled(); // 记忆不得清空
    expect(mockState.restore).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000); // 触发静默重试
    });
    expect(mockState.restore).toHaveBeenCalledWith(
      [{ role: "user", type: "text", id: "m1", content: "历史问题" }],
      "t1",
    );
    expect(sessionsMock.writeLastThreadId).not.toHaveBeenCalled();
  });

  it("clears the remembered thread when the session is gone (404)", async () => {
    sessionsMock.readLastThreadId.mockReturnValue("t1");
    sessionsMock.fetchHistory.mockRejectedValue(
      Object.assign(new Error("会话不存在或已被删除"), { name: "SessionNotFoundError" }),
    );
    render(<App />);

    await waitFor(() => {
      expect(sessionsMock.writeLastThreadId).toHaveBeenCalledWith(null);
    });
    expect(mockState.restore).not.toHaveBeenCalled();
  });
});
