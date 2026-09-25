// 会话库：列表/历史/删除 + localStorage 记忆 + 历史消息映射（App 编排层）。
// 服务端 API：GET /sessions、GET /sessions/{id}/history、DELETE /sessions/{id}
// （POST /sessions/{id}/stop 在 chat.ts 里调用）。
// 历史消息由 server/sessions.py 的 _serialize 输出（user/assistant/tool/other），
// 这里映射回 rows.ts 期望的 LangChain 形状。

import { useCallback, useRef, useState } from "react";
import type { Message } from "./rows";

export type SessionSummary = {
  thread_id: string;
  title: string;
  message_count: number;
  last_updated: string;
};

export type HistoryMessage = {
  role: "user" | "assistant" | "tool" | "other";
  type?: string;
  id?: string;
  content: string;
  tool_calls?: { name: string; args?: unknown }[];
  name?: string;
};

export const LAST_THREAD_KEY = "analyst.lastThreadId";

export function readLastThreadId(): string | null {
  try {
    return localStorage.getItem(LAST_THREAD_KEY);
  } catch {
    return null; // 存储不可用（隐私模式等）
  }
}

export function writeLastThreadId(id: string | null): void {
  try {
    if (id === null) localStorage.removeItem(LAST_THREAD_KEY);
    else localStorage.setItem(LAST_THREAD_KEY, id);
  } catch {
    // 存储不可用静默降级：仅失去刷新自动恢复
  }
}

export async function listSessions(apiUrl: string): Promise<SessionSummary[]> {
  const res = await fetch(`${apiUrl}/sessions`);
  if (!res.ok) throw new Error(`请求失败 HTTP ${res.status}`);
  const data = (await res.json()) as { sessions?: SessionSummary[] };
  return data.sessions ?? [];
}

export async function fetchHistory(
  apiUrl: string,
  threadId: string,
): Promise<HistoryMessage[]> {
  const res = await fetch(`${apiUrl}/sessions/${encodeURIComponent(threadId)}/history`);
  if (res.status === 404) {
    // name 标记会话真被删（与后端临时不可达区分，App 恢复逻辑按 name 判断，
    // 同 chat.ts 的 AbortError 模式）
    const err = new Error("会话不存在或已被删除");
    err.name = "SessionNotFoundError";
    throw err;
  }
  if (!res.ok) throw new Error(`请求失败 HTTP ${res.status}`);
  const data = (await res.json()) as { messages?: HistoryMessage[] };
  return data.messages ?? [];
}

export async function removeSession(apiUrl: string, threadId: string): Promise<void> {
  const res = await fetch(`${apiUrl}/sessions/${encodeURIComponent(threadId)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`请求失败 HTTP ${res.status}`);
}

/** 服务端 _serialize 输出 → rows.ts 期望的 Message[]（历史恢复映射，纯函数）。 */
export function toMessages(history: HistoryMessage[]): Message[] {
  const out: Message[] = [];
  for (const m of history) {
    if (m.role === "user") {
      out.push({ id: m.id, type: "human", content: m.content });
    } else if (m.role === "assistant") {
      // tool_calls 数组丢弃：rows.ts 永不渲染；空文本载体消息（create_chart 调用载体）也跳过
      const body = (m.content ?? "").trim();
      if (body) out.push({ id: m.id, type: "ai", content: m.content });
    } else if (m.role === "tool" && m.name === "create_chart") {
      // 服务端 ToolMessage 序列化不含 tool_call_id，用消息 id 作稳定图卡 key
      out.push({ id: m.id, type: "tool", name: m.name, tool_call_id: m.id, content: m.content });
    }
    // 其余 tool 消息与 role "other"（摘要消息等）跳过：界面全隐
  }
  return out;
}

/** 会话列表状态：refresh 带 seq 守卫（过期响应丢弃）；loading 仅首次加载。 */
export function useSessions(apiUrl: string) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  const refresh = useCallback(async () => {
    const my = ++seq.current;
    try {
      const list = await listSessions(apiUrl);
      if (seq.current !== my) return; // 过期响应丢弃（连续刷新）
      setSessions(list);
      setError(null);
    } catch (err) {
      if (seq.current !== my) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (seq.current === my) setLoading(false);
    }
  }, [apiUrl]);

  const remove = useCallback(
    async (threadId: string) => {
      await removeSession(apiUrl, threadId);
      await refresh();
    },
    [apiUrl, refresh],
  );

  return { sessions, loading, error, refresh, remove };
}
