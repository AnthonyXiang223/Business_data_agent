import { useEffect, useMemo, useRef, useState } from "react";
import Composer from "./Composer";
import FiguresIndex from "./FiguresIndex";
import Header from "./Header";
import MessageFlow from "./MessageFlow";
import SessionSidebar from "./SessionSidebar";
import WelcomeScreen from "./WelcomeScreen";
import { useChat } from "./lib/chat";
import { buildRows, isFigureRow } from "./lib/rows";
import {
  fetchHistory,
  readLastThreadId,
  toMessages,
  useSessions,
  writeLastThreadId,
} from "./lib/sessions";

// 三栏工作台编排：会话列表状态住在 useSessions，对话消息状态住在 useChat，
// App 只协调两者的交叉动作（恢复/新建/删除）与抽屉开关。
export default function App() {
  // 自建服务层（server/，端口 8000）；SSE 事件协议见 server/sse.py
  const apiUrl =
    import.meta.env.VITE_ANALYST_API_URL ?? "http://127.0.0.1:8000";
  const chat = useChat(apiUrl);
  const sessions = useSessions(apiUrl);
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [figuresOpen, setFiguresOpen] = useState(false);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const restoreSeq = useRef(0);
  const autoRestored = useRef(false);
  const wasLoading = useRef(false);

  // 挂载：加载会话列表 + 自动恢复 localStorage 记住的最后会话。
  // 失败分两种：会话真被删（404 → SessionNotFoundError）→ 清记忆回欢迎空态；
  // 后端临时不可达（网络/5xx）→ 保留记忆，每 5 秒静默重试直到成功——
  // 旧逻辑失败一律清空记忆，页面刷新恰逢后端重启窗口会"永久失忆"
  // （实测：下一条消息开新会话，多轮上下文断链）
  useEffect(() => {
    sessions.refresh();
    if (autoRestored.current) return; // StrictMode 双执行防抖
    autoRestored.current = true;
    const last = readLastThreadId();
    if (!last) return;
    const my = ++restoreSeq.current;
    setRestoringId(last);

    void (async () => {
      for (;;) {
        if (restoreSeq.current !== my) return; // 用户已新建/切换会话：放弃本次恢复
        try {
          const history = await fetchHistory(apiUrl, last);
          if (restoreSeq.current !== my) return;
          chat.restore(toMessages(history), last);
          return;
        } catch (err) {
          if (restoreSeq.current !== my) return;
          if (err instanceof Error && err.name === "SessionNotFoundError") {
            writeLastThreadId(null); // 会话真不存在：清记忆，回欢迎空态
            return;
          }
          await new Promise((r) => setTimeout(r, 5000)); // 临时故障：静默重试
        }
      }
    })().finally(() => {
      if (restoreSeq.current === my) setRestoringId(null);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // threadId 变化（session 事件回传 / restore / 新建）→ 持久化 + 刷新列表
  useEffect(() => {
    if (chat.threadId) {
      writeLastThreadId(chat.threadId);
      sessions.refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.threadId]);

  // 每轮运行结束（done/stopped/error 都让 isLoading 落回 false）→ 刷新列表
  useEffect(() => {
    if (wasLoading.current && !chat.isLoading) sessions.refresh();
    wasLoading.current = chat.isLoading;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.isLoading]);

  async function selectSession(threadId: string) {
    setSessionsOpen(false);
    if (threadId === chat.threadId && chat.messages.length > 0) return; // 点击当前会话：只收起抽屉
    if (chat.isLoading) chat.stop();
    const my = ++restoreSeq.current;
    setRestoringId(threadId);
    try {
      const history = await fetchHistory(apiUrl, threadId);
      if (restoreSeq.current !== my) return; // 期间点了别的会话/新建：丢弃过期响应
      chat.restore(toMessages(history), threadId);
      setHistoryError(null);
    } catch (err) {
      if (restoreSeq.current !== my) return;
      setHistoryError(err instanceof Error ? err.message : String(err));
    } finally {
      if (restoreSeq.current === my) setRestoringId(null);
    }
  }

  function handleNewSession() {
    if (chat.isLoading) chat.stop();
    restoreSeq.current++; // 使在途 restore 全部失效
    setRestoringId(null);
    chat.restore([], undefined);
    writeLastThreadId(null);
    setHistoryError(null);
  }

  async function handleDelete(threadId: string) {
    // 删除的是正在运行的当前会话：先停再删，防后台运行重建 checkpoints
    if (threadId === chat.threadId && chat.isLoading) chat.stop();
    await sessions.remove(threadId);
    if (threadId === chat.threadId) {
      chat.restore([], undefined);
      writeLastThreadId(null);
      setHistoryError(null);
    }
  }

  const rows = useMemo(() => buildRows(chat.messages), [chat.messages]);
  const figures = useMemo(() => rows.filter(isFigureRow), [rows]);
  const currentTitle =
    chat.messages.length === 0
      ? null
      : (sessions.sessions.find((s) => s.thread_id === chat.threadId)?.title ?? "新会话");

  return (
    <>
      <Header
        title={currentTitle}
        running={chat.isLoading}
        sessionsOpen={sessionsOpen}
        onToggleSessions={() => setSessionsOpen((v) => !v)}
        figuresOpen={figuresOpen}
        onToggleFigures={() => setFiguresOpen((v) => !v)}
      />
      {sessionsOpen && (
        <button
          className="drawer-backdrop"
          type="button"
          aria-label="关闭会话列表"
          onClick={() => setSessionsOpen(false)}
        />
      )}
      {figuresOpen && (
        <button
          className="drawer-backdrop"
          type="button"
          aria-label="关闭图表看板"
          onClick={() => setFiguresOpen(false)}
        />
      )}
      <div
        className={[
          "app-body",
          figuresOpen && "app-body--figures-open",
          sessionsOpen && "app-body--sessions-open",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        <SessionSidebar
          sessions={sessions.sessions}
          loading={sessions.loading}
          error={sessions.error}
          currentThreadId={chat.threadId}
          onSelect={(id) => void selectSession(id)}
          onNew={handleNewSession}
          onDelete={(id) => void handleDelete(id)}
        />
        <main className="message-flow-wrap">
          {rows.length === 0 && !chat.isLoading && restoringId === null ? (
            <WelcomeScreen onAsk={(q) => void chat.submit(q)} disabled={false} />
          ) : (
            <MessageFlow
              rows={rows}
              loading={chat.isLoading}
              error={chat.error ?? historyError}
            />
          )}
          <Composer
            onSubmit={(t) => void chat.submit(t)}
            onStop={chat.stop}
            running={chat.isLoading}
            disabled={restoringId !== null}
          />
        </main>
        <aside className="figures-rail">
          <FiguresIndex figures={figures} />
        </aside>
      </div>
    </>
  );
}
