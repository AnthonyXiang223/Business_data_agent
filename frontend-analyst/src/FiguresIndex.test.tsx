import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import FiguresIndex from "./FiguresIndex";
import type { FigureRow } from "./lib/rows";

afterEach(() => {
  cleanup();
});

const FIGURES: FigureRow[] = [
  {
    kind: "figure",
    key: "fig-1",
    number: 1,
    spec: {
      title: "品类GMV",
      chart_type: "bar",
      data: { categories: ["a"], series: [{ name: "s", values: [1] }] },
    },
  },
  {
    kind: "figure",
    key: "fig-2",
    number: 2,
    spec: {
      title: "销量占比",
      chart_type: "pie",
      data: { categories: ["a"], series: [{ name: "s", values: [1] }] },
    },
  },
];

describe("FiguresIndex", () => {
  it("lists figures with numbers and titles", () => {
    render(<FiguresIndex figures={FIGURES} />);

    expect(screen.getByText("Figure 1")).toBeTruthy();
    expect(screen.getByText("品类GMV")).toBeTruthy();
    expect(screen.getByText("Figure 2")).toBeTruthy();
    expect(screen.getByText("销量占比")).toBeTruthy();
  });

  it("links each entry to its figure anchor", () => {
    render(<FiguresIndex figures={FIGURES} />);

    const link = screen.getByText("品类GMV").closest("a");
    expect(link?.getAttribute("href")).toBe("#fig-1");
  });

  it("shows the empty state", () => {
    render(<FiguresIndex figures={[]} />);
    expect(screen.getByText("暂无图表")).toBeTruthy();
  });

  it("grows when figures grow", () => {
    const { rerender } = render(<FiguresIndex figures={[FIGURES[0]]} />);
    expect(screen.queryByText("Figure 2")).toBeNull();

    rerender(<FiguresIndex figures={FIGURES} />);
    expect(screen.getByText("Figure 2")).toBeTruthy();
  });
});
