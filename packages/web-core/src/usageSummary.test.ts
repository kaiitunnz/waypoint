import { describe, expect, it } from "vitest";

import type {
  BoardChannel,
  MessageSchedule,
  ProviderUsageDashboardBucket,
  ProviderUsageStatus,
  ScheduledSession,
  SessionUsageDashboardBucket,
  UsageDashboardBucket,
  UsageWindow,
} from "./types";
import {
  accountWindows,
  bucketPeak,
  bucketSourceLabel,
  findUsageWindow,
  formatResetsIn,
  formatShortWhen,
  mostConstrainedWindow,
  orderBucketsWorstFirst,
  overallUsageTone,
  providerHealthLine,
  summarizeChannels,
  summarizeScheduled,
  unresolvedProviderHealth,
} from "./usageSummary";

const NOW = Date.parse("2026-11-24T12:00:00Z");
const window = (overrides: Partial<UsageWindow>): UsageWindow => ({
  id: "w",
  label: "5h",
  used_percent: 10,
  ...overrides,
});

function sessionBucket(
  accountLabel: string,
  windows: UsageWindow[],
): SessionUsageDashboardBucket {
  return {
    origin: "session",
    backend: "claude_code",
    account_key: accountLabel,
    account_label: accountLabel,
    snapshot: { windows },
  } as unknown as SessionUsageDashboardBucket;
}

function providerBucket(providerId: string, stale = false): ProviderUsageDashboardBucket {
  return {
    origin: "provider",
    provider_id: providerId,
    provider_label: "Lumid",
    account_key: providerId,
    account_label: "Lumid",
    snapshot: { windows: [window({ label: "week", used_percent: 1 })] },
    health: { stale },
  } as unknown as ProviderUsageDashboardBucket;
}

const status = (errors: Record<string, number>): ProviderUsageStatus => ({
  provider_id: "lumid",
  provider_type: "lumid",
  provider_label: "Lumid",
  enabled: true,
  stale: false,
  result_counts: {},
  error_counts: errors,
});

describe("usage windows", () => {
  it("finds the 5h window by label or minutes and the weekly one by label, 7d or minutes", () => {
    const bucket = sessionBucket("Max", [
      window({ label: "primary", window_minutes: 300 }),
      window({ label: "weekly cap", window_minutes: null, used_percent: 30 }),
    ]);
    expect(findUsageWindow(bucket, "5h")?.window_minutes).toBe(300);
    expect(findUsageWindow(bucket, "weekly")?.used_percent).toBe(30);
    expect(findUsageWindow(sessionBucket("x", []), "5h")).toBeNull();
    expect(
      findUsageWindow(sessionBucket("x", [window({ label: "x", window_minutes: 10080 })]), "weekly"),
    ).not.toBeNull();
  });

  it("orders accounts worst-first by their peak window", () => {
    const low = sessionBucket("low", [window({ used_percent: 20 })]);
    const high = sessionBucket("high", [window({ used_percent: 20 }), window({ label: "week", used_percent: 80 })]);
    expect(bucketPeak(high)).toBe(80);
    expect(orderBucketsWorstFirst([low, high]).map((b) => b.account_label)).toEqual(["high", "low"]);
  });

  it("labels the source of a bucket", () => {
    expect(bucketSourceLabel(providerBucket("lumid", true), null)).toBe("Lumid · stale");
    expect(bucketSourceLabel(providerBucket("lumid"), null)).toBe("Lumid");
    expect(bucketSourceLabel(sessionBucket("Max", []), null)).toBe("Claude Code");
  });

  it("breaks a tie across accounts for the 5h window, whatever the order", () => {
    const week = sessionBucket("Codex Pro", [window({ label: "week", used_percent: 77 })]);
    const fiveHour = sessionBucket("Claude Max", [window({ label: "5h", used_percent: 77 })]);
    expect(mostConstrainedWindow([week, fiveHour], NOW)).toMatchObject({ label: "5H", accountLabel: "Claude Max" });
    expect(mostConstrainedWindow([fiveHour, week], NOW)).toMatchObject({ label: "5H", accountLabel: "Claude Max" });
  });

  it("picks the most constrained window across accounts, the 5h window on a tie", () => {
    const max = sessionBucket("Claude Max", [
      window({ label: "5h", used_percent: 77, resets_at: new Date(NOW + 72 * 60_000).toISOString() }),
      window({ label: "week", used_percent: 16 }),
    ]);
    const codex = sessionBucket("Codex Pro", [
      window({ label: "5h", used_percent: 24 }),
      window({ label: "week", used_percent: 77 }),
    ]);
    const pick = mostConstrainedWindow([max, codex], NOW);
    expect(pick).toMatchObject({
      accountLabel: "Claude Max",
      label: "5H",
      percent: 77,
      tone: "warn",
      resetsIn: "1h 12m",
    });
    expect(mostConstrainedWindow([])).toBeNull();
    expect(mostConstrainedWindow([sessionBucket("x", [])])).toBeNull();
  });

  it("reads each window an account reports", () => {
    const bucket = sessionBucket("Max", [
      window({ label: "5h", used_percent: 40, resets_at: new Date(NOW + 5 * 60_000).toISOString() }),
      window({ label: "week", used_percent: 95, reset_description: "Mon" }),
    ]);
    expect(accountWindows(bucket, NOW)).toEqual([
      { kind: "5h", label: "5H", percent: 40, tone: "good", resetsIn: "5m" },
      { kind: "weekly", label: "WK", percent: 95, tone: "danger", resetsIn: "Mon" },
    ]);
    expect(accountWindows(sessionBucket("none", []), NOW)).toEqual([]);
  });

  it("says how long until a reset", () => {
    const at = (minutes: number) => new Date(NOW + minutes * 60_000).toISOString();
    expect(formatResetsIn(at(0.2), NOW)).toBe("1m");
    expect(formatResetsIn(at(42), NOW)).toBe("42m");
    expect(formatResetsIn(at(72), NOW)).toBe("1h 12m");
    expect(formatResetsIn(at(120), NOW)).toBe("2h");
    expect(formatResetsIn(at(60 * 28), NOW)).toBe("1d 4h");
    expect(formatResetsIn(at(60 * 48), NOW)).toBe("2d");
    expect(formatResetsIn(at(-5), NOW)).toBeNull();
    expect(formatResetsIn(null, NOW)).toBeNull();
    expect(formatResetsIn("nonsense", NOW)).toBeNull();
  });
});

