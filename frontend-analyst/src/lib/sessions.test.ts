import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchHistory,
  LAST_THREAD_KEY,
  listSessions,
  readLastThreadId,
  removeSession,
  toMessages,
  writeLastThreadId,
} from "./sessions";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("toMessages", () => {
  it("maps user/assistant messages straight through", () => {
    expect(
      toMessages([
        { role: "user", type: "text", id: "m1", content: "问题" },
        { role: "assistant", type: "text", id: "m2", content: "回答" },
      ]),
    ).toEqual([
      { id: "m1", type: "human", content: "问题" },
      { id: "m2", type: "ai", content: "回答" },
    ]);
  });

  it("maps create_chart tool messages with message id as the stable figure key", () => {
    expect(
      toMessages([
        { role: "tool", type: "tool", id: "m3", name: "create_chart", content: '{"title":"t"}' },
      ]),
    ).toEqual([
      { id: "m3", type: "tool", name: "create_chart", tool_call_id: "m3", content: '{"title":"t"}' },
    ]);
  });

  it("skips non-chart tools, unknown roles and assistant tool_calls", () => {
    expect(
      toMessages([
        { role: "tool", type: "tool", id: "m1", name: "query_data", content: "数据" },
        { role: "other", id: "m2", content: "摘要" },
        { role: "assistant", id: "m3", content: "", tool_calls: [{ name: "create_chart", args: {} }] },
      ]),
    ).toEqual([]);
  });
});

describe("lastThreadId persistence", () => {
  it("round-trips through localStorage", () => {
    expect(readLastThreadId()).toBeNull();
    writeLastThreadId("t1");
    expect(localStorage.getItem(LAST_THREAD_KEY)).toBe("t1");
    expect(readLastThreadId()).toBe("t1");
    writeLastThreadId(null);
    expect(readLastThreadId()).toBeNull();
  });
});

describe("sessions API", () => {
  it("listSessions parses the sessions payload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ sessions: [{ thread_id: "t1", title: "A", message_count: 1, last_updated: "x" }] }),
          { status: 200 },
        ),
      ),
    );
    const list = await listSessions("http://x");
    expect(list).toEqual([{ thread_id: "t1", title: "A", message_count: 1, last_updated: "x" }]);
  });

  it("listSessions throws on HTTP error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 500 })));
    await expect(listSessions("http://x")).rejects.toThrow("请求失败 HTTP 500");
  });

  it("fetchHistory returns messages and maps 404 to a friendly message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ messages: [{ role: "user" }] }), { status: 200 })),
    );
    expect(await fetchHistory("http://x", "t1")).toEqual([{ role: "user" }]);

    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 404 })));
    await expect(fetchHistory("http://x", "t1")).rejects.toThrow("会话不存在或已被删除");
  });

  it("removeSession sends DELETE", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await removeSession("http://x", "t1");
    expect(fetchMock).toHaveBeenCalledWith("http://x/sessions/t1", { method: "DELETE" });
  });
});
