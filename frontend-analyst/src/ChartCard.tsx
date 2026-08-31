import { useMemo, useState } from "react";

// ChartCard: renders the agent's chart spec v1 as hand-rolled SVG (bar/line/pie)
// or an HTML table. Visual rules follow the Nature-figure aesthetic (open axes,
// thin lines, no legend frame, direct labels, restrained palette) with the
// palette validated by the dataviz skill's validator; colors live in CSS vars
// (--chart-series-N) so light/dark are CSS-only, no JS mode switching.

export type ChartSeries = { name: string; values: number[] };
export type ChartSpec = {
  title: string;
  chart_type: "bar" | "line" | "pie" | "table";
  data: { categories: string[]; series: ChartSeries[] };
};

export function parseChartSpec(result: string): ChartSpec | null {
  let raw: unknown;
  try {
    raw = JSON.parse(result);
  } catch {
    return null;
  }
  if (typeof raw !== "object" || raw === null) return null;
  const spec = raw as Record<string, unknown>;
  if (typeof spec.title !== "string" || !spec.title.trim()) return null;
  if (
    spec.chart_type !== "bar" &&
    spec.chart_type !== "line" &&
    spec.chart_type !== "pie" &&
    spec.chart_type !== "table"
  )
    return null;
  const data = spec.data as Record<string, unknown> | undefined;
  if (!data || !Array.isArray(data.categories)) return null;
  if (!data.categories.every((c) => typeof c === "string")) return null;
  if (!Array.isArray(data.series) || data.series.length === 0) return null;
  const series: ChartSeries[] = [];
  for (const s of data.series) {
    if (
      typeof s !== "object" ||
      s === null ||
      typeof (s as Record<string, unknown>).name !== "string" ||
      !Array.isArray((s as Record<string, unknown>).values)
    )
      return null;
    const values = ((s as Record<string, unknown>).values as unknown[])
      .filter((v) => typeof v === "number" && Number.isFinite(v)) as number[];
    series.push({ name: (s as Record<string, unknown>).name as string, values });
  }
  return {
    title: spec.title.trim(),
    chart_type: spec.chart_type,
    data: { categories: data.categories as string[], series },
  };
}

// ---------- shared helpers ----------

const W = 640;
const H = 400;
const M = { left: 56, right: 20, top: 16 };
const FONT = 12;

