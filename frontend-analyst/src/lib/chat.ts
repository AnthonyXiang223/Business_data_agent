// 自建服务层 SSE 客户端：POST /chat → 解析 SSE 事件流 → 组装 LangChain 形状消息。
// 为什么手写解析不用 EventSource：EventSource 只支持 GET，而 /chat 是 POST，
// 必须 fetch + ReadableStream 逐块解析。
// 事件协议见 server/sse.py：session / message / tool_start / tool_end / done / stopped / error，
// 心跳是 ':' 开头的注释行（客户端忽略，防代理断流）。
// 停止语义：AbortController 断开监听 + POST /sessions/{id}/stop 请求服务端
// 协作式取消（CancellationMiddleware 在下一个模型调用处生效）；abort 之后
// 服务端的 stopped 事件到不了客户端，"已停止"标记由 stop() 乐观写入。

import { useCallback, useRef, useState } from "react";
import type { Message } from "./rows";

export type ChatEvent =
  | { type: "session"; threadId: string; isNew: boolean }
  | { type: "delta"; text: string }
  | { type: "tool"; name: string; toolCallId?: string; content?: string }
  | { type: "done"; threadId: string; toolCalls?: number; tokens?: number | null }
  | { type: "stopped"; threadId: string; toolCalls?: number; tokens?: number | null }
  | { type: "error"; detail: string };

/** 单帧 SSE（event/data 两行）→ 语义事件；心跳注释行与界面不需要的事件返回 null。 */
export function parseFrame(frame: string): ChatEvent | null {
  let event = "";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!event || !data) return null;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data);
  } catch {
    return null;
  }
  switch (event) {
    case "session":
      return {
        type: "session",
        threadId: String(payload.thread_id),
        isNew: Boolean(payload.is_new),
      };
    case "message":
      return { type: "delta", text: String(payload.delta ?? "") };
    case "tool_end":
      return {
        type: "tool",
        name: String(payload.name),
        toolCallId: payload.tool_call_id ? String(payload.tool_call_id) : undefined,
        // 只有 create_chart 下发完整 content（其余工具是截断的 preview，界面不需要）
        content: payload.content ? String(payload.content) : undefined,
      };
    case "done":
    case "stopped":
      // 字段缺失时不带键（存量协议兼容）；tokens 允许 null（服务端未取到用量的情况）
      return {
        type: event,
        threadId: String(payload.thread_id),
        ...(typeof payload.tool_calls === "number" ? { toolCalls: payload.tool_calls } : {}),
        ...("tokens" in payload
          ? { tokens: typeof payload.tokens === "number" ? payload.tokens : null }
          : {}),
      };
    case "error":
      return { type: "error", detail: String(payload.detail) };
    default:
      return null; // tool_start 等其余事件界面不需要
  }
}

/** 发起对话并把每个语义事件回调出去；网络/HTTP 错误抛给调用方，
 * AbortError（主动停止）静默返回。 */
export async function streamChat(
  apiUrl: string,
  body: { message: string; threadId?: string },
  onEvent: (event: ChatEvent) => void,
  opts?: { signal?: AbortSignal },
): Promise<void> {
  try {
    const res = await fetch(`${apiUrl}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: opts?.signal,
    });
    if (!res.ok || !res.body) {
      throw new Error(`请求失败 HTTP ${res.status}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const event = parseFrame(frame);
        if (event) onEvent(event);
      }
    }
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") return; // 主动停止不算错误
    throw err;
  }
}

/**
 * 对话状态管理：把 SSE 事件流组装回 rows.ts 期望的 LangChain 形状消息。
 * 界面呈现约定与 rows.ts 一致：human 立即显示、message 增量累积进当前 AI 消息、
 * tool 消息只收 create_chart 的成功产物（其余工具流量全隐）。
 * done/stopped 的运行元信息（工具次数/token/耗时）挂到本轮 AI 占位消息。
 * stop()：乐观标记已停止 + abort 断开 + POST /stop 请求服务端协作式取消。
 * restore()：整体替换为某会话的历史消息（会话切换由 App 协调）。
 */