describe("provider health", () => {
  it("reports the worst error, hard failures first", () => {
    expect(providerHealthLine(status({}))).toBeNull();
    expect(providerHealthLine(status({ usage_unavailable: 2, network: 1 }))).toEqual({
      text: "Lumid · unreachable",
      danger: true,
    });
    expect(providerHealthLine(status({ permission_denied: 1 }))).toEqual({
      text: "Lumid · permission denied",
      danger: false,
    });
    expect(providerHealthLine(status({ weird: 1 }))).toEqual({ text: "Lumid · error", danger: true });
  });

  it("lists only providers without a bucket", () => {
    const buckets: UsageDashboardBucket[] = [providerBucket("lumid")];
    expect(unresolvedProviderHealth(buckets, [status({ network: 1 })])).toEqual([]);
    expect(unresolvedProviderHealth([], [status({ network: 1 })])).toHaveLength(1);
  });

  it("escalates the overall tone", () => {
    const warn = [sessionBucket("a", [window({ used_percent: 75 })])];
    const ok = [sessionBucket("a", [window({ used_percent: 5 })])];
    const crit = [sessionBucket("a", [window({ used_percent: 95 })])];
    expect(overallUsageTone(ok, [])).toBe("good");
    expect(overallUsageTone(warn, [])).toBe("warn");
    expect(overallUsageTone(crit, [])).toBe("danger");
    expect(overallUsageTone(ok, [{ text: "x", danger: false }])).toBe("warn");
    expect(overallUsageTone(ok, [{ text: "x", danger: true }])).toBe("danger");
  });
});

describe("scheduled summary", () => {
  const at = (minutes: number) => new Date(NOW + minutes * 60_000).toISOString();

  it("formats the time until a run", () => {
    expect(formatShortWhen(null)).toBe("queued");
    expect(formatShortWhen("nonsense")).toBe("queued");
    expect(formatShortWhen(at(-1), NOW)).toBe("due");
    expect(formatShortWhen(at(14), NOW)).toBe("in 14m");
    expect(formatShortWhen(at(150), NOW)).toBe("in 2h");
    expect(formatShortWhen(at(60 * 49), NOW)).toBe("in 2d");
  });

  it("merges pending launches and messages soonest first and counts the overflow", () => {
    const launch = (id: string, minutes: number, overrides: Partial<ScheduledSession> = {}) =>
      ({
        id,
        backend: "codex",
        cwd: "/repo",
        status: "pending",
        scheduled_at: at(minutes),
        ...overrides,
      }) as ScheduledSession;
    const message = (id: string, minutes: number | null, overrides: Partial<MessageSchedule> = {}) =>
      ({
        id,
        session_id: "s1",
        text: "Re-run e2e",
        status: "pending",
        scheduled_at: minutes === null ? null : at(minutes),
        ...overrides,
      }) as MessageSchedule;

    const summary = summarizeScheduled(
      [launch("a", 600, { cron: "0 2 * * *", title: "Dependabot sweep" }), launch("done", 1, { status: "launched" })],
      [message("m1", 30), message("m2", null), message("m3", 90, { status: "cancelled" })],
      new Map([["s1", "Fix race"]]),
      2,
      NOW,
    );
    expect(summary.queued).toBe(3);
    expect(summary.overflow).toBe(1);
    expect(summary.items.map((item) => item.key)).toEqual(["message:m1", "launch:a"]);
    expect(summary.items[0]).toMatchObject({ target: "Fix race", whenLabel: "in 30m", kind: "message" });
    expect(summary.items[1]).toMatchObject({ target: "Dependabot sweep", recurring: true, preview: "/repo" });
    expect(summary.total).toBe(5);
  });

  it("labels a when-idle message by its trigger, not its queue time", () => {
    const idle = { id: "m1", session_id: "s1", text: "after", status: "pending", trigger: "idle", scheduled_at: at(-30) } as MessageSchedule;
    const summary = summarizeScheduled([], [idle], new Map(), 3, NOW);
    expect(summary.items[0]).toMatchObject({ whenLabel: "when idle", when: null });
  });
});

describe("channel summary", () => {
  it("counts channels and posts and keeps the most recent few", () => {
    const channels: BoardChannel[] = [
      { channel: "a", entry_count: 2, last_created_at: "2026-11-24T10:00:00Z" },
      { channel: "b", entry_count: 3, last_created_at: "2026-11-24T11:00:00Z" },
      { channel: "c", entry_count: 1, last_created_at: "" },
      { channel: "d", entry_count: 4, last_created_at: "2026-11-23T11:00:00Z" },
    ];
    const summary = summarizeChannels(channels);
    expect(summary).toMatchObject({ channels: 4, posts: 10, lastActivity: "2026-11-24T11:00:00Z" });
    expect(summary.top.map((c) => c.channel)).toEqual(["a", "b", "c"]);
    expect(summarizeChannels([])).toMatchObject({ channels: 0, posts: 0, lastActivity: null });
  });
});
