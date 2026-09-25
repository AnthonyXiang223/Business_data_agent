import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import ChartCard, { parseChartSpec, type ChartSpec, visibleLabelIndices } from "./ChartCard";

afterEach(() => {
  cleanup();
});

function spec(overrides: Partial<ChartSpec> = {}): ChartSpec {
  return {
    title: "品类GMV",
    chart_type: "bar",
    data: {
      categories: ["数码", "服饰"],
      series: [{ name: "GMV", values: [120.5, 89] }],
    },
    ...overrides,
  };
}

const BAR_JSON =
  '{"title":"品类GMV","chart_type":"bar","data":{"categories":["数码","服饰"],"series":[{"name":"GMV","values":[120.5,89]},{"name":"销量","values":[30,45]}]}}';

describe("parseChartSpec", () => {
  it("parses a valid spec", () => {
    const parsed = parseChartSpec(BAR_JSON);
    expect(parsed?.title).toBe("品类GMV");
    expect(parsed?.data.series).toHaveLength(2);
  });

  it("returns null for an error string", () => {
    expect(parseChartSpec("[图表校验失败] chart_type 必须是 bar|line|pie|table 之一")).toBeNull();
  });

  it("returns null for invalid JSON", () => {
    expect(parseChartSpec("{not json")).toBeNull();
  });

  it("returns null when required fields are missing", () => {
    expect(parseChartSpec('{"chart_type":"bar","data":{"categories":[],"series":[]}}')).toBeNull();
    expect(parseChartSpec('{"title":"x","chart_type":"scatter","data":{}}')).toBeNull();
  });

  it("filters non-finite values out of a series", () => {
    const parsed = parseChartSpec(
      '{"title":"t","chart_type":"line","data":{"categories":["a","b"],"series":[{"name":"s","values":[1,"NaN",2]}]}}',
    );
    expect(parsed?.data.series[0].values).toEqual([1, 2]);
  });
});

// ---------- bar geometry helpers ----------
// The bar path is hand-rolled SVG; parse its `d` (M/L/Q/h/v/Z only) into
// polyline sample points so the test can assert the shape, not the string.

function pathPoints(d: string): { x: number; y: number }[] {
  const tokens = d.match(/[A-Za-z]|-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/g) ?? [];
  const pts: { x: number; y: number }[] = [];
  let x = 0;
  let y = 0;
  let sx = 0;
  let sy = 0;
  let i = 0;
  const num = () => parseFloat(tokens[i++]);
  while (i < tokens.length) {
    const cmd = tokens[i++];
    if (cmd === "M") {
      x = num();
      y = num();
      sx = x;
      sy = y;
      pts.push({ x, y });
    } else if (cmd === "L") {
      x = num();
      y = num();
      pts.push({ x, y });
    } else if (cmd === "h") {
      x += num();
      pts.push({ x, y });
    } else if (cmd === "v") {
      y += num();
      pts.push({ x, y });
    } else if (cmd === "Q") {
      const cx = num();
      const cy = num();
      const ex = num();
      const ey = num();
      // sample the quadratic (rounded corner) at a few t values
      for (const t of [0.25, 0.5, 0.75]) {
        const a = (1 - t) ** 2;
        const b = 2 * (1 - t) * t;
        const c = t ** 2;
        pts.push({ x: a * x + b * cx + c * ex, y: a * y + b * cy + c * ey });
      }
      x = ex;
      y = ey;
      pts.push({ x, y });
    } else if (cmd === "Z") {
      pts.push({ x: sx, y: sy });
    }
  }
  return pts;
}

/** Assert a bar outline is a rectangle (rounded top corners ok), not a triangle. */
function expectBarRectangular(d: string) {
  const pts = pathPoints(d);
  expect(pts.length).toBeGreaterThan(4);
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const has = (x: number, y: number) =>
    pts.some((p) => Math.hypot(p.x - x, p.y - y) <= 2);
  // All four bounding-box corners must lie on the outline. A path whose final
  // Z closes diagonally to the top-left start point (missing bottom edge)
  // leaves the bottom-left corner empty.
  expect(has(minX, minY), `top-left ${minX},${minY} on outline`).toBe(true);
  expect(has(maxX, minY), `top-right ${maxX},${minY} on outline`).toBe(true);
  expect(has(maxX, maxY), `bottom-right ${maxX},${maxY} on outline`).toBe(true);
  expect(has(minX, maxY), `bottom-left ${minX},${maxY} on outline`).toBe(true);
  // Shoelace area must fill the bounding box (a right triangle fills ~half).
  const w = maxX - minX;
  const h = maxY - minY;
  let area2 = 0;
  for (let k = 0; k < pts.length; k++) {
    const p = pts[k];
    const q = pts[(k + 1) % pts.length];
    area2 += p.x * q.y - q.x * p.y;
  }
  const area = Math.abs(area2) / 2;
  expect(area, `outline area ${area} vs bounding box ${w * h}`).toBeGreaterThan(w * h * 0.85);
}

