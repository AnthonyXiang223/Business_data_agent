import { describe, expect, it } from "vitest";
import { buildRows, messageText, type Message } from "./rows";

const CHART_JSON =
  '{"title":"品类GMV","chart_type":"bar","data":{"categories":["数码","服饰"],"series":[{"name":"GMV","values":[120.5,89]}]}}';

describe("messageText", () => {
  it("flattens string and content-block arrays", () => {
    expect(messageText("abc")).toBe("abc");
    expect(messageText([{ type: "text", text: "a" }, "b"])).toBe("ab");
    expect(messageText(null)).toBe("");
  });
});

describe("buildRows", () => {
  it("maps human messages to prose rows", () => {
    const rows = buildRows([{ id: "h1", type: "human", content: " 分析 GMV " }]);
    expect(rows).toEqual([{ kind: "prose", key: "h1", role: "human", body: "分析 GMV" }]);
  });

  it("maps ai text to prose rows", () => {
    const rows = buildRows([{ id: "a1", type: "ai", content: "结论如下" }]);
    expect(rows[0]).toEqual({ kind: "prose", key: "a1", role: "ai", body: "结论如下" });
  });

  it("renders nothing for an ai message that only carried tool calls", () => {
    const rows = buildRows([
      { id: "a1", type: "ai", content: "", tool_calls: [{ id: "c1", name: "create_chart" }] },
    ]);
    expect(rows).toHaveLength(0);
  });

  it("renders narration text but no cards for an ai message with tool calls and text", () => {
    const rows = buildRows([
      {
        id: "a1",
        type: "ai",
        content: "我先查一下各品类 GMV。",
        tool_calls: [{ id: "c1", name: "query_data", args: {} }],
      },
    ]);
    expect(rows).toEqual([{ kind: "prose", key: "a1", role: "ai", body: "我先查一下各品类 GMV。" }]);
  });

  it("extracts a valid create_chart tool result as figure number 1", () => {
    const msgs: Message[] = [
      { id: "a1", type: "ai", content: "", tool_calls: [{ id: "c1", name: "create_chart" }] },
      { type: "tool", tool_call_id: "c1", name: "create_chart", content: CHART_JSON },
    ];
    const rows = buildRows(msgs);
    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe("figure");
    if (rows[0].kind === "figure") {
      expect(rows[0].number).toBe(1);
      expect(rows[0].key).toBe("fig-c1");
      expect(rows[0].spec.title).toBe("品类GMV");
    }
  });

  it("numbers figures in stream order", () => {
    const msgs: Message[] = [
      { type: "tool", tool_call_id: "c1", name: "create_chart", content: CHART_JSON },
      {
        type: "tool",
        tool_call_id: "c2",
        name: "create_chart",
        content: CHART_JSON.replace("品类GMV", "销量占比"),
      },
    ];
    const rows = buildRows(msgs);
    const figures = rows.filter((r) => r.kind === "figure");
    expect(figures.map((r) => (r.kind === "figure" ? r.number : 0))).toEqual([1, 2]);
  });

  it("ignores a create_chart result that is not a valid spec", () => {
    const msgs: Message[] = [
      { type: "tool", tool_call_id: "c1", name: "create_chart", content: "[图表校验失败] chart_type 非法" },
      { type: "tool", tool_call_id: "c2", name: "create_chart", content: '{"title":1' },
    ];
    expect(buildRows(msgs)).toHaveLength(0);
  });

  it("ignores all non-chart tool traffic", () => {
    const msgs: Message[] = [
      { type: "tool", tool_call_id: "c1", name: "query_data", content: "查询结果: ..." },
      { type: "tool", tool_call_id: "c2", name: "get_data_profile", content: "画像..." },
      { type: "tool", tool_call_id: "c3", name: "write_todos", content: "updated" },
    ];
    expect(buildRows(msgs)).toHaveLength(0);
  });

  it("falls back to an index-based key when tool_call_id is missing", () => {
    const rows = buildRows([{ type: "tool", name: "create_chart", content: CHART_JSON }]);
    expect(rows[0].key).toBe("fig-0");
  });

  it("skips empty human content", () => {
    expect(buildRows([{ id: "h1", type: "human", content: "   " }])).toHaveLength(0);
  });

  it("places a figure between the question and the final answer", () => {
    const msgs: Message[] = [
      { id: "h1", type: "human", content: "画一张 GMV 柱状图" },
      { id: "a1", type: "ai", content: "", tool_calls: [{ id: "c1", name: "create_chart" }] },
      { type: "tool", tool_call_id: "c1", name: "create_chart", content: CHART_JSON },
      { id: "a2", type: "ai", content: "图表已生成，各品类 GMV 如上。" },
    ];
    const rows = buildRows(msgs);
    expect(rows.map((r) => r.kind)).toEqual(["prose", "figure", "prose"]);
  });

  it("passes run meta and stopped through to the ai prose row", () => {
    const rows = buildRows([
      {
        id: "a1",
        type: "ai",
        content: "结论",
        meta: { toolCalls: 2, tokens: 100, durationMs: 5000 },
        stopped: true,
      },
    ]);
    expect(rows).toEqual([
      {
        kind: "prose",
        key: "a1",
        role: "ai",
        body: "结论",
        meta: { toolCalls: 2, tokens: 100, durationMs: 5000 },
        stopped: true,
      },
    ]);
  });

  it("renders a minimal row for a stopped ai message with empty content", () => {
    const rows = buildRows([{ id: "a1", type: "ai", content: "", stopped: true }]);
    expect(rows).toEqual([{ kind: "prose", key: "a1", role: "ai", body: "", stopped: true }]);
  });
});
