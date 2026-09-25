import { useEffect, useState } from "react";
import { Plus, Trash } from "@phosphor-icons/react";
import { formatRelative } from "./lib/format";
import type { SessionSummary } from "./lib/sessions";

// 会话侧栏：列表 + 新建 + 两段式删除确认。
// 选中/删除是平级兄弟按钮（button 内不能嵌 button）。
export default function SessionSidebar({
  sessions,
  loading,
  error,
  currentThreadId,
  onSelect,
  onNew,
  onDelete,
}: {
  sessions: SessionSummary[];
  loading: boolean;
  error: string | null;
  currentThreadId?: string;
  onSelect: (threadId: string) => void;
  onNew: () => void;
  onDelete: (threadId: string) => void;
}) {
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  // 已删除的会话不再出现在列表时，复位确认态
  useEffect(() => {
    if (confirmingId && !sessions.some((s) => s.thread_id === confirmingId)) {
      setConfirmingId(null);
    }
  }, [sessions, confirmingId]);

  return (
    <aside className="sessions" aria-label="会话列表">
      <div className="sessions__head">
        <h2 className="sessions__heading">会话</h2>
        <button className="sessions__new" type="button" aria-label="新建会话" onClick={onNew}>
          <Plus size={16} />
        </button>
      </div>
      {loading && sessions.length === 0 ? (
        <div className="sessions__skeleton" aria-hidden="true">
          <i />
          <i />
          <i />
        </div>
      ) : error ? (
        <p className="sessions__error">{error}</p>
      ) : sessions.length === 0 ? (
        <p className="sessions__empty">暂无会话</p>
      ) : (
        <ul className="sessions__list">
          {sessions.map((s) => (
            <li className="sessions__row" key={s.thread_id}>
              <button
                className={`sessions__item${
                  s.thread_id === currentThreadId ? " sessions__item--active" : ""
                }`}
                type="button"
                onClick={() => onSelect(s.thread_id)}
              >
                <span className="sessions__item-title">{s.title}</span>
                <span className="sessions__item-meta">
                  {s.message_count} 条 · {formatRelative(s.last_updated)}
                </span>
              </button>
              {confirmingId === s.thread_id ? (
                <span className="sessions__confirm">
                  确认删除？
                  <button
                    className="sessions__confirm-yes"
                    type="button"
                    onClick={() => onDelete(s.thread_id)}
                  >
                    删除
                  </button>
                  <button
                    className="sessions__confirm-no"
                    type="button"
                    onClick={() => setConfirmingId(null)}
                  >
                    取消
                  </button>
                </span>
              ) : (
                <button
                  className="sessions__item-delete"
                  type="button"
                  aria-label={`删除会话 ${s.title}`}
                  onClick={() => setConfirmingId(s.thread_id)}
                >
                  <Trash size={14} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
