import { useState } from "react";
import type { FormEvent } from "react";

export default function Composer({
  onSubmit,
  disabled,
}: {
  onSubmit: (text: string) => void;
  disabled: boolean;
}) {
  const [text, setText] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    setText("");
    onSubmit(trimmed);
  }

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <input
        className="composer__input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={disabled ? "分析中…" : "输入你的分析问题"}
        aria-label="分析问题"
        disabled={disabled}
      />
      <button className="composer__button" type="submit" disabled={disabled || !text.trim()}>
        发送
      </button>
    </form>
  );
}
