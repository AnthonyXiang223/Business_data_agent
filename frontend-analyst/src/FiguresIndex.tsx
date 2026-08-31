import type { FigureRow } from "./lib/rows";

// 图表看板：会话内全部 Figure 的索引（论文的 figure 列表）。
// 锚点点击平滑滚动定位；:target 由 CSS 做淡蓝高亮。
export default function FiguresIndex({ figures }: { figures: FigureRow[] }) {
  return (
    <nav className="figures-index" aria-label="图表看板">
      <p className="figures-index__heading">图表</p>
      {figures.length === 0 ? (
        <p className="figures-index__empty">暂无图表</p>
      ) : (
        <div className="figures-index__list">
          {figures.map((f) => (
            <a key={f.key} className="figures-index__entry" href={`#${f.key}`}>
              <span className="figures-index__number">Figure {f.number}</span>
              <span className="figures-index__title">{f.spec.title}</span>
            </a>
          ))}
        </div>
      )}
    </nav>
  );
}
