import { ChartBar, ChartPieSlice, List } from "@phosphor-icons/react";

// 顶栏：品牌 + 当前会话标题 + 运行状态 + 抽屉开关。
// 菜单/图表按钮只在对应抽屉布局的断点显示（CSS 控制）。
export default function Header({
  title,
  running,
  sessionsOpen,
  onToggleSessions,
  figuresOpen,
  onToggleFigures,
}: {
  title: string | null;
  running: boolean;
  sessionsOpen: boolean;
  onToggleSessions: () => void;
  figuresOpen: boolean;
  onToggleFigures: () => void;
}) {
  return (
    <header className="header">
      <button
        className="header__menu"
        type="button"
        aria-label="打开会话列表"
        aria-expanded={sessionsOpen}
        onClick={onToggleSessions}
      >
        <List size={18} />
      </button>
      <span className="header__brand">
        <span className="header__mark" aria-hidden="true">
          <ChartBar size={14} weight="fill" />
        </span>
        数据分析 · ANALYST
      </span>
      <span className="header__title" title={title ?? undefined}>
        {title ?? ""}
      </span>
      <span className="header__status" aria-live="polite">
        {running && (
          <>
            <span className="header__pulse" aria-hidden="true" />
            运行中
          </>
        )}
      </span>
      <button
        className="header__figures"
        type="button"
        aria-label="打开图表看板"
        aria-expanded={figuresOpen}
        onClick={onToggleFigures}
      >
        <ChartPieSlice size={18} />
      </button>
    </header>
  );
}
