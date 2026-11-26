import { describe, expect, it } from "vitest";

import { buildCatalog } from "./backends";
import { failureText, launchRowView, messageRowView, sortScheduled } from "./scheduledSheet";
import { launchCatalogDescriptors } from "./test-helpers";
import type { MessageSchedule, ScheduledSession } from "./types";

const catalog = buildCatalog(launchCatalogDescriptors());
const now = new Date(2026, 10, 26, 12, 0); // Thu 26 Nov, local time
const local = (day: number, hour: number, minute = 0) => new Date(2026, 10, day, hour, minute);
const iso = (date: Date) => date.toISOString();

const launch = (fields: Partial<ScheduledSession>): ScheduledSession =>
  ({
    id: "s1",
    backend: "claude_code",
    cwd: "/home/me/waypoint",
    launch_mode: "auto",
    args: [],
    scheduled_at: iso(local(26, 19, 18)),
    created_at: iso(local(25, 9)),
    status: "pending",
    ...fields,
  }) as ScheduledSession;

const message = (fields: Partial<MessageSchedule>): MessageSchedule =>
  ({
    id: "m1",
    session_id: "claude_code-abc123",
    text: "Re-run the e2e suite",
    submit: true,
    scheduled_at: iso(local(26, 14, 41)),
    created_at: iso(local(26, 9)),
    status: "pending",
    ...fields,
  }) as MessageSchedule;

describe("failureText", () => {
  it("reads an HTTP refusal as words and keeps other reasons", () => {
    expect(failureText("400: cwd-not-found")).toBe("folder not found");
    expect(failureText("409: ssh-master-required")).toBe("SSH password required");
    expect(failureText("session not found")).toBe("session not found");
    expect(failureText(null)).toBe("unknown error");
  });
});

describe("sortScheduled", () => {
  it("puts pending first, soonest first and when-idle last, then history newest first", () => {
    const rows = [
      message({ id: "old", status: "sent", scheduled_at: iso(local(20, 9)) }),
      message({ id: "idle", trigger: "idle" }),
      message({ id: "late", scheduled_at: iso(local(27, 9)) }),
      message({ id: "new", status: "failed", scheduled_at: iso(local(25, 9)) }),
      message({ id: "soon" }),
    ];
    expect(sortScheduled(rows).map((row) => row.id)).toEqual(["soon", "late", "idle", "new", "old"]);
  });

  it("places a cancelled record by when it was made and a past recurrence by its last run", () => {
    const rows = [
      message({ id: "far", status: "cancelled", created_at: iso(local(1, 9)), scheduled_at: iso(new Date(2030, 0, 1)) }),
      message({ id: "sent", status: "sent", scheduled_at: iso(local(20, 9)) }),
      message({ id: "recurring", status: "cancelled", cron: "0 9 * * *", last_run_at: iso(local(25, 9)), scheduled_at: iso(local(27, 9)) }),
    ];
    expect(sortScheduled(rows).map((row) => row.id)).toEqual(["recurring", "sent", "far"]);
  });
});

describe("launchRowView", () => {
  it("shows a pending recurring launch with its cadence, agent and last run", () => {
    const view = launchRowView(
      launch({
        title: "dependabot sweep",
        cron: "0 2 * * *",
        timezone: "UTC",
        model: "sonnet",
        permission_mode: "auto",
        session_id: "claude_code-last",
        last_run_at: iso(local(24, 2)),
        last_run_status: "launched",
      }),
      catalog,
      now,
      "UTC",
      "Sonnet 5.5",
    );
    expect(view).toMatchObject({
      title: "dependabot sweep",
      countdown: "in 7h 18m",
      state: "Daily at 02:00",
      recurring: true,
      actions: ["openLast", "cancel"],
      resolved: false,
      lastRun: { text: "Last run Tue 02:00, launched", failed: false },
      folder: "/home/me/waypoint",
    });
    expect(view.agent).toBe("Claude Code, Sonnet 5.5, Auto");
  });

  it("shows a launched one-time row and a failed one", () => {
    const launched = launchRowView(
      launch({ status: "launched", scheduled_at: iso(local(26, 7)), session_id: "codex-1" }),
      catalog,
      now,
      "UTC",
      null,
    );
    expect(launched).toMatchObject({ title: "waypoint", state: "Launched today at 07:00", countdown: null, actions: ["open", "dismiss"], resolved: true });
    const failed = launchRowView(launch({ status: "failed", failure_reason: "400: cwd-not-found" }), catalog, now, "UTC", null);
    expect(failed).toMatchObject({ state: "Failed: folder not found", failed: true, actions: ["dismiss"] });
  });
});

describe("messageRowView", () => {
  it("shows a pending timed message with Send now", () => {
    const view = messageRowView(message({}), "Fix held-message race", now, "UTC");
    expect(view).toMatchObject({
      title: "Re-run the e2e suite",
      target: "Fix held-message race",
      state: "Today at 14:41",
      countdown: "in 2h 41m",
      actions: ["sendNow", "cancel"],
    });
  });

  it("shows when-idle, sent and failed messages", () => {
    expect(messageRowView(message({ trigger: "idle" }), null, now, "UTC")).toMatchObject({
      state: "When idle",
      countdown: null,
      target: "session abc123",
    });
    expect(messageRowView(message({ trigger: "idle", status: "sent" }), "t", now, "UTC")).toMatchObject({
      state: "Sent when idle, today at 14:41",
      actions: ["open", "dismiss"],
      resolved: true,
    });
    // A record delivered without a recorded time still holds its queue time.
    expect(
      messageRowView(message({ trigger: "idle", status: "sent", scheduled_at: iso(local(26, 9)) }), "t", now, "UTC").state,
    ).toBe("Sent when idle");
    expect(messageRowView(message({ status: "failed", failure_reason: "session is busy" }), "t", now, "UTC")).toMatchObject({
      state: "Failed: session is busy",
      failed: true,
      actions: ["dismiss"],
    });
    expect(messageRowView(message({ text: "", attachments: ["a"] }), "t", now, "UTC").title).toBe("(no text) (+1 file)");
  });
});