describe("ChartCard", () => {
  it("renders a bar chart with title, categories and legend for 2+ series", () => {
    render(<ChartCard spec={parseChartSpec(BAR_JSON)!} />);

    expect(screen.getByText("品类GMV")).toBeTruthy();
    expect(screen.getByRole("img")).toBeTruthy();
    const legend = screen.getByRole("list");
    expect(legend.textContent).toContain("GMV");
    expect(legend.textContent).toContain("销量");
    // category name appears both as an SVG x label and a table header (数据表格)
    expect(screen.getAllByText("数码").length).toBeGreaterThan(0);
  });

  it("omits the legend for a single series", () => {
    render(<ChartCard spec={spec()} />);

    expect(screen.queryByRole("list")).toBeNull();
  });

  describe("bar geometry", () => {
    it("draws every bar as a rectangle, not a triangle", () => {
      render(
        <ChartCard
          spec={spec({
            data: {
              categories: ["数码", "服饰"],
              series: [{ name: "GMV", values: [120.5, 89] }],
            },
          })}
        />,
      );

      const bars = document.querySelectorAll<SVGPathElement>("path.chart-card__bar");
      expect(bars.length).toBe(2);
      for (const bar of bars) {
        expectBarRectangular(bar.getAttribute("d") ?? "");
      }
    });

    it("keeps tiny bars rectangular even when one value dwarfs the rest", () => {
      render(
        <ChartCard
          spec={spec({
            data: {
              categories: ["数码", "服饰"],
              series: [{ name: "GMV", values: [1500, 3] }],
            },
          })}
        />,
      );

      const bars = document.querySelectorAll<SVGPathElement>("path.chart-card__bar");
      expect(bars.length).toBe(2);
      for (const bar of bars) {
        expectBarRectangular(bar.getAttribute("d") ?? "");
      }
    });
  });

  it("renders a line chart", () => {
    render(
      <ChartCard
        spec={spec({
          chart_type: "line",
          data: { categories: ["1月", "2月"], series: [{ name: "GMV", values: [1, 2] }] },
        })}
      />,
    );

    expect(screen.getByRole("img")).toBeTruthy();
    expect(screen.getAllByText("1月").length).toBeGreaterThan(0);
  });

  it("renders a pie chart with percentage labels", () => {
    render(
      <ChartCard
        spec={spec({
          chart_type: "pie",
          data: { categories: ["A", "B"], series: [{ name: "占比", values: [60, 40] }] },
        })}
      />,
    );

    expect(screen.getByRole("img")).toBeTruthy();
    expect(screen.getByText("A 60.0%")).toBeTruthy();
    expect(screen.getByText("B 40.0%")).toBeTruthy();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("folds a pie with more than 5 segments into top-5 + 其他 with a legend", () => {
    render(
      <ChartCard
        spec={spec({
          chart_type: "pie",
          data: {
            categories: ["武汉", "广州", "成都", "南京", "深圳", "重庆", "西安"],
            series: [{ name: "销售额", values: [100, 90, 80, 70, 60, 50, 40] }],
          },
        })}
      />,
    );

    const legend = screen.getByRole("list");
    expect(legend.textContent).toContain("其他");
    expect(legend.textContent).toContain("武汉");
    expect(screen.getAllByText(/武汉/).length).toBeGreaterThan(0);
  });

  it("renders a table chart as an HTML table", () => {
    render(
      <ChartCard
        spec={spec({
          chart_type: "table",
          data: { categories: ["列1", "列2"], series: [{ name: "行1", values: [1, 2] }] },
        })}
      />,
    );

    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByText("行1")).toBeTruthy();
  });

  it("shows a tooltip on keyboard focus and hides it on blur", () => {
    render(<ChartCard spec={spec()} />);

    const bar = screen.getAllByRole("graphics-symbol")[0];
    fireEvent.focus(bar);
    const tooltip = screen.getByRole("status");
    expect(tooltip.textContent).toContain("120.5");

    fireEvent.blur(bar);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("renders a placeholder when the spec has no data", () => {
    render(
      <ChartCard spec={{ title: "t", chart_type: "bar", data: { categories: [], series: [] } }} />,
    );

    expect(screen.getByText("图表数据为空")).toBeTruthy();
  });

  it("colors bars per category from the series group ramp (blue then orange)", () => {
    const { container } = render(
      <ChartCard
        spec={{
          title: "t",
          chart_type: "bar",
          data: {
            categories: ["a", "b"],
            series: [
              { name: "s1", values: [10, 20] },
              { name: "s2", values: [5, 15] },
            ],
          },
        }}
      />,
    );

    // DOM 顺序：cat0-s1, cat0-s2, cat1-s1, cat1-s2
    const fills = Array.from(container.querySelectorAll<SVGPathElement>(".chart-card__bar")).map(
      (p) => p.getAttribute("fill"),
    );
    // 同一系列不同类别用不同档位；第二系列换橙组
    expect(fills).toEqual(["#e8eaf6", "#f5d9cb", "#bbcfe8", "#eea78b"]);
  });
});

describe("visibleLabelIndices", () => {
  it("keeps every label when they fit", () => {
    expect(visibleLabelIndices(["数码", "服饰"], 100)).toEqual([0, 1]);
  });

  it("decimates 30 dense date labels and keeps both endpoints", () => {
    const dates = Array.from({ length: 30 }, (_, i) => `2024-12-${String(i + 1).padStart(2, "0")}`);
    const idx = visibleLabelIndices(dates, 564 / 30); // band ≈ 18.8px
    expect(idx[0]).toBe(0);
    expect(idx[idx.length - 1]).toBe(29);
    expect(idx.length).toBeLessThan(dates.length);
    // 严格递增且相邻可见标签放得下（步长至少为 1）
    for (let k = 1; k < idx.length; k++) {
      expect(idx[k]).toBeGreaterThan(idx[k - 1]);
    }
  });

  it("widens the stride until every adjacent pair fits", () => {
    // 8 个全宽 CJK 标签（13px 字号、字宽≈13）、槽宽仅 10px：半宽和 13 > 10-2，
    // 相邻可见标签步长必须 ≥ 2
    const idx = visibleLabelIndices(
      ["一二三四五六七八"].join("").split(""),
      10,
    );
    for (let k = 1; k < idx.length; k++) {
      expect(idx[k] - idx[k - 1]).toBeGreaterThanOrEqual(2);
    }
    expect(idx[0]).toBe(0);
    expect(idx[idx.length - 1]).toBe(7); // 端点优先保留
  });

  it("returns empty for no categories", () => {
    expect(visibleLabelIndices([], 20)).toEqual([]);
  });
});

describe("dense category labels", () => {
  const dates = Array.from({ length: 30 }, (_, i) => `2024-12-${String(i + 1).padStart(2, "0")}`);
  const denseSpec = (chart_type: "bar" | "line"): ChartSpec => ({
    title: "近 30 天 GMV 趋势",
    chart_type,
    data: { categories: dates, series: [{ name: "GMV", values: dates.map((_, i) => 100 + i) }] },
  });

  it("decimates 30 date labels on a line chart, keeping both endpoints horizontal", () => {
    const { container } = render(<ChartCard spec={denseSpec("line")} />);
    const labels = Array.from(container.querySelectorAll(".chart-card__cat-label"));
    expect(labels.length).toBeLessThan(dates.length);
    expect(labels.length).toBeGreaterThan(2);
    // 首尾日期保留；所有标签水平（无 transform 旋转）
    expect(labels[0].textContent).toContain("2024-12-01");
    expect(labels[labels.length - 1].textContent).toContain("2024-12-30");
    for (const l of labels) expect(l.getAttribute("transform")).toBeNull();
    // 被抽稀的槽位保留短刻度
    const ticks = container.querySelectorAll(".chart-card__svg line.chart-card__axis");
    expect(ticks.length).toBeGreaterThan(0);
  });

  it("decimates 30 date labels on a bar chart the same way", () => {
    const { container } = render(<ChartCard spec={denseSpec("bar")} />);
    const labels = Array.from(container.querySelectorAll(".chart-card__cat-label"));
    expect(labels.length).toBeLessThan(dates.length);
    for (const l of labels) expect(l.getAttribute("transform")).toBeNull();
  });
});

describe("export menu", () => {
  it("offers PNG and SVG for chart types, CSV only for tables", () => {
    const { rerender } = render(<ChartCard spec={spec()} />);
    fireEvent.click(screen.getByLabelText("导出图表"));
    expect(screen.getByText("导出 PNG")).toBeTruthy();
    expect(screen.getByText("导出 SVG")).toBeTruthy();
    expect(screen.queryByText("导出 CSV")).toBeNull();

    // 点击菜单外部关闭
    fireEvent.mouseDown(document.body);
    expect(screen.queryByText("导出 PNG")).toBeNull();

    rerender(
      <ChartCard
        spec={spec({
          chart_type: "table",
          data: { categories: ["列1"], series: [{ name: "行1", values: [1] }] },
        })}
      />,
    );
    fireEvent.click(screen.getByLabelText("导出图表"));
    expect(screen.getByText("导出 CSV")).toBeTruthy();
    expect(screen.queryByText("导出 PNG")).toBeNull();
  });

  it("closes the menu after choosing an export", () => {
    render(<ChartCard spec={spec()} />);
    fireEvent.click(screen.getByLabelText("导出图表"));
    fireEvent.click(screen.getByText("导出 SVG"));
    expect(screen.queryByText("导出 SVG")).toBeNull();
  });

  it("does not show the export entry for empty data", () => {
    render(
      <ChartCard
        spec={{ title: "t", chart_type: "bar", data: { categories: [], series: [] } }}
      />,
    );
    expect(screen.queryByLabelText("导出图表")).toBeNull();
  });
});
