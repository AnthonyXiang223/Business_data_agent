import { afterEach, describe, expect, it, vi } from "vitest";
import { parseFrame, streamChat } from "./chat";

describe("parseFrame", () => {
  it("parses session / delta / tool_end events", () => {
    expect(
      parseFrame('event: session\ndata: {"thread_id":"t1","is_new":true}'),
    ).toEqual({ type: "session", threadId: "t1", isNew: true });
    expect(parseFrame('event: message\ndata: {"delta":"你好"}')).toEqual({
      type: "delta",
      text: "你好",
    });
    expect(
      parseFrame(
        'event: tool_end\ndata: {"name":"create_chart","content":"{...}","tool_call_id":"c1"}',
      ),
    ).toEqual({ type: "tool", name: "create_chart", toolCallId: "c1", content: "{...}" });
  });

  it("keeps the full chart spec and drops truncated previews", () => {
    expect(
      parseFrame('event: tool_end\ndata: {"name":"query_data","preview":"短预览"}'),
    ).toEqual({ type: "tool", name: "query_data", toolCallId: undefined, content: undefined });
  });

  it("ignores heartbeat comment lines and unknown events", () => {
    expect(parseFrame(": ping")).toBeNull();
    expect(parseFrame('event: tool_start\ndata: {"name":"query_data"}')).toBeNull();
    expect(parseFrame("")).toBeNull();
    expect(parseFrame("event: message\ndata: 不是JSON")).toBeNull();
  });

  it("parses done and error", () => {
    expect(parseFrame('event: done\ndata: {"thread_id":"t1"}')).toEqual({
      type: "done",
      threadId: "t1",
    });
    expect(parseFrame('event: error\ndata: {"detail":"网关超时"}')).toEqual({
      type: "error",
      detail: "网关超时",
    });
  });

  it("parses done with run stats (tokens nullable)", () => {
    expect(
      parseFrame('event: done\ndata: {"thread_id":"t1","tool_calls":3,"tokens":520}'),
    ).toEqual({ type: "done", threadId: "t1", toolCalls: 3, tokens: 520 });
    expect(
      parseFrame('event: done\ndata: {"thread_id":"t1","tool_calls":0,"tokens":null}'),
    ).toEqual({ type: "done", threadId: "t1", toolCalls: 0, tokens: null });
  });

  it("parses the stopped event", () => {
    expect(
      parseFrame('event: stopped\ndata: {"thread_id":"t1","tool_calls":2,"tokens":150}'),
    ).toEqual({ type: "stopped", threadId: "t1", toolCalls: 2, tokens: 150 });
  });
});

function frameStream(frames: string[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const f of frames) controller.enqueue(new TextEncoder().encode(f + "\n\n"));
      controller.close();
    },
  });
}

describe("streamChat", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("emits parsed events in order", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            frameStream([
              'event: session\ndata: {"thread_id":"t1","is_new":true}',
              'event: message\ndata: {"delta":"你好"}',
              'event: done\ndata: {"thread_id":"t1","tool_calls":2,"tokens":320}',
            ]),
            { status: 200 },
          ),
      ),
    );
    const events: Array<{ type: string }> = [];
    await streamChat("http://x", { message: "hi" }, (e) => events.push(e));
    expect(events.map((e) => e.type)).toEqual(["session", "delta", "done"]);
  });

  it("passes the abort signal through to fetch", async () => {
    let capturedInit: RequestInit | undefined;
    const fetchMock = vi.fn(async (_url: unknown, init?: RequestInit) => {
      capturedInit = init;
      return new Response(frameStream([]), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const ctl = new AbortController();
    await streamChat("http://x", { message: "hi" }, () => {}, { signal: ctl.signal });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(capturedInit?.signal).toBe(ctl.signal);
  });

  it("returns silently on AbortError (deliberate stop is not an error)", async () => {
    const abortErr = Object.assign(new Error("aborted"), { name: "AbortError" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw abortErr;
      }),
    );
    await expect(streamChat("http://x", { message: "hi" }, () => {})).resolves.toBeUndefined();
  });

  it("throws on HTTP error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 500 })));
    await expect(streamChat("http://x", { message: "hi" }, () => {})).rejects.toThrow(
      "请求失败 HTTP 500",
    );
  });
});
