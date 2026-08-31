import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import FigureCard from "./FigureCard";
import type { ChartSpec } from "./ChartCard";

afterEach(() => {
  cleanup();
});

const SPEC: ChartSpec = {
  title: "品类GMV",
  chart_type: "bar",
  data: {
    categories: ["数码", "服饰"],
    series: [{ name: "GMV", values: [120.5, 89] }],
  },
};

describe("FigureCard", () => {
  it("renders the figure label, id and chart title", () => {
    render(<FigureCard figureKey="fig-1" number={1} spec={SPEC} />);

    expect(screen.getByText("Figure 1")).toBeTruthy();
    expect(document.getElementById("fig-1")).toBeTruthy();
    expect(screen.getByText("品类GMV")).toBeTruthy();
  });

  it("expands the data table on 数据详情 click", () => {
    render(<FigureCard figureKey="fig-1" number={1} spec={SPEC} />);

    const details = screen.getByText("数据详情").closest("details");
    expect(details?.hasAttribute("open")).toBe(false);

    fireEvent.click(screen.getByText("数据详情"));
    expect(details?.hasAttribute("open")).toBe(true);
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getAllByText("数码").length).toBeGreaterThan(0);
  });

  it("renders the figure number passed in", () => {
    render(<FigureCard figureKey="fig-2" number={2} spec={SPEC} />);
    expect(screen.getByText("Figure 2")).toBeTruthy();
  });
});
