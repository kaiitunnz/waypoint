import { describe, expect, it } from "vitest";

import { NAV_DESTINATIONS, activeNavKey, formatBadgeCount, isSettingsPath, sessionFilesHref, sessionHref, settingsSectionHref, settingsSectionOf, showsTabBar } from "./shellNav";

describe("activeNavKey", () => {
  it("maps each route to its destination", () => {
    expect(activeNavKey("/")).toBe("sessions");
    expect(activeNavKey("/session/abc")).toBe("sessions");
    expect(activeNavKey("/session/abc/files")).toBe("sessions");
    expect(activeNavKey("/inbox")).toBe("needs-you");
    expect(activeNavKey("/inbox/42")).toBe("needs-you");
    expect(activeNavKey("/assistant")).toBe("assistant");
    expect(activeNavKey("/board")).toBe("board");
    expect(activeNavKey("/telemetry")).toBe("usage");
  });

  it("ignores a trailing slash and unrelated prefixes", () => {
    expect(activeNavKey("/board/")).toBe("board");
    expect(activeNavKey("/boardroom")).toBeNull();
    expect(activeNavKey("/gallery")).toBeNull();
  });

  it("covers every destination's own href", () => {
    for (const destination of NAV_DESTINATIONS) {
      expect(activeNavKey(destination.href)).toBe(destination.key);
    }
  });
});

describe("sessionHref", () => {
  it("encodes the id", () => {
    expect(sessionHref("claude_code-1")).toBe("/session/claude_code-1");
    expect(sessionHref("a/b c")).toBe("/session/a%2Fb%20c");
  });
});

describe("showsTabBar", () => {
  it("shows on top-level screens", () => {
    for (const path of ["/", "/inbox", "/board", "/telemetry", "/board/"]) {
      expect(showsTabBar(path, false)).toBe(true);
    }
  });

  it("hides inside a session, an item and the assistant", () => {
    for (const path of ["/session/a", "/session/a/files", "/inbox/7", "/assistant", "/gallery"]) {
      expect(showsTabBar(path, false)).toBe(false);
    }
  });

  it("stays beside a needs-you item in columns", () => {
    expect(showsTabBar("/inbox/7", true)).toBe(true);
    expect(showsTabBar("/session/a", true)).toBe(false);
    expect(showsTabBar("/assistant", true)).toBe(false);
  });
});

describe("formatBadgeCount", () => {
  it("hides zero and caps large counts", () => {
    expect(formatBadgeCount(0)).toBeNull();
    expect(formatBadgeCount(5)).toBe("5");
    expect(formatBadgeCount(100)).toBe("99+");
  });
});

describe("sessionFilesHref", () => {
  it("links a session's files route with the path encoded", () => {
    expect(sessionFilesHref("a b", "~/src/x y.py")).toBe("/session/a%20b/files?path=~%2Fsrc%2Fx%20y.py");
  });

  it("opens the explorer at its root without a path", () => {
    expect(sessionFilesHref("a b")).toBe("/session/a%20b/files");
  });
});

describe("settings paths", () => {
  it("covers the page and its sections, nothing else", () => {
    expect(isSettingsPath("/settings")).toBe(true);
    expect(isSettingsPath("/settings/")).toBe(true);
    expect(isSettingsPath(settingsSectionHref("session-lists"))).toBe(true);
    expect(isSettingsPath("/settingsx")).toBe(false);
    expect(isSettingsPath("/session/settings")).toBe(false);
  });

  it("reads the section back from its path", () => {
    expect(settingsSectionOf(settingsSectionHref("session lists"))).toBe("session lists");
    expect(settingsSectionOf("/settings/appearance/")).toBe("appearance");
    expect(settingsSectionOf("/settings")).toBeUndefined();
    expect(settingsSectionOf("/")).toBeUndefined();
  });

  it("keeps the page outside the navigation and the tab bar", () => {
    expect(activeNavKey("/settings")).toBeNull();
    expect(showsTabBar("/settings", false)).toBe(false);
  });
});
