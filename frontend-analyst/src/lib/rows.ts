// 消息 → 界面行映射（纯函数，无副作用）。
// 业务界面只呈现三类内容：用户提问、AI 最终回答、图表（Figure）。
// 全部工具调用过程对用户不可见——唯一例外是 create_chart 的成功产物。

import { parseChartSpec, type ChartSpec } from "../ChartCard";

export type RawToolCall = { id?: string; name?: string; args?: unknown };
export type Message = {
  id?: string;
  type: string;
  content: unknown;
  tool_calls?: RawToolCall[];
  tool_call_id?: string;
  name?: string;
};

export type ProseRow = { kind: "prose"; key: string; role: "human" | "ai"; body: string };
export type FigureRow = { kind: "figure"; key: string; number: number; spec: ChartSpec };
export type Row = ProseRow | FigureRow;

/** content 可能是字符串或内容块数组（langgraph messages 流的两种形态）。 */
export function messageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) =>
        typeof part === "string"
          ? part
          : typeof part === "object" && part !== null && "text" in part
            ? String((part as { text: unknown }).text ?? "")
            : "",
      )
      .join("");
  }
  return "";
}

export function buildRows(messages: Message[]): Row[] {
  const rows: Row[] = [];
  // 图编号是纯函数内的局部计数器：同一 messages 输入永远产出同一编号序列，
  // 不依赖 React ref / 模块状态，流式更新与 StrictMode 双渲染下都稳定。
  let figureNumber = 0;
  for (const msg of messages) {
    if (msg.type === "human") {
      const body = messageText(msg.content).trim();
      if (body) {
        rows.push({ kind: "prose", key: msg.id ?? `h-${rows.length}`, role: "human", body });
      }
    } else if (msg.type === "ai") {
      // tool_calls 数组本身永不渲染；空文本的携带消息不产生任何行
      //（create_chart 的调用载体消息就这样被静默吞掉，图在工具结果到达时出现）。
      const body = messageText(msg.content).trim();
      if (body) {
        rows.push({ kind: "prose", key: msg.id ?? `a-${rows.length}`, role: "ai", body });
      }
    } else if (msg.type === "tool") {
      if (msg.name !== "create_chart") continue; // 其余工具流量全不可见
      const spec = parseChartSpec(messageText(msg.content));
      if (!spec) continue; // 解析失败（校验错误文本/流式半截 JSON）→ 静默忽略
      figureNumber += 1;
      rows.push({
        kind: "figure",
        key: `fig-${msg.tool_call_id ?? rows.length}`, // tool_call_id 是稳定身份
        number: figureNumber,
        spec,
      });
    }
  }
  return rows;
}

export function isFigureRow(row: Row): row is FigureRow {
  return row.kind === "figure";
}
