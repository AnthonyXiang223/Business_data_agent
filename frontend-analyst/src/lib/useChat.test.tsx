import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChat } from "./chat";

type FetchMock = ReturnType<typeof vi.fn>;

/** 手写 /chat fetch mock：先推 frames，hang 时挂起直到 abort（AbortError 终止）。 */
function mockFetch(opts: { frames?: string[]; hang?: boolean } = {}): FetchMock {
  const fetchMock = vi.fn(async (_url: unknown, init?: RequestInit) => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const f of opts.frames ?? []) {
          controller.enqueue(new TextEncoder().encode(f + "\n\n"));
        }
        if (opts.hang) {
          // 模拟真实 fetch：abort 后流以 AbortError 终止
          const abortErr = Object.assign(new Error("aborted"), { name: "AbortError" });
          init?.signal?.addEventListener("abort", () => controller.error(abortErr));
          return;
        }
        controller.close();
      },
    });
    return new Response(stream, { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useChat", () => {
  it("assembles a full run: session, deltas, tool, done meta on the placeholder ai message", async () => {
    const fetchMock = mockFetch({
      frames: [
        'event: session\ndata: {"thread_id":"t1","is_new":true}',
        'event: message\ndata: {"delta":"结"}',
        'event: message\ndata: {"delta":"论"}',
        'event: tool_end\ndata: {"name":"create_chart","content":"{\\"title\\":\\"t\\"}","tool_call_id":"c1"}',
        'event: done\ndata: {"thread_id":"t1","tool_calls":2,"tokens":320}',
      ],
    });
    const { result } = renderHook(() => useChat("http://x"));
    await act(async () => {
      await result.current.submit("问一下");
    });

    const msgs = result.current.messages;
    expect(msgs).toHaveLength(3);
    expect(msgs[0]).toMatchObject({ type: "human", content: "问一下" });
    expect(msgs[1]).toMatchObject({ type: "ai", content: "结论" });
    expect(msgs[1].meta).toMatchObject({ toolCalls: 2, tokens: 320 });
    expect(msgs[1].meta?.durationMs).toBeTypeOf("number");
    expect(msgs[2]).toMatchObject({ type: "tool", name: "create_chart", tool_call_id: "c1" });
    expect(result.current.threadId).toBe("t1");
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      body: JSON.stringify({ message: "问一下" }),
    });
  });

  it("stop marks the last ai message stopped, aborts and posts to the stop endpoint", async () => {
    const fetchMock = mockFetch({
      frames: ['event: session\ndata: {"thread_id":"t1","is_new":true}'],
      hang: true,
    });
    const { result } = renderHook(() => useChat("http://x"));
    let p: Promise<void>;
    act(() => {
      p = result.current.submit("长分析");
    });
    await waitFor(() => expect(result.current.threadId).toBe("t1"));
    act(() => {
      result.current.stop();
    });
    await act(async () => {
      await p;
    });

    const msgs = result.current.messages;
    expect(msgs[1].stopped).toBe(true);
    expect(msgs[1].meta?.durationMs).toBeTypeOf("number");
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/sessions/t1/stop"))).toBe(true);
  });

  it("restore replaces messages and threadId and stops the old run", async () => {
    const fetchMock = mockFetch({
      frames: ['event: session\ndata: {"thread_id":"t1","is_new":true}'],
      hang: true,
    });
    const { result } = renderHook(() => useChat("http://x"));
    let p: Promise<void>;
    act(() => {
      p = result.current.submit("长分析");
    });
    await waitFor(() => expect(result.current.threadId).toBe("t1"));
    act(() => {
      result.current.restore([{ id: "m1", type: "human", content: "历史" }], "t9");
    });
    await act(async () => {
      await p;
    });

    expect(result.current.messages).toEqual([{ id: "m1", type: "human", content: "历史" }]);
    expect(result.current.threadId).toBe("t9");
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/sessions/t1/stop"))).toBe(true);
  });

  it("surfaces stream error events", async () => {
    mockFetch({ frames: ['event: error\ndata: {"detail":"网关超时"}'] });
    const { result } = renderHook(() => useChat("http://x"));
    await act(async () => {
      await result.current.submit("问");
    });

    expect(result.current.error).toBe("网关超时");
    expect(result.current.isLoading).toBe(false);
  });

  it("marks the ai message stopped when the stopped event arrives", async () => {
    mockFetch({
      frames: [
        'event: session\ndata: {"thread_id":"t1","is_new":true}',
        'event: stopped\ndata: {"thread_id":"t1","tool_calls":1,"tokens":null}',
      ],
    });
    const { result } = renderHook(() => useChat("http://x"));
    await act(async () => {
      await result.current.submit("问");
    });

    const ai = result.current.messages.find((m) => m.type === "ai");
    expect(ai?.stopped).toBe(true);
    expect(ai?.meta).toMatchObject({ toolCalls: 1, tokens: null });
    expect(result.current.error).toBeNull();
  });
});