export function useChat(apiUrl: string) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string | undefined>();
  const seq = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const threadIdRef = useRef<string | undefined>(undefined);
  const startRef = useRef(0);

  const applyThreadId = useCallback((id: string) => {
    threadIdRef.current = id;
    setThreadId(id);
  }, []);

  const requestStop = useCallback(
    (tid: string | undefined) => {
      if (!tid) return;
      // best-effort：服务端在下一个模型调用处生效；失败静默（本地 abort 已断开）
      fetch(`${apiUrl}/sessions/${encodeURIComponent(tid)}/stop`, {
        method: "POST",
      }).catch(() => {});
    },
    [apiUrl],
  );

  const submit = useCallback(
    async (text: string) => {
      const humanId = `h-${++seq.current}`;
      const aiId = `a-${seq.current}`;
      const ctl = new AbortController();
      abortRef.current = ctl;
      startRef.current = Date.now();
      setIsLoading(true);
      setError(null);
      setMessages((prev) => [
        ...prev,
        { id: humanId, type: "human", content: text },
        { id: aiId, type: "ai", content: "" }, // 增量累积的占位消息（空文本不渲染行）
      ]);
      try {
        await streamChat(
          apiUrl,
          { message: text, threadId: threadIdRef.current },
          (event) => {
            switch (event.type) {
              case "session":
                applyThreadId(event.threadId);
                break;
              case "delta":
                setMessages((prev) => {
                  const last = prev[prev.length - 1];
                  if (!last || last.id !== aiId) return prev;
                  return [
                    ...prev.slice(0, -1),
                    { ...last, content: (last.content as string) + event.text },
                  ];
                });
                break;
              case "tool":
                if (event.name === "create_chart" && event.content) {
                  setMessages((prev) => [
                    ...prev,
                    {
                      id: event.toolCallId,
                      type: "tool",
                      name: event.name,
                      tool_call_id: event.toolCallId,
                      content: event.content,
                    },
                  ]);
                }
                break;
              case "done":
              case "stopped": {
                // 元信息按 aiId 精确挂到本轮的占位 AI 消息（不按"最后一条 AI"猜，
                // 恢复的历史消息不会被误挂）
                const meta = {
                  toolCalls: event.toolCalls,
                  tokens: event.tokens,
                  durationMs: Date.now() - startRef.current,
                };
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === aiId
                      ? { ...m, meta, ...(event.type === "stopped" ? { stopped: true } : {}) }
                      : m,
                  ),
                );
                break;
              }
              case "error":
                setError(event.detail);
                break;
            }
          },
          { signal: ctl.signal },
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setIsLoading(false);
        if (abortRef.current === ctl) abortRef.current = null;
      }
    },
    [apiUrl, applyThreadId],
  );

  const stop = useCallback(() => {
    const ctl = abortRef.current;
    if (!ctl) return;
    // 乐观标记最后一条 AI 消息"已停止"：abort 之后服务端的 stopped 事件到不了客户端
    setMessages((prev) => {
      let idx = -1;
      for (let i = prev.length - 1; i >= 0; i--) {
        if (prev[i].type === "ai") {
          idx = i;
          break;
        }
      }
      if (idx < 0) return prev;
      const last = prev[idx];
      return [
        ...prev.slice(0, idx),
        {
          ...last,
          stopped: true,
          meta: { ...last.meta, durationMs: Date.now() - startRef.current },
        },
        ...prev.slice(idx + 1),
      ];
    });
    ctl.abort();
    requestStop(threadIdRef.current);
  }, [requestStop]);

  const restore = useCallback(
    (next: Message[], nextThreadId: string | undefined) => {
      const ctl = abortRef.current;
      if (ctl) {
        ctl.abort();
        // 切走时旧会话还在跑：通知服务端停止，防后台继续烧 token
        if (threadIdRef.current && threadIdRef.current !== nextThreadId) {
          requestStop(threadIdRef.current);
        }
      }
      abortRef.current = null;
      setMessages(next);
      threadIdRef.current = nextThreadId;
      setThreadId(nextThreadId);
      setError(null);
      setIsLoading(false);
    },
    [requestStop],
  );

  return { messages, isLoading, error, threadId, submit, stop, restore };
}
