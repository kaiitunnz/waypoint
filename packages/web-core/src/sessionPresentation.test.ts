import { describe, expect, it } from "vitest";

import {
  agentLabel,
  agentModelLabel,
  brightPreview,
  formatSessionTime,
  isUnread,
  isWorkingStatus,
  lampWord,
  sessionLamp,
  sessionStanding,
  shortModelLabel,
  showsUnread,
  spokenStatus,
} from "./sessionPresentation";
import type { SessionRecord } from "./types";

const session = (overrides: Partial<SessionRecord> = {}): SessionRecord =>
  ({
    id: "s1",
    backend: "claude_code",
    transport: "claude_cli",
    status: "idle",
    last_event_at: "2026-11-25T10:00:00Z",
    read_at: "2026-11-25T10:00:00Z",
    ...overrides,
  }) as SessionRecord;

describe("isUnread", () => {
  it("is unread only while activity is newer than the marker", () => {
    expect(isUnread(session())).toBe(false);
    expect(isUnread(session({ last_event_at: "2026-11-25T10:00:01Z" }))).toBe(true);
    expect(isUnread(session({ read_at: "2026-11-25T11:00:00Z" }))).toBe(false);
  });

  it("reads a missing marker as read", () => {
    expect(isUnread(session({ read_at: undefined }))).toBe(false);
    expect(isUnread(session({ read_at: null, last_event_at: "2030-01-01T00:00:00Z" }))).toBe(false);
  });

  it("compares instants, not strings", () => {
    expect(
      isUnread(session({ last_event_at: "2026-11-25T10:00:00.5+00:00", read_at: "2026-11-25T10:00:00Z" })),
    ).toBe(true);
  });
});

describe("sessionLamp", () => {
  it("maps status to a lamp", () => {
    expect(sessionLamp(session({ status: "running" }), false)).toBe("working");
    expect(sessionLamp(session({ status: "starting" }), false)).toBe("working");
    expect(sessionLamp(session({ status: "error" }), false)).toBe("error");
    expect(sessionLamp(session({ status: "interrupted" }), false)).toBe("error");
    expect(sessionLamp(session({ status: "exited" }), false)).toBe("exited");
    expect(sessionLamp(session(), false)).toBe("idle");
  });

  it("needs you for a pending ask or a session waiting on input", () => {
    expect(sessionLamp(session({ status: "running" }), true)).toBe("needs");
    expect(sessionLamp(session({ status: "waiting_input" }), false)).toBe("needs");
  });

  it("names each lamp", () => {
    expect(lampWord("needs", "waiting_input")).toBe("Needs you");
    expect(lampWord("error", "interrupted")).toBe("Interrupted");
    expect(lampWord("error", "error")).toBe("Error");
    expect(lampWord("idle", "idle")).toBe("Idle");
  });
});

describe("showsUnread", () => {
  const unread = { last_event_at: "2026-11-25T10:05:00Z" };

  it("marks unread activity, except on an exited session", () => {
    expect(showsUnread(session(unread))).toBe(true);
    expect(showsUnread(session({ ...unread, status: "running" }))).toBe(true);
    expect(showsUnread(session({ ...unread, status: "exited" }))).toBe(false);
    expect(showsUnread(session())).toBe(false);
  });
});

describe("spokenStatus", () => {
  it("is spoken after the state's word", () => {
    expect(spokenStatus("Idle", true)).toBe("Idle, unread");
    expect(spokenStatus("Idle", false)).toBe("Idle");
  });
});

describe("brightPreview", () => {
  it("brightens the line of an unread finished turn only", () => {
    const unread = session({ read_at: "2026-01-01T00:00:00Z", last_event_at: "2026-01-01T00:01:00Z" });
    expect(brightPreview(unread, "idle")).toBe(true);
    expect(brightPreview(unread, "working")).toBe(false);
    expect(brightPreview(session(), "idle")).toBe(false);
  });
});

describe("isWorkingStatus", () => {
  it("is true while the agent runs or starts", () => {
    expect(["running", "starting"].map((status) => isWorkingStatus(status as SessionRecord["status"]))).toEqual([true, true]);
    expect(["idle", "waiting_input", "exited"].some((status) => isWorkingStatus(status as SessionRecord["status"]))).toBe(false);
    expect(isWorkingStatus(null)).toBe(false);
    expect(isWorkingStatus(undefined)).toBe(false);
  });
});

describe("sessionStanding", () => {
  it("names the lamp once the socket is open", () => {
    expect(sessionStanding(session({ status: "running" }), false, "open")).toEqual({ lamp: "working", word: "Working" });
    expect(sessionStanding(session({ status: "running" }), true, "open")).toEqual({ lamp: "needs", word: "Needs you" });
  });

  it("names the connection while the socket is not open", () => {
    expect(sessionStanding(session({ status: "running" }), false, "reconnecting")).toEqual({
      lamp: "working",
      word: "Reconnecting",
    });
    expect(sessionStanding(session(), false, "connecting").word).toBe("Connecting");
  });
});

describe("agentModelLabel", () => {
  it("reads the resolved model, a Claude alias or a bare id", () => {
    expect(shortModelLabel(session({ resolved_model: "claude-opus-4-8", model: "opus[1m]" }))).toBe("Opus 4.8");
    expect(shortModelLabel(session({ model: "opus[1m]" }))).toBe("Opus");
    expect(shortModelLabel(session({ model: "google/gemini-3.1-pro-preview" }))).toBe("gemini-3.1-pro-preview");
    expect(shortModelLabel(session({ model: "gpt-6.2-sol" }))).toBe("gpt-6.2-sol");
    expect(shortModelLabel(session())).toBeNull();
  });

  it("names the agent alone", () => {
    expect(agentLabel(session({ model: "sonnet" }))).toBe("Claude Code");
  });

  it("joins the agent and the model", () => {
    expect(agentModelLabel(session({ model: "sonnet" }))).toBe("Claude Code, Sonnet");
    expect(agentModelLabel(session())).toBe("Claude Code");
  });
});

describe("formatSessionTime", () => {
  const now = new Date(2026, 10, 25, 12, 0, 0).getTime();
  const at = (...args: [number, number, number, number, number]) => new Date(...args).toISOString();

  it("reads recent, same-day, yesterday, this-week and older times", () => {
    expect(formatSessionTime(new Date(now - 20_000).toISOString(), now)).toBe("now");
    expect(formatSessionTime(new Date(now - 2 * 60_000).toISOString(), now)).toBe("2m");
    expect(formatSessionTime(at(2026, 10, 25, 8, 40), now)).toBe("08:40");
    expect(formatSessionTime(at(2026, 10, 24, 23, 50), now)).toBe("Yesterday");
    expect(formatSessionTime(at(2026, 10, 23, 9, 0), now)).toBe("Mon");
    expect(formatSessionTime(at(2026, 10, 12, 9, 0), now)).toBe("Nov 12");
    expect(formatSessionTime(at(2025, 11, 31, 9, 0), now)).toBe("Dec 31, 2025");
    expect(formatSessionTime("nope", now)).toBe("");
  });
});
