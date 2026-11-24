import { describe, expect, it } from "vitest";

import { activeNavKey, formatBadgeCount, NAV_DESTINATIONS, showsTabBar } from "./shellNav";

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

describe("showsTabBar", () => {
  it("shows on top-level screens", () => {
    for (const path of ["/", "/inbox", "/board", "/telemetry", "/board/"]) {
      expect(showsTabBar(path)).toBe(true);
    }
  });

  it("hides inside a session, an item and the assistant", () => {
    for (const path of ["/session/a", "/session/a/files", "/inbox/7", "/assistant", "/gallery"]) {
      expect(showsTabBar(path)).toBe(false);
    }
  });
});

describe("formatBadgeCount", () => {
  it("hides zero and caps large counts", () => {
    expect(formatBadgeCount(0)).toBeNull();
    expect(formatBadgeCount(5)).toBe("5");
    expect(formatBadgeCount(100)).toBe("99+");
  });
});
