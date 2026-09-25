import { useState } from "react";
import type { FormEvent } from "react";
import { PaperPlaneRight, Stop } from "@phosphor-icons/react";

// 输入区：运行中发送按钮变停止（对话启停）。
export default function Composer({
  onSubmit,
  onStop,
  running,
  disabled,
}: {
  onSubmit: (text: string) => void;
  onStop: () => void;
  running: boolean;
  disabled: boolean;
}) {
  const [text, setText] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || disabled || running) return;
    setText("");
    onSubmit(trimmed);
  }

  const placeholder = running
    ? "分析中，可点击停止"
    : disabled
      ? "加载会话中…"
      : "输入你的分析问题";

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <input
        className="composer__input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
        aria-label="分析问题"
        disabled={disabled || running}
      />
      {running ? (
        <button className="composer__button composer__button--stop" type="button" onClick={onStop}>
          <Stop size={14} />
          停止
        </button>
      ) : (
        <button className="composer__button" type="submit" disabled={disabled || !text.trim()}>
          <PaperPlaneRight size={14} />
          发送
        </button>
      )}
    </form>
  );
}