function fmtNum(v: number): string {
  return v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

// 配色模型（用户拍板）：每个"变量组"一个低饱和色家族。
// 组内按 X 轴类别取档位（浅→深，超出循环）；第二组橙家族，第三组绿家族，依此类推。
const SERIES_RAMPS: string[][] = [
  ["#e8eaf6", "#bbcfe8", "#92bbd7", "#659bca", "#376eae", "#234086"], // 蓝组
  ["#f5d9cb", "#eea78b", "#e97c5b", "#e65037", "#c42126", "#851718"], // 橙组
  ["#e6f0e2", "#c4dcc0", "#9ec89d", "#78b478", "#529f52", "#2e832e"], // 绿组
];

function seriesGroupRamp(seriesIndex: number): string[] {
  return SERIES_RAMPS[seriesIndex % SERIES_RAMPS.length];
}

/** 类别色：该系列所在组的第 categoryIndex 档（浅→深） */
function categoryColor(seriesIndex: number, categoryIndex: number): string {
  const ramp = seriesGroupRamp(seriesIndex);
  return ramp[categoryIndex % ramp.length];
}

/** 组内最深档：折线连线/端标等需要单一代表色时使用 */
function seriesSolidColor(seriesIndex: number): string {
  const ramp = seriesGroupRamp(seriesIndex);
  return ramp[ramp.length - 1];
}

/** Estimate rendered text width in SVG px: CJK ≈ 1×fontSize, ASCII ≈ 0.62×fontSize. */
function estLabelWidth(label: string, fontSize: number): number {
  let w = 0;
  for (const ch of label) {
    w += ch.codePointAt(0)! > 0xff ? fontSize : fontSize * 0.62;
  }
  return w;
}

function truncateLabel(label: string, maxChars: number): string {
  return label.length > maxChars ? `${label.slice(0, maxChars - 1)}…` : label;
}

function niceStep(range: number, target: number): number {
  const raw = range / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
}

type TooltipState = {
  /** position in viewBox units; converted to % of the plot box on render */
  x: number;
  y: number;
  rows: { label: string; color: string; value: string }[];
};

type HoverHandlers = {
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  onFocus: () => void;
  onBlur: () => void;
};

function tooltipStyle(x: number, y: number): { left: string; top: string } {
  const leftFrac = x / W > 0.7 ? (x - 200) / W : (x + 10) / W;
  return {
    left: `${Math.max(0, leftFrac * 100)}%`,
    top: `${Math.min(88, (y / H) * 100)}%`,
  };
}

function DataTable({ spec }: { spec: ChartSpec }) {
  return (
    <table className="chart-card__table">
      <thead>
        <tr>
          <th></th>
          {spec.data.categories.map((c) => (
            <th key={c}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {spec.data.series.map((s) => (
          <tr key={s.name}>
            <th scope="row">{s.name}</th>
            {s.values.map((v, i) => (
              <td key={i}>{fmtNum(v)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------- bar ----------

function BarPlot({
  spec,
  makeHandlers,
}: {
  spec: ChartSpec;
  makeHandlers: (show: TooltipState) => HoverHandlers;
}) {
  const cats = spec.data.categories;
  const series = spec.data.series;
  const maxVal = Math.max(0, ...series.flatMap((s) => s.values));
  const step = niceStep(Math.max(maxVal, 1), 5);
  const yMax = Math.ceil(maxVal / step) * step || 1;

  // series beyond 5 slots fold into one gray "Other" (palette has only 5 hues)
  const visible = series.slice(0, 5);
  const rest = series.slice(5);
  const plotSeries = [
    ...visible,
    ...(rest.length > 0
      ? [
          {
            name: "Other",
            values: cats.map((_, i) => rest.reduce((acc, s) => acc + (s.values[i] ?? 0), 0)),
          },
        ]
      : []),
  ];

  const plotW = W - M.left - M.right;
  const band = plotW / cats.length;
  const longest = Math.max(...cats.map((c) => estLabelWidth(truncateLabel(c, 14), FONT)));
  const rotate = longest > band;
  const bottom = rotate ? 58 : 30;
  const plotH = H - M.top - bottom;
  const y = (v: number) => M.top + plotH - (v / yMax) * plotH;

  const ticks = useMemo(() => {
    const out: number[] = [];
    for (let t = 0; t <= yMax + step / 2; t += step) out.push(t);
    return out;
  }, [yMax, step]);

  const showDirectLabels = series.length <= 2 && cats.length <= 8;
  const groupW = band * 0.7;
  const barW = Math.max(2, Math.min(groupW / plotSeries.length - 2, 26));

  return (
    <svg className="chart-card__svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={spec.title}>
      {ticks.map((t) => (
        <g key={t}>
          {/* nature 风：只画刻度短线，不画横贯画布的网格线 */}
          <line x1={M.left - 5} x2={M.left} y1={y(t)} y2={y(t)} className="chart-card__axis" />
          <text x={M.left - 8} y={y(t) + 4} textAnchor="end" className="chart-card__tick-label">
            {fmtNum(t)}
          </text>
        </g>
      ))}
      {/* open axes: left + bottom only */}
      <line x1={M.left} x2={M.left} y1={M.top} y2={M.top + plotH} className="chart-card__axis" />
      <line
        x1={M.left}
        x2={W - M.right}
        y1={M.top + plotH}
        y2={M.top + plotH}
        className="chart-card__axis"
      />

      {cats.map((cat, ci) => {
        const gx = M.left + ci * band + (band - groupW) / 2;
        const xPos = M.left + ci * band + band / 2;
        const labelY = H - (rotate ? 10 : 4);
        return (
          <g key={cat}>
            {plotSeries.map((s, si) => {
              const v = s.values[ci];
              if (typeof v !== "number" || !Number.isFinite(v) || v <= 0) return null;
              const bx = gx + si * (groupW / plotSeries.length);
              const h = (v / yMax) * plotH;
              const color = categoryColor(si, ci);
              const rTop = Math.min(3, barW / 2, h / 2); // top corner radius, clamped
              const d =
                h > rTop
                  ? // bottom edge must be explicit: Z alone would close from the
                    // bottom-right corner diagonally back to the top-left start
                    // point, turning the bar into a triangle
                    `M ${bx} ${y(v)} L ${bx} ${y(v) + rTop} Q ${bx} ${y(v)} ${bx + rTop} ${y(v)} L ${bx + barW - rTop} ${y(v)} Q ${bx + barW} ${y(v)} ${bx + barW} ${y(v) + rTop} L ${bx + barW} ${y(v) + h} L ${bx} ${y(v) + h} Z`
                  : `M ${bx} ${y(v)} h ${barW} v ${h} h ${-barW} Z`;
              return (
                <path
                  key={si}
                  d={d}
                  fill={color}
                  className="chart-card__bar"
                  tabIndex={0}
                  role="graphics-symbol"
                  aria-label={`${cat} ${s.name} ${fmtNum(v)}`}
                  {...makeHandlers({
                    x: bx + barW / 2,
                    y: y(v),
                    rows: [{ label: `${cat} · ${s.name}`, color, value: fmtNum(v) }],
                  })}
                />
              );
            })}
            {/* direct value labels on bar tops (also the contrast-WARN relief channel) */}
            {showDirectLabels &&
              plotSeries.map((s, si) => {
                const v = s.values[ci];
                if (typeof v !== "number" || !Number.isFinite(v) || v <= 0) return null;
                return (
                  <text
                    key={si}
                    x={gx + si * (groupW / plotSeries.length) + barW / 2}
                    y={y(v) - 4}
                    textAnchor="middle"
                    className="chart-card__direct-label"
                  >
                    {fmtNum(v)}
                  </text>
                );
              })}
            <text
              x={xPos}
              y={labelY}
              textAnchor={rotate ? "end" : "middle"}
              transform={rotate ? `rotate(-30 ${xPos} ${labelY})` : undefined}
              className="chart-card__cat-label"
            >
              {truncateLabel(cat, 14)}
              <title>{cat}</title>
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ---------- line ----------

function LinePlot({
  spec,
  makeHandlers,
}: {
  spec: ChartSpec;
  makeHandlers: (show: TooltipState) => HoverHandlers;
}) {
  const cats = spec.data.categories;
  const series = spec.data.series;
  const all = series.flatMap((s) => s.values);
  const minVal = Math.min(...all);
  const maxVal = Math.max(...all);
  const range = Math.max(maxVal - minVal, Math.max(Math.abs(maxVal), 1));
  const step = niceStep(range, 5);
  const yMax = Math.ceil(maxVal / step) * step;
  const yMin = minVal >= 0 ? 0 : Math.floor(minVal / step) * step;

  const bottom = 40;
  const plotH = H - M.top - bottom;
  const plotW = W - M.left - M.right;
  const band = plotW / cats.length;
  const span = yMax - yMin || 1;
  const y = (v: number) => M.top + ((yMax - v) / span) * plotH;
  const x = (ci: number) => M.left + ci * band + band / 2;

  const ticks = useMemo(() => {
    const out: number[] = [];
    for (let t = yMin; t <= yMax + step / 2; t += step) out.push(t);
    return out;
  }, [yMin, yMax, step]);

  const [hovered, setHovered] = useState<number | null>(null);
  const clear = () => setHovered(null);

  return (
    <svg className="chart-card__svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={spec.title}>
      {ticks.map((t) => (
        <g key={t}>
          {/* nature 风：只画刻度短线，不画横贯画布的网格线 */}
          <line x1={M.left - 5} x2={M.left} y1={y(t)} y2={y(t)} className="chart-card__axis" />
          <text x={M.left - 8} y={y(t) + 4} textAnchor="end" className="chart-card__tick-label">
            {fmtNum(t)}
          </text>
        </g>
      ))}
      <line x1={M.left} x2={M.left} y1={M.top} y2={M.top + plotH} className="chart-card__axis" />
      <line x1={M.left} x2={W - M.right} y1={M.top + plotH} y2={M.top + plotH} className="chart-card__axis" />

      {hovered !== null && (
        <line x1={x(hovered)} x2={x(hovered)} y1={M.top} y2={M.top + plotH} className="chart-card__crosshair" />
      )}

      {series.map((s, si) => {
        const lineColor = seriesSolidColor(si);
        const pts = s.values
          .map((v, ci) => (Number.isFinite(v) ? { ci, v } : null))
          .filter((p): p is { ci: number; v: number } => p !== null);
        const path = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.ci)} ${y(p.v)}`).join(" ");
        return (
          <g key={s.name}>
            <path d={path} fill="none" stroke={lineColor} className="chart-card__line" />
            {pts.map((p) => {
              const dotColor = categoryColor(si, p.ci);
              return (
                <g key={p.ci}>
                  <circle cx={x(p.ci)} cy={y(p.v)} r={3.5} fill={dotColor} className="chart-card__dot" />
                  <circle
                    cx={x(p.ci)}
                    cy={y(p.v)}
                    r={12}
                    fill="transparent"
                    tabIndex={0}
                    role="graphics-symbol"
                    aria-label={`${cats[p.ci]} ${s.name} ${fmtNum(p.v)}`}
                    {...makeHandlers({
                      x: x(p.ci),
                      y: y(p.v),
                      rows: [{ label: `${cats[p.ci]} · ${s.name}`, color: dotColor, value: fmtNum(p.v) }],
                    })}
                  />
                </g>
              );
            })}
            {pts.length > 0 && (
              <text
                x={x(pts[pts.length - 1].ci) + 6}
                y={y(pts[pts.length - 1].v) - 6}
                className="chart-card__end-label"
                style={{ fill: lineColor }}
              >
                {s.name}
              </text>
            )}
          </g>
        );
      })}

      {/* per-category crosshair band: keyboard reachable, lists all series */}
      {cats.map((cat, ci) => {
        const bandHandlers = makeHandlers({
          x: x(ci),
          y: M.top,
          rows: series.map((s, si) => ({
            label: `${cat} · ${s.name}`,
            color: categoryColor(si, ci),
            value: fmtNum(s.values[ci] ?? 0),
          })),
        });
        return (
          <rect
            key={cat}
            x={M.left + ci * band}
            y={M.top}
            width={band}
            height={plotH}
            fill="transparent"
            tabIndex={0}
            role="graphics-symbol"
            aria-label={`${cat}：${series.map((s) => `${s.name} ${fmtNum(s.values[ci] ?? 0)}`).join("，")}`}
            onMouseEnter={() => {
              setHovered(ci);
              bandHandlers.onMouseEnter();
            }}
            onMouseLeave={() => {
              clear();
              bandHandlers.onMouseLeave();
            }}
            onFocus={() => {
              setHovered(ci);
              bandHandlers.onFocus();
            }}
            onBlur={() => {
              clear();
              bandHandlers.onBlur();
            }}
          />
        );
      })}

      {cats.map((cat, ci) => (
        <text key={cat} x={x(ci)} y={H - 8} textAnchor="middle" className="chart-card__cat-label">
          {truncateLabel(cat, 14)}
          <title>{cat}</title>
        </text>
      ))}
    </svg>
  );
}

// ---------- pie ----------

const MAX_PIE_SEGMENTS = 5;

type PieSegment = { cat: string; v: number; ci: number; isOther: boolean };

function computePieSegments(spec: ChartSpec): { segments: PieSegment[]; folded: boolean } {
  const s = spec.data.series[0];
  const cats = spec.data.categories;
  if (cats.length <= MAX_PIE_SEGMENTS) {
    return {
      folded: false,
      segments: cats.map((c, i) => ({ cat: c, v: s.values[i] ?? 0, ci: i, isOther: false })),
    };
  }
  // 类别色固定顺序、禁止循环（dataviz 硬规则）：>5 段折叠为 top5 + 灰色"其他"
  const sorted = cats
    .map((c, i) => ({ cat: c, v: s.values[i] ?? 0, ci: i }))
    .sort((a, b) => b.v - a.v);
  const top = sorted.slice(0, MAX_PIE_SEGMENTS).map((x) => ({ ...x, isOther: false }));
  const restSum = sorted.slice(MAX_PIE_SEGMENTS).reduce((acc, x) => acc + x.v, 0);
  return { folded: true, segments: [...top, { cat: "其他", v: restSum, ci: 0, isOther: true }] };
}

function polar(cx: number, cy: number, r: number, deg: number): [number, number] {
  const rad = ((deg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

function arcPath(cx: number, cy: number, r: number, a0: number, a1: number): string {
  const [x0, y0] = polar(cx, cy, r, a0);
  const [x1, y1] = polar(cx, cy, r, a1);
  const large = a1 - a0 > 180 ? 1 : 0;
  return `M ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1} L ${cx} ${cy} Z`;
}

function PiePlot({
  spec,
  makeHandlers,
}: {
  spec: ChartSpec;
  makeHandlers: (show: TooltipState) => HoverHandlers;
}) {
  const { segments } = computePieSegments(spec);
  const total = segments.reduce((acc, x) => acc + x.v, 0);
  const cx = W / 2;
  const cy = H / 2 + 6;
  const r = Math.min(H / 2 - 40, 150);

  let acc = 0;
  return (
    <svg className="chart-card__svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={spec.title}>
      {segments.map((seg) => {
        const { cat, v, ci, isOther } = seg;
        const pct = total > 0 ? v / total : 0;
        const a0 = (acc / (total || 1)) * 360;
        acc += v;
        const a1 = (acc / (total || 1)) * 360;
        const mid = (a0 + a1) / 2;
        const color = isOther ? "var(--chart-other)" : categoryColor(0, ci);
        const showLabel = pct >= 0.08;
        const [lx, ly] = polar(cx, cy, r + 18, mid);
        const [tx, ty] = polar(cx, cy, r * 0.7, mid);
        const segHandlers = makeHandlers({
          x: tx,
          y: ty,
          rows: [{ label: cat, color, value: `${fmtNum(v)}（${(pct * 100).toFixed(1)}%）` }],
        });
        return (
          <g key={cat}>
            <path
              d={arcPath(cx, cy, r, a0, a1)}
              fill={color}
              className="chart-card__pie-seg"
              tabIndex={0}
              role="graphics-symbol"
              aria-label={`${cat} ${fmtNum(v)}（${(pct * 100).toFixed(1)}%）`}
              {...segHandlers}
            />
            {showLabel && (
              <text
                x={lx}
                y={ly}
                textAnchor={lx >= cx ? "start" : "end"}
                dominantBaseline="middle"
                className="chart-card__pie-label"
              >
                {`${truncateLabel(cat, 8)} ${(pct * 100).toFixed(1)}%`}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

// ---------- main component ----------

export default function ChartCard({ spec }: { spec: ChartSpec }) {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  const hide = () => setTooltip(null);

  const makeHandlers = (show: TooltipState): HoverHandlers => ({
    onMouseEnter: () => setTooltip(show),
    onMouseLeave: hide,
    onFocus: () => setTooltip(show),
    onBlur: hide,
  });

  if (!spec.data.categories.length || !spec.data.series.length) {
    return (
      <div className="chart-card">
        <h4 className="chart-card__title">{spec.title}</h4>
        <p className="chart-card__empty">图表数据为空</p>
      </div>
    );
  }

  const series = spec.data.series;
  const pieSegments = spec.chart_type === "pie" ? computePieSegments(spec) : null;
  const showLegend =
    spec.chart_type === "pie" ? pieSegments !== null && pieSegments.folded : series.length >= 2;
  const plot =
    spec.chart_type === "bar" ? (
      <BarPlot spec={spec} makeHandlers={makeHandlers} />
    ) : spec.chart_type === "line" ? (
      <LinePlot spec={spec} makeHandlers={makeHandlers} />
    ) : spec.chart_type === "pie" ? (
      <PiePlot spec={spec} makeHandlers={makeHandlers} />
    ) : (
      <DataTable spec={spec} />
    );

  return (
    <div className="chart-card">
      <h4 className="chart-card__title">{spec.title}</h4>
      {showLegend && (
        <div className="chart-card__legend" role="list">
          {spec.chart_type === "pie"
            ? pieSegments!.segments.map((seg) => (
                <span className="chart-card__legend-item" role="listitem" key={seg.cat}>
                  <span
                    className="chart-card__legend-swatch"
                    style={{
                      background: seg.isOther
                        ? "var(--chart-other)"
                        : categoryColor(0, seg.ci),
                    }}
                  />
                  {seg.cat}
                </span>
              ))
            : series.map((s, i) => (
                <span className="chart-card__legend-item" role="listitem" key={s.name}>
                  <span
                    className="chart-card__legend-swatch"
                    style={{
                      // bar 的类别色横跨整组渐变；line 用组内最深档作代表色
                      background:
                        spec.chart_type === "bar"
                          ? `linear-gradient(90deg, ${seriesGroupRamp(i).join(", ")})`
                          : seriesSolidColor(i),
                    }}
                  />
                  {s.name}
                </span>
              ))}
        </div>
      )}
      <div className="chart-card__plot">
        {plot}
        {tooltip && (
          <div className="chart-card__tooltip" role="status" style={tooltipStyle(tooltip.x, tooltip.y)}>
            {tooltip.rows.map((r) => (
              <div className="chart-card__tooltip-row" key={r.label}>
                <span className="chart-card__tooltip-swatch" style={{ background: r.color }} />
                <span className="chart-card__tooltip-label">{r.label}</span>
                <strong className="chart-card__tooltip-value">{r.value}</strong>
              </div>
            ))}
          </div>
        )}
      </div>
      {spec.chart_type !== "table" && (
        <details className="chart-card__table-toggle">
          <summary>数据详情</summary>
          <DataTable spec={spec} />
        </details>
      )}
    </div>
  );
}
