import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import FigureCard from "./FigureCard";
import type { Row } from "./lib/rows";

export default function MessageFlow({
  rows,
  loading,
  error,
}: {
  rows: Row[];
  loading: boolean;
  error: string | null;
}) {
  return (
    <section className="message-flow" aria-label="分析会话">
      {rows.length === 0 && !loading && (
        <p className="message-flow__hint">
          试试：分析 ecommerce_data 各品类 GMV，画一张品类 GMV 对比柱状图。
        </p>
      )}
      {rows.map((row) =>
        row.kind === "prose" ? (
          <article key={row.key} className={`msg msg--${row.role}`}>
            {row.role === "human" ? (
              <>
                <p className="msg__label">问题</p>
                <div className="msg__body">{row.body}</div>
              </>
            ) : (
              <div className="msg__body">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    // Agent 没有图片工具，任何图片链接都是幻觉产物；
                    // 图表只经 create_chart 工具走 FigureCard 渲染
                    img: () => null,
                  }}
                >
                  {row.body}
                </ReactMarkdown>
              </div>
            )}
          </article>
        ) : (
          <FigureCard key={row.key} figureKey={row.key} number={row.number} spec={row.spec} />
        ),
      )}
      {loading && (
        <p className="message-flow__loading" aria-live="polite">
          分析中
        </p>
      )}
      {error && <p className="message-flow__error">{error}</p>}
    </section>
  );
}
