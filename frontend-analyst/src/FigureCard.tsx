import ChartCard, { type ChartSpec } from "./ChartCard";

// 论文 Figure：编号标签 + 图表作 hero + 可展开数据详情（ChartCard 自带）。
export default function FigureCard({
  figureKey,
  number,
  spec,
}: {
  figureKey: string;
  number: number;
  spec: ChartSpec;
}) {
  return (
    <article id={figureKey} className="figure-card">
      <p className="figure-card__label">Figure {number}</p>
      <ChartCard spec={spec} />
    </article>
  );
}
