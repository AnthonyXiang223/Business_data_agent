import { useMemo, useState } from "react";
import { useStream } from "@langchain/react";
import Composer from "./Composer";
import FiguresIndex from "./FiguresIndex";
import MessageFlow from "./MessageFlow";
import { buildRows, isFigureRow, type Message } from "./lib/rows";

type StreamState = { messages: Message[] };

export default function App() {
  const apiUrl =
    import.meta.env.VITE_LANGGRAPH_API_URL ?? "http://127.0.0.1:2024";
  const [threadId, setThreadId] = useState<string | undefined>();

  const stream = useStream<StreamState>({
    apiUrl,
    assistantId: "analyst",
    threadId,
    // threadId 仅内存状态，不写 URL（会话恢复留给阶段 5 checkpoint 持久化）
    onThreadId: (id) => setThreadId(id),
  });

  const rows = useMemo(() => buildRows(stream.messages as Message[]), [stream.messages]);
  const figures = useMemo(() => rows.filter(isFigureRow), [rows]);

  function onSubmit(text: string) {
    stream.submit({ messages: [{ type: "human", content: text }] });
  }

  return (
    <>
      <header className="header">
        <h1 className="header__title">数据分析 · ANALYST</h1>
      </header>
      <div className="app-body">
        <FiguresIndex figures={figures} />
        <div className="message-flow-wrap">
          <MessageFlow
            rows={rows}
            loading={stream.isLoading}
            error={stream.error ? String(stream.error) : null}
          />
          <Composer onSubmit={onSubmit} disabled={stream.isLoading} />
        </div>
      </div>
    </>
  );
}
