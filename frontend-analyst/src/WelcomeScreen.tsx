import { ChartBar, ChartLine, ClockCounterClockwise, Database } from "@phosphor-icons/react";

// 新会话欢迎空态：能力说明 + 示例问题（均基于 ecommerce_data 真实可答，
// 覆盖趋势/占比/客单价/复购四条工具路径）。

const SAMPLE_QUESTIONS = [
  "近 30 天 GMV 趋势如何？画一张折线图",
  "各品类 GMV 占比是多少？画一张饼图",
  "整体客单价是多少？各品类差异如何？",
  "复购率与复购用户的 GMV 贡献如何？",
];

const CAPABILITIES = [
  { icon: Database, title: "数据探查", desc: "自动检查数据质量与指标口径，先探后问" },
  { icon: ChartLine, title: "指标分析", desc: "趋势、下钻、分布对比与相关性分析" },
  { icon: ChartBar, title: "图表输出", desc: "柱状图、折线图、饼图与明细表，可导出 PNG / SVG / CSV" },
  { icon: ClockCounterClockwise, title: "会话记忆", desc: "每次分析独立成会话，随时恢复继续" },
];

export default function WelcomeScreen({
  onAsk,
  disabled,
}: {
  onAsk: (question: string) => void;
  disabled: boolean;
}) {
  return (
    <section className="welcome">
      <p className="welcome__greeting">你好，我是业务数据分析师。</p>
      <p className="welcome__sub">
        连接业务数据集，探查数据、计算指标、输出图表。你可以直接提问，或从下面的示例开始。
      </p>
      <ul className="welcome__caps">
        {CAPABILITIES.map((c) => (
          <li className="welcome__cap" key={c.title}>
            <span className="welcome__cap-iconbox" aria-hidden="true">
              <c.icon className="welcome__cap-icon" size={18} />
            </span>
            <span className="welcome__cap-title">{c.title}</span>
            <span className="welcome__cap-desc">{c.desc}</span>
          </li>
        ))}
      </ul>
      <div className="welcome__chips">
        {SAMPLE_QUESTIONS.map((q) => (
          <button
            className="welcome__chip"
            key={q}
            type="button"
            disabled={disabled}
            onClick={() => onAsk(q)}
          >
            {q}
          </button>
        ))}
      </div>
    </section>
  );
}
