import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import WelcomeScreen from "./WelcomeScreen";

afterEach(() => {
  cleanup();
});

describe("WelcomeScreen", () => {
  it("renders the greeting and all four capabilities", () => {
    render(<WelcomeScreen onAsk={vi.fn()} disabled={false} />);

    expect(screen.getByText("你好，我是业务数据分析师。")).toBeTruthy();
    expect(screen.getByText("数据探查")).toBeTruthy();
    expect(screen.getByText("指标分析")).toBeTruthy();
    expect(screen.getByText("图表输出")).toBeTruthy();
    expect(screen.getByText("会话记忆")).toBeTruthy();
  });

  it("renders all four sample questions", () => {
    render(<WelcomeScreen onAsk={vi.fn()} disabled={false} />);

    expect(screen.getByText("近 30 天 GMV 趋势如何？画一张折线图")).toBeTruthy();
    expect(screen.getByText("各品类 GMV 占比是多少？画一张饼图")).toBeTruthy();
    expect(screen.getByText("整体客单价是多少？各品类差异如何？")).toBeTruthy();
    expect(screen.getByText("复购率与复购用户的 GMV 贡献如何？")).toBeTruthy();
  });

  it("fires onAsk with the full question text", () => {
    const onAsk = vi.fn();
    render(<WelcomeScreen onAsk={onAsk} disabled={false} />);

    fireEvent.click(screen.getByText("近 30 天 GMV 趋势如何？画一张折线图"));
    expect(onAsk).toHaveBeenCalledWith("近 30 天 GMV 趋势如何？画一张折线图");
  });
});
