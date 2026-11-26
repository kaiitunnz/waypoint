import { describe, expect, it } from "vitest";

import type { HeldMessage, MessageSchedule } from "../types";
import { buildMessageQueue, heldCount, queueItemView, queueStripItem } from "./messageQueue";

const held = (id: string, overrides: Partial<HeldMessage> = {}): HeldMessage => ({
  id,
  session_id: "s1",
  origin: "schedule",
  text: `held ${id}`,
  attachments: [],
  created_at: new Date().toISOString(),
  hold_reason: "idle",
  ...overrides,
});

const scheduled = (id: string, overrides: Partial<MessageSchedule> = {}): MessageSchedule => ({
  id,
  session_id: "s1",
  text: `scheduled ${id}`,
  submit: true,
  status: "pending",
  created_at: "2026-01-01T00:00:00Z",
  scheduled_at: new Date(Date.now() + 60 * 60_000).toISOString(),
  ...overrides,
});

describe("buildMessageQueue", () => {
  it("lists held messages in arrival order, then pending schedules soonest first", () => {
    const later = scheduled("late", { scheduled_at: new Date(Date.now() + 2 * 3600_000).toISOString() });
    const sooner = scheduled("soon", { scheduled_at: new Date(Date.now() + 600_000).toISOString() });
    const done = scheduled("done", { status: "sent" });
    const queue = buildMessageQueue([held("a"), held("b")], [later, sooner, done]);
    expect(queue.map((item) => item.id)).toEqual(["a", "b", "soon", "late"]);
    expect(heldCount(queue)).toBe(2);
  });
});

describe("queueStripItem", () => {
  it("prefers the latest held message", () => {
    expect(queueStripItem(buildMessageQueue([held("a"), held("b")], [scheduled("s")]))?.id).toBe("b");
  });

  it("falls back to the next schedule, or nothing", () => {
    expect(queueStripItem(buildMessageQueue([], [scheduled("s")]))?.id).toBe("s");
    expect(queueStripItem([])).toBeNull();
  });
});

describe("queueItemView", () => {
  it("describes a held message with its hold reason and actions", () => {
    const [item] = buildMessageQueue([held("a", { hold_reason: "focus", attachments: ["f"] })], []);
    const view = queueItemView(item, { focus: true });
    expect(view.origin).toBe("Scheduled");
    expect(view.reason).toBe("Held by Focus");
    expect(view.manual).toBe(true);
    expect(view.text).toBe("held a (+1 file)");
    expect(view.actions).toEqual(["release", "cancel"]);
  });

  it("describes a recurring schedule with its cadence and actions", () => {
    const [item] = buildMessageQueue([], [scheduled("s", { cron: "0 9 * * *", timezone: "UTC" })]);
    const view = queueItemView(item, { focus: false });
    expect(view.origin).toBe("Scheduled");
    expect(view.cadence).toBeTruthy();
    expect(view.when).toMatch(/^in /);
    expect(view.actions).toEqual(["sendNow", "cancel"]);
  });

  it("reads an idle schedule as sending when idle", () => {
    const [item] = buildMessageQueue([], [scheduled("s", { trigger: "idle" })]);
    const view = queueItemView(item, { focus: false });
    expect(view.when).toBe("When idle");
    expect(view.reason).toBeNull();
  });
});
