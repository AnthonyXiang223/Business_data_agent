import { useEffect, useMemo, useRef, useState } from "react";
import { DownloadSimple } from "@phosphor-icons/react";
import { downloadCsv, exportFileName, exportPng, exportSvg } from "./lib/chartExport";

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
const FONT = 13; // 与 styles.css 的 --fs-small 保持一致（标签宽度估算的基准字号）

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

/** 横轴标签抽稀：返回应显示的类别下标（保持水平、绝不重叠、绝不旋转）。
 * 相邻可见标签的间距须容纳两者半宽之和（预留 2px 间隙）；步长从 1 起
 * 逐级放宽直到全部相邻对都放得下；首尾标签优先保留（时间序列端点有意义）。
 * 导出的纯函数，单测覆盖。 */
export function visibleLabelIndices(cats: string[], slotW: number, maxChars = 14): number[] {
  const n = cats.length;
  if (n === 0) return [];
  const widths = cats.map((c) => estLabelWidth(truncateLabel(c, maxChars), FONT));
  const fits = (a: number, b: number) =>
    (widths[a] + widths[b]) / 2 <= Math.abs(a - b) * slotW - 2;
  let stride = 1;
  while (stride < n) {
    let ok = true;
    for (let i = stride; i < n; i += stride) {
      if (!fits(i, i - stride)) {
        ok = false;
        break;
      }
    }
    if (ok) break;
    stride++;
  }
  const out: number[] = [];
  for (let i = 0; i < n; i += stride) out.push(i);
  // 端点优先（时间序列最后一天有意义）：放不下时依次让出前一个可见标签
  while (out.length > 1 && out[out.length - 1] !== n - 1) {
    if (fits(n - 1, out[out.length - 1])) {
      out.push(n - 1);
      break;
    }
    out.pop();
  }
  if (out.length === 1 && out[0] !== n - 1 && fits(n - 1, out[0])) out.push(n - 1);
  return out;
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
    // 滚动容器：宽表（多类别×长值）的最小内容宽度会撑破卡片/界面，
    // 包一层 overflow-x 让它超出时横向滚动而不是溢出
    <div className="chart-card__table-wrap">
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
    </div>
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
  // 横轴标签抽稀：保持水平，放不下的下标跳过（旋转标签已废弃——禁止斜放/重叠）
  const labelIdxs = new Set(visibleLabelIndices(cats, band));
  const bottom = 30;
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
        const labelY = H - 4;
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
            {labelIdxs.has(ci) ? (
              <text x={xPos} y={labelY} textAnchor="middle" className="chart-card__cat-label">
                {truncateLabel(cat, 14)}
                <title>{cat}</title>
              </text>
            ) : (
              // 被抽稀的槽位保留短刻度，轴位仍可对齐
              <line
                x1={xPos}
                x2={xPos}
                y1={M.top + plotH}
                y2={M.top + plotH + 5}
                className="chart-card__axis"
              />
            )}
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
  // 横轴标签抽稀：30 个日期等密集类别全部渲染必然重叠，间隔显示 + 短刻度
  const labelIdxs = new Set(visibleLabelIndices(cats, band));
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

      {cats.map((cat, ci) =>
        labelIdxs.has(ci) ? (
          <text key={cat} x={x(ci)} y={H - 8} textAnchor="middle" className="chart-card__cat-label">
            {truncateLabel(cat, 14)}
            <title>{cat}</title>
          </text>
        ) : (
          <line
            key={cat}
            x1={x(ci)}
            x2={x(ci)}
            y1={M.top + plotH}
            y2={M.top + plotH + 5}
            className="chart-card__axis"
          />
        ),
      )}
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
  const [exportOpen, setExportOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const hide = () => setTooltip(null);

  const makeHandlers = (show: TooltipState): HoverHandlers => ({
    onMouseEnter: () => setTooltip(show),
    onMouseLeave: hide,
    onFocus: () => setTooltip(show),
    onBlur: hide,
  });

  // 导出菜单：外部点击 / Escape 关闭
  useEffect(() => {
    if (!exportOpen) return;
    function onDocDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setExportOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setExportOpen(false);
    }
    document.addEventListener("mousedown", onDocDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [exportOpen]);

  function handleExport(kind: "png" | "svg" | "csv") {
    if (kind === "csv") {
      downloadCsv(spec, exportFileName(spec.title, "csv"));
    } else {
      const svg = rootRef.current?.querySelector<SVGSVGElement>("svg.chart-card__svg");
      if (!svg) return;
      const name = exportFileName(spec.title, kind);
      if (kind === "png") void exportPng(svg, name);
      else exportSvg(svg, name);
    }
    setExportOpen(false);
  }

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
    <div className="chart-card" ref={rootRef}>
      <div className="chart-card__head">
        <h4 className="chart-card__title">{spec.title}</h4>
        <div className="chart-card__export">
          <button
            className="chart-card__export-toggle"
            type="button"
            aria-label="导出图表"
            aria-expanded={exportOpen}
            onClick={() => setExportOpen((v) => !v)}
          >
            <DownloadSimple size={14} />
          </button>
          {exportOpen && (
            <div className="chart-card__export-menu" role="menu">
              {spec.chart_type === "table" ? (
                <button
                  className="chart-card__export-item"
                  type="button"
                  role="menuitem"
                  onClick={() => handleExport("csv")}
                >
                  导出 CSV
                </button>
              ) : (
                <>
                  <button
                    className="chart-card__export-item"
                    type="button"
                    role="menuitem"
                    onClick={() => handleExport("png")}
                  >
                    导出 PNG
                  </button>
                  <button
                    className="chart-card__export-item"
                    type="button"
                    role="menuitem"
                    onClick={() => handleExport("svg")}
                  >
                    导出 SVG
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>
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
