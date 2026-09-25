// 展示格式化纯函数：耗时/token/相对时间（无副作用，可单测）。
// 文案全中文；数字走 CSS 层 tabular-nums。

/** 毫秒 → 中文耗时文案：8 秒 / 1 分 20 秒 */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  const totalSec = Math.round(ms / 1000);
  if (totalSec < 60) return `${totalSec} 秒`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min} 分 ${sec} 秒`;
}

/** token 数千分位：formatTokens(1234) → "1,234 tokens" */
export function formatTokens(n: number): string {
  return `${n.toLocaleString("zh-CN")} tokens`;
}

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前 / YYYY-MM-DD（now 可注入便于测试） */
export function formatRelative(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const diff = now - t;
  if (diff < 60_000) return "刚刚";
  const min = Math.floor(diff / 60_000);
  if (min < 60) return `${min} 分钟前`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  const d = new Date(t);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
