import { describe, expect, it } from "vitest";
import { formatDuration, formatRelative, formatTokens } from "./format";

describe("formatDuration", () => {
  it("formats seconds and minutes", () => {
    expect(formatDuration(5000)).toBe("5 秒");
    expect(formatDuration(83000)).toBe("1 分 23 秒");
    expect(formatDuration(0)).toBe("0 秒");
  });

  it("returns empty for invalid input", () => {
    expect(formatDuration(-1)).toBe("");
    expect(formatDuration(Number.NaN)).toBe("");
  });
});

describe("formatTokens", () => {
  it("groups thousands", () => {
    expect(formatTokens(1234)).toBe("1,234 tokens");
    expect(formatTokens(0)).toBe("0 tokens");
  });
});

describe("formatRelative", () => {
  const now = Date.parse("2026-09-14T12:00:00Z");

  it("formats recency buckets", () => {
    expect(formatRelative("2026-09-14T11:59:40Z", now)).toBe("刚刚");
    expect(formatRelative("2026-09-14T11:55:00Z", now)).toBe("5 分钟前");
    expect(formatRelative("2026-09-14T10:00:00Z", now)).toBe("2 小时前");
    expect(formatRelative("2026-09-11T12:00:00Z", now)).toBe("3 天前");
  });

  it("falls back to a date string beyond 30 days", () => {
    expect(formatRelative("2026-08-01T00:00:00Z", now)).toBe("2026-08-01");
  });

  it("returns empty for invalid input", () => {
    expect(formatRelative("not-a-date", now)).toBe("");
  });
});
