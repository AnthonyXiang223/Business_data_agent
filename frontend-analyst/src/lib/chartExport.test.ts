import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../ChartCard";
import { buildCsv, exportFileName, serializeSvg } from "./chartExport";

function makeSvg(): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 640 400");
  svg.setAttribute("class", "chart-card__svg");
  const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
  text.setAttribute("class", "chart-card__cat-label");
  text.textContent = "数码";
  const bar = document.createElementNS("http://www.w3.org/2000/svg", "path");
  bar.setAttribute("class", "chart-card__bar");
  bar.setAttribute("fill", "#e8eaf6");
  svg.appendChild(bar);
  svg.appendChild(text);
  return svg;
}

describe("serializeSvg", () => {
  it("serializes with xmlns, explicit size and a white background rect", () => {
    const out = serializeSvg(makeSvg(), { width: 1280, height: 800 });
    expect(out.startsWith("<svg")).toBe(true);
    expect(out).toContain('xmlns="http://www.w3.org/2000/svg"');
    expect(out).toContain('width="1280"');
    expect(out).toContain('height="800"');
    expect(out).toContain('fill="#ffffff"');
    expect(out).toContain("数码");
    expect(out).toContain('fill="#e8eaf6"');
  });

  it("defaults to 640x400 when no size is given", () => {
    const out = serializeSvg(makeSvg());
    expect(out).toContain('width="640"');
    expect(out).toContain('height="400"');
  });

  it("inlines computed styles on every cloned element", () => {
    const out = serializeSvg(makeSvg());
    // 每个克隆元素都应带 style 属性（即使 jsdom 计算值为空串）
    const withStyle = (out.match(/style="/g) ?? []).length;
    expect(withStyle).toBeGreaterThanOrEqual(2);
  });
});

describe("buildCsv", () => {
  const spec: ChartSpec = {
    title: "t",
    chart_type: "table",
    data: {
      categories: ["数码", "服饰,鞋包"],
      series: [{ name: "GMV", values: [120.5, 89] }],
    },
  };

  it("prepends a UTF-8 BOM and builds a header plus one row per series", () => {
    const csv = buildCsv(spec);
    expect(csv.startsWith("﻿")).toBe(true);
    const body = csv.slice(1);
    expect(body).toBe('系列,数码,"服饰,鞋包"\nGMV,120.5,89\n');
  });

  it("doubles quotes inside quoted cells", () => {
    const csv = buildCsv({
      title: "t",
      chart_type: "table",
      data: {
        categories: ['说"明'],
        series: [{ name: "GMV", values: [1] }],
      },
    });
    expect(csv).toContain('"说""明"');
  });
});

describe("exportFileName", () => {
  it("strips filename-illegal characters and falls back for empty titles", () => {
    expect(exportFileName("品类 GMV/a:b", "png")).toBe("品类 GMVab.png");
    expect(exportFileName("   ", "svg")).toBe("chart.svg");
  });
});
