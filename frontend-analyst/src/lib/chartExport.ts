// 图表导出：手写 SVG → PNG/SVG 下载，table 型 → CSV。
// 策略：克隆 DOM 里的 SVG 节点，把计算样式内联进克隆（脱离文档后
// styles.css 的类与 CSS 变量全部失效，不内联导出图会丢字体/颜色），
// 补白底 rect 后 XMLSerializer 序列化。PNG 走 Image → canvas 2x 栅格化。
// jsdom 无 canvas.toBlob/Image 加载：纯函数（serializeSvg/buildCsv/
// exportFileName）可单测，下载胶水不测。

import type { ChartSpec } from "../ChartCard";

const PNG_W = 1280;
const PNG_H = 800;

/** 需从计算样式内联到克隆节点的属性（字体与笔刷；几何由节点属性自带） */
const STYLE_PROPS = [
  "font-family",
  "font-size",
  "font-weight",
  "fill",
  "stroke",
  "stroke-width",
  "fill-opacity",
  "stroke-opacity",
  "opacity",
] as const;

/** 克隆 SVG → 内联计算样式 → 序列化为独立可显示的 SVG 字符串（纯函数）。 */
export function serializeSvg(
  svg: SVGSVGElement,
  size?: { width: number; height: number },
): string {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", String(size?.width ?? 640));
  clone.setAttribute("height", String(size?.height ?? 400));
  const originals = Array.from(svg.querySelectorAll<SVGElement>("*"));
  const clones = Array.from(clone.querySelectorAll<SVGElement>("*"));
  originals.forEach((el, i) => {
    if (!clones[i]) return;
    const cs = getComputedStyle(el);
    clones[i].setAttribute(
      "style",
      STYLE_PROPS.map((p) => `${p}:${cs.getPropertyValue(p)}`).join(";"),
    );
  });
  // 白纸底色：SVG 查看器默认透明底，导出图必须补白
  const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  bg.setAttribute("width", "100%");
  bg.setAttribute("height", "100%");
  bg.setAttribute("fill", "#ffffff");
  clone.insertBefore(bg, clone.firstChild);
  return new XMLSerializer().serializeToString(clone);
}

/** table 型 spec → CSV 字符串（UTF-8 BOM，Excel 中文不乱码；纯函数）。 */
export function buildCsv(spec: ChartSpec): string {
  const escape = (cell: string) =>
    /[",\n]/.test(cell) ? `"${cell.replace(/"/g, '""')}"` : cell;
  const header = ["系列", ...spec.data.categories].map(escape).join(",");
  const rows = spec.data.series.map((s) =>
    [s.name, ...s.values.map((v) => String(v))].map(escape).join(","),
  );
  return "\uFEFF" + [header, ...rows].join("\n") + "\n";
}

/** 标题消毒 + 扩展名（文件名非法字符剔除，空标题回退 "chart"）。 */
export function exportFileName(title: string, ext: "png" | "svg" | "csv"): string {
  const clean = title.replace(/[\\/:*?"<>|]/g, "").trim();
  return `${clean || "chart"}.${ext}`;
}

function downloadBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** PNG 导出：SVG 串 → Image → canvas 2x 栅格化 → 下载（jsdom 不测）。 */
export async function exportPng(svg: SVGSVGElement, fileName: string): Promise<void> {
  try {
    // 等 Geist 等自托管字体加载完再栅格化，否则回退字体栈
    await (document as Document & { fonts?: { ready: Promise<unknown> } }).fonts?.ready;
  } catch {
    // jsdom / 旧浏览器无 fonts API：直接用当前字体栈
  }
  const svgStr = serializeSvg(svg, { width: PNG_W, height: PNG_H });
  const blob = new Blob([svgStr], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const img = new Image();
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error("导出失败：无法栅格化图表"));
      img.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = PNG_W;
    canvas.height = PNG_H;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("导出失败：无法创建画布");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, PNG_W, PNG_H);
    ctx.drawImage(img, 0, 0, PNG_W, PNG_H);
    canvas.toBlob((b) => {
      if (b) downloadBlob(b, fileName);
    }, "image/png");
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** SVG 导出：序列化后直接下载。 */
export function exportSvg(svg: SVGSVGElement, fileName: string): void {
  downloadBlob(new Blob([serializeSvg(svg)], { type: "image/svg+xml;charset=utf-8" }), fileName);
}

/** CSV 导出（table 型图表）。 */
export function downloadCsv(spec: ChartSpec, fileName: string): void {
  downloadBlob(new Blob([buildCsv(spec)], { type: "text/csv;charset=utf-8" }), fileName);
}
